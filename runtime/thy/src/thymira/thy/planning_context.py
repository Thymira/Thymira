"""Contextual planning evidence, request framing and proposal compilation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic_ai.exceptions import ModelRetry, UnexpectedModelBehavior

from thymira.agents.prompt_framing import frame_untrusted
from thymira.schemas import ModelWorkIntent, resolve_model_work_intent
from thymira.thy.evaluation_plan import normalize_evaluation_plan, prior_model_catalog
from thymira.thy.models import PlanOutput
from thymira.thy.nodes.plan_scope import normalize_model_scope

if TYPE_CHECKING:
    from pydantic_ai import Agent as PydanticAgent
    from pydantic_ai import RunContext

    from thymira.agents import AgentCatalog
    from thymira.thy.models import ThyState


def contextual_proposal_feedback(
    output: PlanOutput,
    *,
    context_run_ids: tuple[str, ...],
) -> str | None:
    """Return bounded correction feedback for legacy outputs or unlinked references."""
    rejected_items: list[dict[str, str]] = []
    for task in output.tasks:
        if task.required_artifacts:
            rejected_items.append(
                {
                    "task_id": task.id,
                    "task_instruction": task.instruction,
                    "legacy_required_artifacts": json.dumps(
                        task.required_artifacts,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "reason": (
                        "contextual tasks must declare promised files in outputs_to_publish; "
                        "required_artifacts is runtime-owned"
                    ),
                }
            )
        rejected_items.extend(
            {
                "task_id": task.id,
                "task_instruction": task.instruction,
                "reference_name": reference.name,
                "source_run_id": reference.source_run_id,
                "reason": "source_run_id is not one of the linked prior runs",
            }
            for reference in getattr(task, "artifact_references", ())
            if reference.source_run_id not in context_run_ids
        )
    if not rejected_items:
        return None
    rejected = frame_untrusted(
        json.dumps(rejected_items, ensure_ascii=False, sort_keys=True),
        label="rejected-plan-proposal",
        max_chars=8000,
    )
    return (
        "Revise the rejected candidate below and return the complete corrected plan. For every "
        "file the current request and corrected task instruction promise to publish, put its exact "
        "path, kind, and any metric contract in outputs_to_publish. Preserve every such output; "
        "do not omit one to pass this check. A historical-only file is a reference, not a new "
        "output. Do not send required_artifacts: the runtime compiles outputs_to_publish into that "
        "field before policy review. Keep prior-run files in artifact_references with a linked "
        "source_run_id; registered dataset names are logical inputs, not artifact references. "
        "Rejected proposal data follows, framed as untrusted data: "
        f"{rejected}"
    )


def compile_plan_output(output: PlanOutput) -> PlanOutput:
    """Compile declared outputs and prior-run references into the runtime AgentTask contract."""
    proposal = output.model_dump(mode="json")
    for task in proposal["tasks"]:
        outputs = task.pop("outputs_to_publish", None)
        references = task.pop("artifact_references", ())
        if outputs is not None:
            if task["required_artifacts"]:
                raise ValueError(
                    "contextual required_artifacts must be corrected to outputs_to_publish"
                )
            task["required_artifacts"] = outputs
        if outputs:
            output_lines = "\n".join(_publication_line(requirement) for requirement in outputs)
            task["instruction"] += "\n\nFiles this task must publish:\n" + output_lines
        if references:
            reference_lines = "\n".join(
                f"- run_id: {item['source_run_id']}; path: {item['name']}" for item in references
            )
            task["instruction"] += (
                "\n\nPrior-run artifacts supplied as references only. Their presence in the "
                "current workspace has not been verified; do not recreate or copy them "
                "automatically. Use normal tools and policy checks to access them if available."
                f"\n{reference_lines}"
            )
    return PlanOutput.model_validate(proposal)


def _publication_line(requirement: dict[str, object]) -> str:
    details = [f"kind={requirement['kind']}"]
    if requirement.get("match") == "content":
        details.append(f"metric_keys={requirement['metric_keys']}")
    return f"- {requirement['name']} ({', '.join(details)})"


def planning_request(
    state: ThyState, *, contextual: bool
) -> tuple[str, str, tuple[str, ...], ModelWorkIntent]:
    """Frame current work and historical references without changing their evidence roles."""
    conversation = state.run.conversation
    clarification_context = conversation.clarification_context if conversation else ()
    context_run_ids = conversation.context_run_ids if conversation else ()
    constraints = state.execution_constraints
    constraint_note = (
        f"\nAuthorised execution constraints: {constraints.model_dump(mode='json')}"
        if constraints is not None
        else ""
    )
    registered = ", ".join(
        f"{d.name} (target: {d.target})" if d.target else d.name for d in state.datasets
    )
    dataset_note = (
        "\n"
        + frame_untrusted(
            f"Registered datasets: {registered}",
            label="thy-dataset-catalog",
        )
        if registered
        else ""
    )
    context_note = (
        "\n"
        + frame_untrusted(
            f"Project context (.thymira/context.md):\n{state.project_context.strip()}",
            label="thy-project-context",
        )
        if state.project_context
        else ""
    )
    model_note = "\n" + frame_untrusted(
        prior_model_catalog(state.run), label="verified-prior-model-catalog", max_chars=12000
    )
    current_message = (
        state.run.conversation.message if state.run.conversation is not None else state.run.prompt
    )
    human_intent = resolve_model_work_intent(
        current_message,
        clarification_context,
        dataset_names=tuple(dataset.name for dataset in state.datasets),
    )
    model_note += f"\nHuman-resolved model work intent: {human_intent.value}"
    if conversation is None:
        request_note = f"Current user request (authoritative):\n{current_message}"
        history_note = ""
    else:
        clarifications = "\n\n".join(clarification_context) or "(none)"
        request_note = (
            "Current user message (authoritative):\n"
            f"{current_message}\n\n"
            "Earlier user clarifications for this request (authoritative):\n"
            f"{clarifications}\n\n"
            "Combined requested work:\n"
            f"{conversation.requested_work}"
        )
        history_note = "\n" + frame_untrusted(
            "Full Run prompt and prior conversation context. Reference only; do not infer "
            "new output obligations from this history:\n" + state.run.prompt,
            label="thy-historical-conversation-reference",
            max_chars=12000,
        )
    if contextual:
        request_note += (
            "\n\nFor contextual tasks, put every file the task promises to publish in "
            "outputs_to_publish with its exact workspace-relative path and artifact kind. "
            "For metrics, include match=content and the requested metric_keys. Do not provide "
            "required_artifacts; the runtime compiles outputs_to_publish into that execution "
            "contract before policy review. Keep prior-run files in artifact_references with "
            "their linked source_run_id; references are context only and never outputs to create."
        )
        if context_run_ids:
            request_note += "\nLinked prior run IDs: " + ", ".join(context_run_ids)
    prompt = (
        f"Plan only for this request:\n{request_note}{history_note}{constraint_note}"
        f"{dataset_note}{context_note}{model_note}"
    )
    return prompt, current_message, clarification_context, human_intent


def plan_for_request(
    agent: PydanticAgent[None, PlanOutput],
    state: ThyState,
    catalog: AgentCatalog,
    *,
    contextual: bool,
) -> tuple[PlanOutput | None, str | None]:
    """Run the bounded contextual proposal check before canonical model-work normalization."""
    prompt, current_message, clarification_context, human_intent = planning_request(
        state, contextual=contextual
    )
    conversation = state.run.conversation
    context_run_ids = conversation.context_run_ids if conversation else ()

    @agent.output_validator
    def _validate_contextual_proposal(
        run_context: RunContext[None], output: PlanOutput
    ) -> PlanOutput:
        del run_context
        if not contextual:
            return output
        feedback = contextual_proposal_feedback(
            output,
            context_run_ids=context_run_ids,
        )
        if feedback:
            raise ModelRetry(feedback)
        return output

    try:
        result = agent.run_sync(prompt)
    except UnexpectedModelBehavior as exc:
        cause = str(exc.__cause__ or exc)
        if contextual and any(
            field in cause
            for field in ("required_artifacts", "outputs_to_publish", "artifact_references")
        ):
            return None, (
                "plan rejected: contextual outputs or references remained invalid "
                "after one correction"
            )
        raise
    canonical_output = compile_plan_output(result.output)
    if human_intent is ModelWorkIntent.CLARIFY:
        planned, planning_error = (
            None,
            (
                "Model work is ambiguous. Clarify whether to train a new model or evaluate "
                "an existing one; no model work was started."
            ),
        )
    elif human_intent is ModelWorkIntent.EVALUATE_EXISTING:
        try:
            planned = normalize_evaluation_plan(state.run, canonical_output, catalog)
            planning_error = None
        except ValueError as exc:
            planned, planning_error = None, str(exc)
    else:
        planned, planning_error = normalize_model_scope(
            current_message,
            canonical_output,
            catalog,
            clarification_context=clarification_context,
            dataset_names=tuple(dataset.name for dataset in state.datasets),
        )
    return planned, planning_error
