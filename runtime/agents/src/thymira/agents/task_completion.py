"""Task-scoped runtime tool settlement and its reconstructible completion evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from thymira.schemas import Artifact, ArtifactKind, Event, EventType, Task, ToolCallStatus
from thymira.tools.builtins.python_arguments import RunPythonOutputArtifact
from thymira.tools.publication_progress import validated_python_output_declarations
from thymira.tools.results import ModelEvaluationValue

_SHA256_HEX_LENGTH = 64

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from pydantic import BaseModel

    from thymira.state import ArtifactStore


@dataclass(frozen=True, slots=True)
class ToolAttemptEvidence:
    """One task-scoped terminal tool event joined to its authoritative call arguments."""

    task_id: str
    call_id: str | None
    tool: str
    arguments: dict[str, object] | None
    terminal: Event
    succeeded: bool
    error: str | None
    artifact_ids: tuple[str, ...]
    value: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class TaskCompletionEvidence:
    """Runtime-owned facts used to decide, record, and reconstruct one completed task."""

    attempts: tuple[ToolAttemptEvidence, ...] = ()
    unresolved_failures: tuple[ToolAttemptEvidence, ...] = ()
    repaired_tool_call_ids: tuple[str, ...] = ()
    ignored_tool_call_ids: tuple[str, ...] = ()
    verified_artifact_ids: tuple[str, ...] = ()
    validation_failures: tuple[str, ...] = ()
    missing_required_artifacts: tuple[str, ...] = ()
    plan_task_id: str | None = None

    @property
    def error(self) -> str | None:
        """Render unresolved runtime failures before task-owned validation failures."""
        messages = [
            f"{attempt.tool}: {attempt.error or 'tool call failed'}"
            for attempt in self.unresolved_failures
        ]
        messages.extend(self.validation_failures)
        return "; ".join(messages) if messages else None


def successful_evaluation_artifact_id(attempt: ToolAttemptEvidence) -> str | None:
    """Read the artifact id only from a canonical, schema-valid evaluator success envelope."""
    envelope = attempt.value
    if envelope is None or set(envelope) != {"kind", "value"} or envelope.get("kind") != "success":
        return None
    value = envelope.get("value")
    if not isinstance(value, dict):
        return None
    try:
        result = ModelEvaluationValue.model_validate(value)
    except ValidationError:
        return None
    return result.artifact_id


class TaskCompletionValidator(Protocol):
    """Add code-owned task requirements to the runner's runtime call settlement."""

    def __call__(
        self,
        _task: Task,
        output: BaseModel,
        _events: Sequence[Event],
        evidence: TaskCompletionEvidence,
    ) -> TaskCompletionEvidence:
        """Add task-plan checks after task-scoped runtime settlement."""
        ...


def tool_attempts_for_task(
    events: Sequence[Event], task_id: str
) -> tuple[ToolAttemptEvidence, ...]:
    """Join every task-owned tool terminal to its exact started call and validated arguments."""
    starts: dict[tuple[str, str, str], Event] = {}
    for event in events:
        payload = event.payload
        call_id = payload.get("tool_call_id")
        owner_task = payload.get("task_id")
        if (
            event.type is EventType.TOOL_STARTED
            and isinstance(call_id, str)
            and isinstance(owner_task, str)
        ):
            starts[(event.run_id, owner_task, call_id)] = event

    attempts: list[ToolAttemptEvidence] = []
    for event in events:
        if event.type not in {EventType.TOOL_COMPLETED, EventType.TOOL_DENIED}:
            continue
        payload = event.payload
        owner_task = payload.get("task_id")
        if owner_task != task_id:
            continue
        raw_call_id = payload.get("tool_call_id")
        call_id = raw_call_id if isinstance(raw_call_id, str) and raw_call_id else event.subject_id
        start = (
            starts.get((event.run_id, task_id, call_id))
            if isinstance(call_id, str) and call_id
            else None
        )
        if (
            event.type is EventType.TOOL_DENIED
            and payload.get("ticket_outcome") == "pending"
            and _same_approved_continuation(event, starts.values(), task_id)
        ):
            # The bridge assigns a fresh manager call ID when PydanticAI replays a parked call.
            # The task, one-shot decision and canonical intent digest still identify that exact
            # continuation; a fresh identical request has a fresh decision and remains separate.
            continue
        arguments = (start.payload.get("arguments") if start is not None else None) or payload.get(
            "arguments"
        )
        tool = payload.get("tool")
        status = payload.get("status")
        status_value = status.value if isinstance(status, ToolCallStatus) else status
        exit_code = payload.get("exit_code")
        error_value = payload.get("error") or payload.get("reason")
        succeeded = (
            event.type is EventType.TOOL_COMPLETED
            and status_value == ToolCallStatus.COMPLETED.value
            and not (isinstance(exit_code, int) and exit_code != 0)
            and not (isinstance(error_value, str) and error_value)
        )
        raw_ids = payload.get("artifact_ids")
        artifact_ids = (
            tuple(value for value in raw_ids if isinstance(value, str))
            if isinstance(raw_ids, list)
            else ()
        )
        value = payload.get("value")
        attempts.append(
            ToolAttemptEvidence(
                task_id=task_id,
                call_id=call_id if isinstance(call_id, str) and call_id else None,
                tool=tool if isinstance(tool, str) else "unknown tool",
                arguments=arguments if isinstance(arguments, dict) else None,
                terminal=event,
                succeeded=succeeded,
                error=error_value if isinstance(error_value, str) else None,
                artifact_ids=artifact_ids,
                value=value if isinstance(value, dict) else None,
            )
        )
    return tuple(attempts)


def _same_approved_continuation(pending: Event, starts: Iterable[Event], task_id: str) -> bool:
    """Match a parked denial to only the later execution using its consumed approval ticket."""
    payload = pending.payload
    decision_id = payload.get("decision_id")
    intent = payload.get("tool_intent_sha256")
    for started in starts:
        started_payload = started.payload
        if (
            started.run_id == pending.run_id
            and started.type is EventType.TOOL_STARTED
            and started.seq > pending.seq
            and started_payload.get("task_id") == task_id
            and isinstance(decision_id, str)
            and decision_id
            and started_payload.get("decision_id") == decision_id
            and isinstance(intent, str)
            and intent
            and started_payload.get("tool_intent_sha256") == intent
        ):
            return True
    return False


def assess_task_completion(
    events: Sequence[Event], task_id: str, artifact_store: ArtifactStore | None
) -> TaskCompletionEvidence:
    """Resolve only failed calls repaired by the same declared work and verified publication."""
    attempts = tool_attempts_for_task(events, task_id)
    active_by_id: dict[str, Artifact] = {}
    store_verified = False
    if artifact_store is not None:
        try:
            if not artifact_store.verify():
                active_by_id = {
                    artifact.id: artifact
                    for artifact in artifact_store.list_active()
                    if artifact.valid
                }
                store_verified = True
        except (KeyError, OSError, ValueError):
            active_by_id = {}

    unresolved: list[ToolAttemptEvidence] = []
    repaired: list[str] = []
    verified_ids: list[str] = []
    for failed in attempts:
        if failed.succeeded:
            continue
        repair = next(
            (
                attempt
                for attempt in attempts
                if attempt.succeeded
                and attempt.terminal.seq > failed.terminal.seq
                and failed.call_id is not None
                and same_tool_work(failed, attempt)
                and _repair_publications(failed, attempt, active_by_id, store_verified)
            ),
            None,
        )
        if repair is None:
            unresolved.append(failed)
        else:
            repaired.append(failed.call_id or "")
            verified_ids.extend(_published_ids(repair, active_by_id))

    return TaskCompletionEvidence(
        attempts=attempts,
        unresolved_failures=tuple(unresolved),
        repaired_tool_call_ids=tuple(dict.fromkeys(repaired)),
        verified_artifact_ids=tuple(dict.fromkeys(verified_ids)),
    )


def same_tool_work(  # noqa: PLR0911  # fail-closed branches keep repair identity explicit
    failed: ToolAttemptEvidence, successful: ToolAttemptEvidence
) -> bool:
    """Compare repair by declared outputs, logical source path, or evaluator input contract."""
    failed_args = failed.arguments
    successful_args = successful.arguments
    if failed_args is None or successful_args is None:
        return False
    if failed.tool == "evaluate_model" or successful.tool == "evaluate_model":
        return (
            failed.tool == successful.tool == "evaluate_model"
            and all(
                failed_args.get(key) == successful_args.get(key)
                for key in (
                    "model_artifact",
                    "dataset",
                    "target_column",
                    "split",
                    "evaluation_context_artifact",
                )
            )
            and all(
                isinstance(failed_args.get(key), str) and failed_args.get(key)
                for key in ("model_artifact", "dataset", "target_column", "split")
            )
        )

    failed_outputs = declared_outputs_for_attempt(failed)
    successful_outputs = declared_outputs_for_attempt(successful)
    if failed_outputs is None or successful_outputs is None:
        return False
    if failed_outputs:
        if not set(failed_outputs).issubset(successful_outputs):
            return False
        return _same_source_path(failed_args, successful_args) or (
            _same_registered_manifest_output_work(failed, successful, failed_outputs)
        )

    failed_source = failed_args.get("source_artifact_name")
    successful_source = successful_args.get("source_artifact_name")
    if failed_source or successful_source:
        return (
            failed.tool == successful.tool == "run_python"
            and isinstance(failed_source, str)
            and isinstance(successful_source, str)
            and failed_source == successful_source
        )
    return False


def _same_registered_manifest_output_work(
    failed: ToolAttemptEvidence,
    successful: ToolAttemptEvidence,
    failed_outputs: tuple[tuple[str, str | None], ...],
) -> bool:
    """Match distinct registered sources only for complete dynamic output-set coverage."""
    failed_args = failed.arguments
    successful_args = successful.arguments
    if (
        failed.tool != "run_python"
        or successful.tool != "run_python"
        or failed.task_id != successful.task_id
        or not failed.task_id
        or failed.terminal.run_id != successful.terminal.run_id
        or failed_args is None
        or successful_args is None
        or not isinstance(failed_args.get("output_manifest_path"), str)
        or not isinstance(successful_args.get("output_manifest_path"), str)
        or not failed_args.get("output_manifest_path")
        or not successful_args.get("output_manifest_path")
    ):
        return False
    source_fields = (
        "source_artifact_id",
        "source_artifact_name",
        "source_artifact_sha256",
    )
    failed_source = tuple(failed_args.get(field) for field in source_fields)
    successful_source = tuple(successful_args.get(field) for field in source_fields)
    if (
        not all(isinstance(value, str) and value for value in failed_source)
        or not all(isinstance(value, str) and value for value in successful_source)
        or failed_source[0] == successful_source[0]
        or failed_source[1] == successful_source[1]
        or not _is_sha256(failed_source[2])
        or not _is_sha256(successful_source[2])
    ):
        return False
    successful_outputs = declared_outputs_for_attempt(successful)
    return (
        successful_outputs is not None
        and bool(failed_outputs)
        and set(failed_outputs).issubset(successful_outputs)
    )


def _is_sha256(value: object) -> bool:
    """Check manager-recorded source digests without importing the broader event helpers."""
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _same_source_path(left: dict[str, object], right: dict[str, object]) -> bool:
    """Require equal logical source names when either call names a registered source."""
    left_name = left.get("source_artifact_name")
    right_name = right.get("source_artifact_name")
    left_id = left.get("source_artifact_id")
    right_id = right.get("source_artifact_id")
    if (left_id and not isinstance(left_name, str)) or (
        right_id and not isinstance(right_name, str)
    ):
        return False
    if left_name or right_name:
        return isinstance(left_name, str) and left_name == right_name
    return True


def declared_outputs_for_attempt(  # noqa: PLR0911  # malformed evidence always remains unknown
    attempt: ToolAttemptEvidence,
) -> tuple[tuple[str, str | None], ...] | None:
    """Read output paths from this call's validated declarations, never its final prose."""
    arguments = attempt.arguments
    if arguments is None:
        return None
    if attempt.tool == "run_python" and arguments.get("output_manifest_path"):
        manifest_path = arguments.get("output_manifest_path")
        if not isinstance(manifest_path, str):
            return None
        raw = attempt.terminal.payload.get("output_declarations")
        try:
            declarations = (
                validated_python_output_declarations(
                    tuple(_output_descriptor_from_payload(item) for item in raw),
                    manifest_path=manifest_path,
                )
                if isinstance(raw, list)
                else None
            )
        except (TypeError, ValueError):
            return None
        if declarations is None:
            return None
        return tuple((item.path, item.kind.value) for item in declarations)
    raw_outputs = arguments.get("output_artifacts")
    if raw_outputs is None:
        return _write_file_output(arguments) if attempt.tool == "write_file" else ()
    if not isinstance(raw_outputs, (list, tuple)):
        return None
    outputs: list[tuple[str, str | None]] = []
    for item in raw_outputs:
        raw_kind = item.get("kind") if isinstance(item, dict) else None
        kind = raw_kind.value if isinstance(raw_kind, ArtifactKind) else raw_kind
        if kind is None and attempt.tool == "run_python":
            # RunPythonOutputArtifact defaults an omitted kind to ArtifactKind.OTHER.
            kind = ArtifactKind.OTHER.value
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or (kind is not None and not isinstance(kind, str))
        ):
            return None
        outputs.append((item["path"].replace("\\", "/"), kind))
    return tuple(outputs)


def _output_descriptor_from_payload(item: object) -> RunPythonOutputArtifact:
    """Keep event JSON validation inside the shared descriptor validator."""
    if not isinstance(item, dict):
        raise TypeError("output declaration is not an object")
    return RunPythonOutputArtifact.model_validate(item)


def _write_file_output(arguments: dict[str, object]) -> tuple[tuple[str, str | None], ...] | None:
    """Read the single output declaration from one validated write_file call."""
    path = arguments.get("path")
    kind = arguments.get("kind")
    if isinstance(kind, ArtifactKind):
        kind = kind.value
    if isinstance(path, str) and (kind is None or isinstance(kind, str)):
        return ((path.replace("\\", "/"), kind),)
    return None


def _repair_publications(
    failed: ToolAttemptEvidence,
    successful: ToolAttemptEvidence,
    active_by_id: dict[str, Artifact],
    store_verified: bool,
) -> bool:
    """Require a real, active publication for declared outputs before resolving a failure."""
    if not store_verified:
        return False
    if failed.tool == "evaluate_model":
        return _evaluation_repair_publication(successful, active_by_id)
    outputs = declared_outputs_for_attempt(failed)
    if outputs is None:
        return False
    if not outputs:
        # Registered-source execution has no output contract; the same logical source path is the
        # work identity and a successful terminal execution is its repair evidence.
        return bool(failed.arguments and failed.arguments.get("source_artifact_name"))
    return _declared_publications_match(outputs, successful, active_by_id)


def _evaluation_repair_publication(
    successful: ToolAttemptEvidence, active_by_id: dict[str, Artifact]
) -> bool:
    """Verify a successful evaluator's authoritative metrics artifact publication."""
    artifact_id = successful_evaluation_artifact_id(successful)
    artifact = active_by_id.get(artifact_id) if isinstance(artifact_id, str) else None
    return (
        artifact is not None
        and artifact.id in successful.artifact_ids
        and artifact.kind is ArtifactKind.METRICS
    )


def _declared_publications_match(
    outputs: tuple[tuple[str, str | None], ...],
    successful: ToolAttemptEvidence,
    active_by_id: dict[str, Artifact],
) -> bool:
    """Verify that each declared output was published by this successful call."""
    published = _published_ids(successful, active_by_id)
    if len(published) < len(outputs):
        return False
    for name, kind in outputs:
        artifact = next(
            (
                active_by_id[identity]
                for identity in published
                if active_by_id[identity].name == name
            ),
            None,
        )
        if artifact is None or (kind is not None and artifact.kind.value != kind):
            return False
    return True


def _published_ids(
    successful: ToolAttemptEvidence, active_by_id: dict[str, Artifact]
) -> tuple[str, ...]:
    """Return the successful terminal's artifact IDs that remain active and hash-verified."""
    return tuple(identity for identity in successful.artifact_ids if identity in active_by_id)


def completion_authority_payload(evidence: TaskCompletionEvidence) -> dict[str, object]:
    """Serialize the runtime evidence that supports a completed or failed task."""
    return {
        "plan_task_id": evidence.plan_task_id,
        "repaired_tool_call_ids": list(evidence.repaired_tool_call_ids),
        "ignored_tool_call_ids": list(evidence.ignored_tool_call_ids),
        "unresolved_tool_call_ids": [
            attempt.call_id for attempt in evidence.unresolved_failures if attempt.call_id
        ],
        "verified_artifact_ids": list(evidence.verified_artifact_ids),
        "validation_failures": list(evidence.validation_failures),
        "missing_required_artifacts": list(evidence.missing_required_artifacts),
    }
