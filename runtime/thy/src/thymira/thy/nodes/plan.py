"""Plan node: THY's own plan, gated for approval before Execute (THY-11).

Unlike Execute (THY-09), Plan is never a delegated sub-agent call: it is THY's own reasoning
(`Role.THY`, `task='plan'`, FRONTIER tier by the routing table), built directly on `routed_model`
rather than `AgentRunner`/`Delegator` — THY has no `AgentSpec`, so there is nothing for a
spec-driven runner to run here.

`gate.check_action(action_type='plan.proposed')` is the seam ADR-0004 calls for: THY's plan always
passes through the Policy Engine before Execute. The decision is trusted exactly as
`thymira.policies.allows_execution` trusts it: `PASS`/`WARNING` proceed; `REQUIRE_HUMAN_REVIEW` is
**not** satisfied by the `Gate`'s synchronous approver -- its answer is recorded as a
`human.approval` event (evidence), never as authority (`product-final.md` corrections C-9/C-15) --
so, under the default policy (no rule for `plan.proposed`, hence `REQUIRE_HUMAN_REVIEW`), the graph
halts at Plan until `HITL-01` provides a checkable `Approval`. A policy whose `ActionRule` passes
`plan.proposed` lets the plan advance. A seeded `state.plan` (a Core resume carrying a
`ThyProgress`) skips the model and the Gate entirely: it was already gated when it was first
proposed, and asking either again could plan the waiting call away or mint a second decision
for it.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from types import GenericAlias
from typing import TYPE_CHECKING, Any

from pydantic import Field, create_model
from pydantic_ai import Agent as PydanticAgent

from thymira.agents.llm.routing import Role
from thymira.agents.model_binding import routed_model
from thymira.observability import agent as agent_observation
from thymira.observability import guardrail
from thymira.policies import allows_execution
from thymira.schemas import Decision, EventType, ExecutionAction
from thymira.thy.models import PlanOutput, ThyAgentKind, ThyPlanReview
from thymira.thy.plan_review import (
    plan_candidate_sha256,
    plan_candidate_snapshot,
    plan_review_scope,
    plan_review_scope_sha256,
)
from thymira.thy.planning_context import plan_for_request
from thymira.thy.planning_contract import (
    ContextualPlanningTask,
    PlanningTask,
)
from thymira.tools.profile_paths import profile_output_guidance

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.agents import AgentCatalog, RuntimeSkillCatalog
    from thymira.agents.llm.base import LLMProvider, LLMResponse
    from thymira.agents.llm.routing import ModelChoice
    from thymira.agents.request_ledger import RequestLedger
    from thymira.events import EventLog
    from thymira.policies import Gate
    from thymira.schemas import ModelRoutePolicy
    from thymira.thy.models import ThyState

_AGENT_DESCRIPTIONS: dict[str, str] = {
    "data": "inspect and profile the dataset (shape, dtypes, missing values, target candidates).",
    "coding": "write and run analysis or transformation code.",
    "experiment": "train a new model as a single tracked experiment.",
    "evaluation": "evaluate an existing registered model without training using evaluate_model.",
    "statistics-agent": "run and interpret an inferential or descriptive statistical test.",
    "data-quality-agent": "flag leakage, imbalance, drift and sensitive attributes.",
    "visualization-agent": "produce a captioned plot artifact.",
    "ml-agent": (
        "train a model with iterative hyperparameter search across multiple trials, escalating"
        " to a tuning helper -- use only when the request explicitly asks for tuning/search, not"
        " for a single baseline run."
    ),
}

"""One line per `ThyAgentKind` value THY's Plan model may ever propose. Filtered against the
actual catalog by `_plan_instructions` -- see its docstring for why a static prompt was wrong."""


def _plannable_agent_names(catalog: AgentCatalog) -> tuple[str, ...]:
    """Return the enum-backed agents this exact catalog can actually dispatch."""
    registered = frozenset(catalog.names())
    return tuple(kind.value for kind in ThyAgentKind if kind.value in registered)


def _catalog_agent_kind_type(agent_names: tuple[str, ...]) -> type[StrEnum]:
    """Create the string enum used by one catalog-scoped planning schema."""
    enum_members = {ThyAgentKind(name).name: name for name in agent_names}
    return StrEnum("_CatalogThyAgentKind", enum_members)


def _catalog_skill_name_type(skill_names: tuple[str, ...]) -> type[StrEnum]:
    """Create a collision-free string enum for one runtime skill catalog.

    Runtime skill names may contain ``-`` and ``.`` and therefore are not safe Python member
    names. Positional member keys keep the exact catalog values without lossy normalisation or
    collisions between otherwise similar names.
    """
    enum_members = {f"SKILL_{index}": name for index, name in enumerate(skill_names)}
    return StrEnum("_CatalogRuntimeSkillName", enum_members)


@lru_cache(maxsize=32)
def _catalog_plan_output_type(
    agent_names: tuple[str, ...],
    skill_names: tuple[str, ...] = (),
    *,
    contextual: bool = False,
) -> type[PlanOutput]:
    """Build a cached ``PlanOutput`` subtype matching both executable catalogs.

    ``PlanOutput`` deliberately carries every globally supported ``ThyAgentKind`` so callers can
    opt into the full specialist roster. A particular Run may use a narrower catalog, however.
    Giving that Run the global JSON Schema contradicts its prompt and lets a model select an agent
    Execute cannot resolve. Runtime skill names have the same boundary: a free-form name passes
    policy but later fails while prompt construction resolves the catalog. The derived schema
    closes both gaps before the Gate; conversion back to canonical ``PlanOutput`` happens
    immediately after validation.
    """
    if not agent_names:
        raise ValueError("the THY catalog has no enum-backed agents available for planning")
    catalog_kind = _catalog_agent_kind_type(agent_names)
    if skill_names:
        catalog_skill = _catalog_skill_name_type(skill_names)
        skill_names_annotation = GenericAlias(tuple, (catalog_skill, Ellipsis))
        skill_names_field: object = ()
    else:
        skill_names_annotation = GenericAlias(tuple, (str, Ellipsis))
        skill_names_field = Field(default=(), max_length=0)
    task_base = ContextualPlanningTask if contextual else PlanningTask
    catalog_task = create_model(
        "_CatalogAgentTask",
        __base__=task_base,
        agent=(catalog_kind, ...),
        skill_names=(skill_names_annotation, skill_names_field),
    )
    # The annotation is deliberately runtime-built from the dynamic task subtype. Pydantic needs
    # the concrete GenericAlias here so the JSON Schema nests the restricted agent enum.
    catalog_tasks_annotation = GenericAlias(tuple, (catalog_task, Ellipsis))
    return create_model(
        "_CatalogPlanOutput",
        __base__=PlanOutput,
        tasks=(catalog_tasks_annotation, Field(min_length=1)),
    )


def _plan_instructions(
    catalog: AgentCatalog,
    runtime_skill_catalog: RuntimeSkillCatalog | None = None,
    *,
    contextual: bool = False,
) -> str:
    """Build Plan instructions from the same roster that constrains its structured schema.

    A static list of all six `ThyAgentKind` agents drifted from `default_thy_catalog()`'s
    deliberate three-agent MVP scope (`test_default_thy_catalog_carries_the_three_thy_agent_
    kinds`). Filtering the prose fixed only half of that contract: the global `PlanOutput` JSON
    Schema still offered unavailable values. `_plannable_agent_names` now feeds both this prompt
    and `_catalog_plan_output_type`, so prose and machine-readable choices cannot drift again.
    """
    lines = [f"- {name}: {_AGENT_DESCRIPTIONS[name]}" for name in _plannable_agent_names(catalog)]
    output_guidance = (
        "Put each file this task promises to publish in outputs_to_publish with its exact "
        "workspace-relative path and artifact kind. Use the path named by the user when one was "
        "requested; otherwise choose a clear path and state the same path in the task instruction. "
        "Keep outputs_to_publish empty when the task promises no file. Do not provide "
        "required_artifacts; the runtime compiles outputs_to_publish into the execution contract "
        "before policy review. Historical artifacts belong in artifact_references with a linked "
        "source_run_id; registered dataset names are logical inputs, not artifact references."
        if contextual
        else "When the user requests named output files, put each exact workspace-relative path "
        "in that task's required_artifacts, including its artifact kind when known. These are "
        "acceptance criteria, not suggestions; do not invent paths. Leave required_artifacts "
        "empty when no file output was requested."
    )
    preamble = (
        "You are THY, planning a data-science run. Produce an ordered, non-empty list of "
        "AgentTask items, each naming the specialist agent, the ThyGraph phase it runs in "
        "(always 'execute'), and a concrete instruction. Choose each agent for the work it "
        "owns, and only when the run needs that work. For a basic descriptive EDA with "
        "distributions and numeric correlations, use the Data agent and instruct it to call "
        "profile_dataset(include_plots=true). That tool publishes the profile and PNG charts; "
        "do not add a Coding task just to recreate those charts. Use Coding for custom plots "
        "or analysis beyond the deterministic profile. "
        f"{profile_output_guidance() + ' ' if contextual else ''}"
        f"{output_guidance} kind='report' is only for a file MIRA's audit can read as a report -- "
        "Markdown, "
        "plain text, PDF, CSV or JSON; a '.ipynb' notebook is always kind='code', produced by "
        "run_notebook, never kind='report':"
    )
    trailer = (
        "Name only these agents, spelled exactly as listed. For metrics of an existing model "
        "set work_kind=evaluate_existing. Never train a replacement to answer that request. "
        "Use the verified prior model catalog; source_model_id is its exact model_artifact_id. "
        "For explicit new training set work_kind=train_new. "
        "A metrics deliverable must have kind=metrics and list requested metric_keys. "
        "Only metrics may use match=content; every other artifact uses match=exact. "
        "For metrics set match=content "
        "for a planner-chosen "
        "filename; keep match=exact only for a path explicitly required by the user. "
        "Accuracy alone cannot satisfy precision, recall, F1 or ROC-AUC."
    )
    runtime_skill_names = runtime_skill_catalog.names() if runtime_skill_catalog is not None else ()
    if runtime_skill_catalog is not None and runtime_skill_names:
        trailer = (
            f"{trailer}\nFor a task that needs a runtime skill, set skill_names to one or more "
            "exact names from the catalog; leave it empty when no runtime skill is needed. "
            "Runtime skill bodies are loaded by Execute only after these names are selected.\n"
            f"{runtime_skill_catalog.index_text()}"
        )
    else:
        trailer = f"{trailer}\nNo runtime skills are available; leave every skill_names empty."
    return f"{preamble}\n" + "\n".join(lines) + f"\n{trailer}"


def plan_node(
    catalog: AgentCatalog,
    gate: Gate,
    event_log: EventLog,
    *,
    provider: LLMProvider | None = None,
    before_model_request: Callable[[], Sequence[str]] | None = None,
    before_model_selection: Callable[[], None] | None = None,
    before_model_call: Callable[[ModelChoice], None] | None = None,
    record_model_usage: Callable[[LLMResponse], None] | None = None,
    route_policy: ModelRoutePolicy | None = None,
    runtime_skill_catalog: RuntimeSkillCatalog | None = None,
    request_ledger: RequestLedger | None = None,
) -> Callable[..., dict[str, Any]]:
    """Build the Plan node bound to `gate`.

    A `BLOCK` decision records the reason on `state.error`; `REQUIRE_HUMAN_REVIEW` preserves a
    separate pending candidate. Both leave `state.plan` empty and halt before Execute. An allowed
    decision populates `state.plan` and advances normally. Either way, the
    decision is appended to `state.policy_signals`. A non-empty `state.plan` (a Core resume)
    short-circuits before any of that: it advances straight to Execute without calling the model
    or the Gate again.
    """

    def _plan(state: ThyState) -> dict[str, Any]:
        existing = _existing_plan_result(state)
        if existing is not None:
            return existing
        plannable_agent_names = _plannable_agent_names(catalog)
        if not plannable_agent_names:
            return state.model_copy(
                update={"error": "plan rejected: no registered THY agents are available"}
            ).model_dump()
        plannable_skill_names = (
            runtime_skill_catalog.names() if runtime_skill_catalog is not None else ()
        )
        conversation = state.run.conversation
        clarification_context = conversation.clarification_context if conversation else ()
        context_run_ids = conversation.context_run_ids if conversation else ()
        contextual = conversation is not None and (
            conversation.ordinal > 1 or bool(context_run_ids) or bool(clarification_context)
        )
        plan_output_type = _catalog_plan_output_type(
            plannable_agent_names,
            plannable_skill_names,
            contextual=contextual,
        )
        model = routed_model(
            Role.THY,
            "plan",
            event_log,
            provider=provider,
            output_schema=plan_output_type,
            before_model_request=before_model_request,
            before_model_selection=before_model_selection,
            before_model_call=before_model_call,
            record_model_usage=record_model_usage,
            route_policy=route_policy,
            request_ledger=request_ledger,
            request_owner_id=f"{state.run.id}:thy-plan",
        )
        agent: PydanticAgent[None, PlanOutput] = PydanticAgent(
            model=model,
            output_type=plan_output_type,
            instructions=_plan_instructions(
                catalog,
                runtime_skill_catalog,
                contextual=contextual,
            ),
            retries=1,
        )
        with agent_observation(name="thy-plan", objective=state.run.prompt) as observation:
            planned, planning_error = plan_for_request(agent, state, catalog, contextual=contextual)
            observation.update(
                output={"task_count": len(planned.tasks) if planned is not None else 0}
            )

        if planning_error is not None:
            return state.model_copy(update={"error": planning_error}).model_dump()
        if planned is None:  # pragma: no cover - guarded by planning_error above
            raise AssertionError("model plan normalisation returned no plan without an error")

        with guardrail("policy-plan-proposed") as decision_observation:
            candidate_snapshot = plan_candidate_snapshot(planned)
            scope = plan_review_scope(
                state.run,
                state.execution_decision,
                state.execution_constraints,
                policy_sha256=gate.engine.policy_sha256,
            )
            candidate_sha256 = plan_candidate_sha256(planned)
            scope_sha256 = plan_review_scope_sha256(scope)
            decision = gate.check_action(
                subject_kind="run",
                subject_id=state.run.id,
                action_type="plan.proposed",
                payload={"task_count": len(planned.tasks)},
                summary=_plan_review_summary(planned),
                details={
                    "action_type": "plan.proposed",
                    "plan": candidate_snapshot,
                    "plan_review": {
                        "version": 1,
                        "candidate_sha256": candidate_sha256,
                        "scope_sha256": scope_sha256,
                        "scope": scope,
                    },
                },
            )
            decision_observation.update(output={"decision": decision.decision.value})
        policy_signals = (*state.policy_signals, decision)
        if decision.decision is Decision.REQUIRE_HUMAN_REVIEW:
            request_event = next(
                (
                    event
                    for event in reversed(event_log.events())
                    if event.type is EventType.HUMAN_APPROVAL_REQUESTED
                    and event.payload.get("decision_id") == decision.id
                ),
                None,
            )
            if request_event is None or request_event.hash is None:
                raise RuntimeError("plan review Gate did not persist its approval request")
            pending = ThyPlanReview(
                candidate=planned,
                decision=decision,
                request_event_id=request_event.event_id,
                request_event_hash=request_event.hash,
                candidate_sha256=candidate_sha256,
                scope_sha256=scope_sha256,
                scope=scope,
            )
            return state.model_copy(
                update={"pending_plan_review": pending, "policy_signals": policy_signals}
            ).model_dump()
        if not allows_execution(decision):
            return state.model_copy(
                update={
                    "error": f"plan rejected: {decision.reason}",
                    "policy_signals": policy_signals,
                }
            ).model_dump()

        next_state = state.advance()
        return next_state.model_copy(
            update={"plan": planned.tasks, "policy_signals": policy_signals}
        ).model_dump()

    return _plan


def _existing_plan_result(state: ThyState) -> dict[str, Any] | None:
    """Preserve pending/seeded continuations and refuse prohibited planning before model calls."""
    if state.pending_plan_review is not None:
        if state.plan:
            return state.model_copy(
                update={"error": "pending plan review cannot be an executable seeded plan"}
            ).model_dump()
        return state.model_dump()
    if state.plan:
        # The candidate was authorized before seeding. Replanning or re-gating would replace it.
        return state.advance().model_dump()
    if (
        state.execution_constraints is not None
        and ExecutionAction.PLAN in state.execution_constraints.prohibited_actions
    ):
        return state.model_copy(
            update={"error": "plan rejected: execution constraints prohibit plan.proposed"}
        ).model_dump()
    return None


def _plan_review_summary(plan: PlanOutput) -> str:
    """Describe the candidate's actual tasks and promised outputs for the approval surface."""
    tasks = []
    for task in plan.tasks:
        outputs = ", ".join(item.name for item in task.required_artifacts) or "no named files"
        tasks.append(f"{task.id}: {task.instruction} (outputs: {outputs})")
    return "Review the proposed plan: " + "; ".join(tasks)


__all__ = ["plan_node"]
