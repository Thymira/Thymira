"""Pure projections for agent-step identity and lifecycle event evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thymira.events import canonical_json, sha256_text

if TYPE_CHECKING:
    from pydantic_ai.usage import RunUsage

    from thymira.agents.prompts import AssembledPrompt
    from thymira.agents.resume import PendingResume
    from thymira.agents.settlement import DelegationIdentity
    from thymira.agents.spec import AgentSpec
    from thymira.agents.usage import RunUsage as SharedRunUsage
    from thymira.schemas import Task


def _step_key(spec: AgentSpec, task: Task, assembled: AssembledPrompt) -> str:
    """Hash the stable inputs that identify one isolated agent step."""
    return sha256_text(
        canonical_json(
            {
                "spec": spec.model_dump(mode="json"),
                "task_id": task.id,
                "objective": task.objective,
                "prompt": assembled.model_dump(mode="json"),
            }
        )
    )


def _usage_payload(
    run_usage: RunUsage, shared: SharedRunUsage | None, cost_before: float | None
) -> dict[str, Any]:
    """This step's tokens and cost, for the `agent.completed` event (THY-30).

    Tokens come from the run's own aggregated `RunUsage` (PydanticAI sums every request's
    `RequestUsage`, so a multi-turn step reports the whole step). Cost is the *delta* of the
    shared run budget across this step: cost lives only on the real `LLMResponse` and is
    accumulated into `ctx.usage` by `routed_model`, never on any pre-call event, so the honest
    per-agent figure is what the budget grew by while this step ran. With no shared accumulator
    (an unmetered run) cost is unknown and reported as `0.0`. The fold in `thymira.cli` sums these
    per agent for its per-agent token/cost line; the totals match the run's usage exactly.

    A budget total of `None` means a model call reported no price
    (`thymira.agents.usage.RunUsage.add_cost`), so the delta is unmeasurable and the payload says
    `null` rather than claiming the step cost nothing. Only the *metered* path can say that: the
    unmetered `shared is None` case keeps reporting `0.0`, because there no budget was ever asked
    for and no ceiling depends on the answer.
    """
    cost: float | None
    if shared is None:
        cost = 0.0
    elif shared.cost_usd is None or cost_before is None:
        cost = None
    else:
        cost = shared.cost_usd - cost_before
    return {
        "input_tokens": int(run_usage.input_tokens or 0),
        "output_tokens": int(run_usage.output_tokens or 0),
        "cost_usd": cost,
    }


def _delegation_payload(delegation: DelegationIdentity | None) -> dict[str, Any]:
    """The three fields that mark a lifecycle event as belonging to a delegation, or nothing."""
    if delegation is None:
        return {}
    return {
        "parent_agent": delegation.parent_agent,
        "delegation_key": delegation.delegation_key,
        "delegation_depth": delegation.delegation_depth,
    }


def _resume_terminal_payload(resume: PendingResume | None) -> dict[str, Any]:
    """Mark a terminal event as the close of its original parked execution identity."""
    if resume is None:
        return {}
    return {
        "resumed": True,
        "execution_identity_sha256": resume.identity.digest(),
    }
