"""Bind task settlements to plan-declared, task-owned artifact deliveries."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thymira.agents.task_completion import (
    declared_outputs_for_attempt,
    successful_evaluation_artifact_id,
)
from thymira.schemas import Artifact, ArtifactKind
from thymira.thy.agents.coding import CodeResult
from thymira.thy.agents.evaluation import EvaluationResult
from thymira.thy.artifact_delivery import resolve_requirement
from thymira.thy.models import AgentTask, ThyAgentKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

    from thymira.agents.task_completion import (
        TaskCompletionEvidence,
        TaskCompletionValidator,
        ToolAttemptEvidence,
    )
    from thymira.schemas import Event, Task
    from thymira.state import ArtifactStore


def task_completion_validator(
    agent_task: AgentTask, store: ArtifactStore | None
) -> TaskCompletionValidator:
    """Create the code-owned completion contract reused on task resume."""

    def validate(
        _task: Task,
        output: BaseModel,
        _events: Sequence[Event],
        evidence: TaskCompletionEvidence,
    ) -> TaskCompletionEvidence:
        failures = list(evidence.validation_failures)
        active = _active_task_artifacts(evidence, store)
        preferred_metrics_id = (
            output.metrics_artifact_id if isinstance(output, EvaluationResult) else None
        )
        resolved, missing, requirement_failures = _resolve_requirements(
            agent_task, active, store, preferred_metrics_id
        )
        failures.extend(requirement_failures)
        referenced_ids, reference_failures = _validate_code_result(output, active)
        failures.extend(reference_failures)
        metrics_id, evaluation_failure = _validate_evaluation_result(
            agent_task, output, evidence, active, resolved
        )
        if evaluation_failure is not None:
            failures.append(evaluation_failure)
        settlement = _settle_tool_failures(agent_task, evidence, active, resolved, missing)
        still_unresolved, repaired, ignored, repaired_ids = settlement
        verified_ids = [
            *evidence.verified_artifact_ids,
            *(item.id for _, item in resolved),
            *referenced_ids,
            *repaired_ids,
        ]
        if metrics_id is not None:
            verified_ids.append(metrics_id)

        return replace(
            evidence,
            unresolved_failures=tuple(still_unresolved),
            repaired_tool_call_ids=tuple(dict.fromkeys(repaired)),
            ignored_tool_call_ids=tuple(dict.fromkeys(ignored)),
            verified_artifact_ids=tuple(dict.fromkeys(verified_ids)),
            validation_failures=tuple(failures),
            missing_required_artifacts=tuple(missing),
            plan_task_id=agent_task.id,
        )

    return validate


def _resolve_requirements(
    agent_task: AgentTask,
    active: list[Artifact],
    store: ArtifactStore | None,
    preferred_metrics_id: str | None,
) -> tuple[list[tuple[str, Artifact]], list[str], list[str]]:
    """Resolve every plan deliverable through THY's canonical contract validator."""
    resolved: list[tuple[str, Artifact]] = []
    missing: list[str] = []
    failures: list[str] = []
    used_ids: set[str] = set()
    preferred_metrics = [artifact for artifact in active if artifact.id == preferred_metrics_id]
    candidates = (
        preferred_metrics
        if agent_task.agent is ThyAgentKind.EVALUATION and preferred_metrics
        else active
    )
    for requirement in agent_task.required_artifacts:
        artifact = resolve_requirement(requirement, candidates, used_ids, store)
        if artifact is None:
            missing.append(requirement.name)
            failures.append(f"required artifact {requirement.name!r} is missing or invalid")
        else:
            resolved.append((requirement.name, artifact))
            used_ids.add(artifact.id)
    return resolved, missing, failures


def _validate_code_result(output: BaseModel, active: list[Artifact]) -> tuple[list[str], list[str]]:
    """Validate every model-authored artifact reference against task-owned active artifacts."""
    if not isinstance(output, CodeResult):
        return [], []
    used_ids: list[str] = []
    failures: list[str] = []
    for reference in output.artifacts:
        matches = [artifact for artifact in active if reference in {artifact.id, artifact.name}]
        if len(matches) != 1:
            failures.append(
                f"final CodeResult artifact reference {reference!r} is not task-owned, "
                "active and hash-verified"
            )
        else:
            used_ids.append(matches[0].id)
    return used_ids, failures


def _validate_evaluation_result(
    agent_task: AgentTask,
    output: BaseModel,
    evidence: TaskCompletionEvidence,
    active: list[Artifact],
    resolved: list[tuple[str, Artifact]],
) -> tuple[str | None, str | None]:
    """Bind the final evaluation metrics id to the normalized plan and successful call."""
    if agent_task.agent is not ThyAgentKind.EVALUATION:
        return None, None
    if not agent_task.required_artifacts:
        return None, "evaluation task has no normalized metric requirement"
    if not isinstance(output, EvaluationResult) or not output.metrics_artifact_id:
        return None, "evaluation result is missing its metrics artifact id"
    metrics_id = output.metrics_artifact_id
    producers = [
        attempt
        for attempt in evidence.attempts
        if attempt.succeeded
        and attempt.tool == "evaluate_model"
        and successful_evaluation_artifact_id(attempt) == metrics_id
        and metrics_id in attempt.artifact_ids
    ]
    artifact = next((item for item in active if item.id == metrics_id), None)
    resolved_ids = {item.id for _, item in resolved}
    if (
        len(producers) != 1
        or artifact is None
        or artifact.kind is not ArtifactKind.METRICS
        or metrics_id not in resolved_ids
    ):
        return None, (
            "evaluation metrics id is not a successful task-owned evaluate_model publication"
        )
    return metrics_id, None


def _settle_tool_failures(
    agent_task: AgentTask,
    evidence: TaskCompletionEvidence,
    active: list[Artifact],
    resolved: list[tuple[str, Artifact]],
    missing: list[str],
) -> tuple[list[ToolAttemptEvidence], list[str], list[str], list[str]]:
    """Repair matching required outputs or explicitly ignore bounded unrelated exploration."""
    repaired = list(evidence.repaired_tool_call_ids)
    ignored = list(evidence.ignored_tool_call_ids)
    remaining: list[ToolAttemptEvidence] = []
    repaired_artifact_ids: list[str] = []
    requirements_complete = bool(agent_task.required_artifacts) and not missing
    for failed in evidence.unresolved_failures:
        repaired_ids = _repaired_required_outputs(failed, resolved, evidence, active, missing)
        if repaired_ids:
            if failed.call_id:
                repaired.append(failed.call_id)
            repaired_artifact_ids.extend(repaired_ids)
        elif requirements_complete and _unrelated_explicit_output(failed, agent_task, resolved):
            if failed.call_id:
                ignored.append(failed.call_id)
        else:
            remaining.append(failed)
    return remaining, repaired, ignored, repaired_artifact_ids


def _active_task_artifacts(
    evidence: TaskCompletionEvidence, store: ArtifactStore | None
) -> list[Artifact]:
    """Restrict requirement candidates to active, intact IDs on successful calls of this task."""
    if store is None:
        return []
    try:
        if store.verify():
            return []
        active_by_id = {artifact.id: artifact for artifact in store.list_active() if artifact.valid}
    except (KeyError, OSError, ValueError):
        return []
    published_ids = {
        artifact_id
        for attempt in evidence.attempts
        if attempt.succeeded
        for artifact_id in attempt.artifact_ids
    }
    return [active_by_id[item] for item in published_ids if item in active_by_id]


def _repaired_required_outputs(
    failed: ToolAttemptEvidence,
    resolved: list[tuple[str, Artifact]],
    evidence: TaskCompletionEvidence,
    active: list[Artifact],
    missing: list[str],
) -> tuple[str, ...]:
    """Return exact requirement publications made after a failed call, if any."""
    arguments = failed.arguments
    failed_seq = failed.terminal.seq
    tool = failed.tool
    if not isinstance(arguments, dict) or tool not in {"write_file", "run_python"}:
        return ()
    output_declarations = declared_outputs_for_attempt(failed)
    untyped_paths = {path for path, kind in output_declarations or () if kind is None}
    if not output_declarations or (untyped_paths and tool != "write_file"):
        return ()
    declared = {(path, kind) for path, kind in output_declarations if kind is not None}
    paths = {path for path, _kind in output_declarations}
    if set(missing).intersection(paths):
        return ()
    matching = [
        artifact
        for requirement_name, artifact in resolved
        if (requirement_name, artifact.kind.value) in declared
        or (tool == "write_file" and requirement_name in untyped_paths)
    ]
    if not matching:
        return ()
    later_publications = {
        artifact_id
        for success in evidence.attempts
        if (
            success.succeeded
            and success.task_id == failed.task_id
            and success.terminal.run_id == failed.terminal.run_id
            and success.terminal.seq > failed_seq
        )
        for artifact_id in success.artifact_ids
    }
    active_by_id = {artifact.id: artifact for artifact in active}
    verified_later = {
        (artifact.name, artifact.kind.value)
        for artifact_id in later_publications
        if (artifact := active_by_id.get(artifact_id)) is not None
    }
    verified_later_paths = {path for path, _kind in verified_later}
    if not all(
        (path in verified_later_paths if kind is None else (path, kind) in verified_later)
        for path, kind in output_declarations
    ):
        return ()
    return tuple(
        artifact.id
        for artifact in matching
        if artifact.id in later_publications and artifact.id in active_by_id
    )


def _unrelated_explicit_output(
    failed: ToolAttemptEvidence,
    agent_task: AgentTask,
    resolved: list[tuple[str, Artifact]],
) -> bool:
    """Allow bounded exploration only for explicit, unrelated output paths under a plan."""
    arguments = failed.arguments
    tool = failed.tool
    if (
        agent_task.agent is ThyAgentKind.EVALUATION
        or not isinstance(arguments, dict)
        or any(
            key in arguments
            for key in ("model_artifact", "evaluation_context_artifact", "source_model_artifact_id")
        )
        or tool not in {"write_file", "run_python"}
    ):
        return False
    paths = _declared_output_paths(failed)
    required_names = {name for name, _artifact in resolved}
    return bool(paths) and not required_names.intersection(paths)


def _declared_output_paths(failed: ToolAttemptEvidence) -> set[str] | None:
    """Read runtime-validated declarations shared with generic task settlement."""
    outputs = declared_outputs_for_attempt(failed)
    return {path for path, _kind in outputs} if outputs is not None and outputs else None
