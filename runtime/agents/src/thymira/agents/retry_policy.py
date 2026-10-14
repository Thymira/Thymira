"""Task-scoped stops for repeated Python preflight and publication failures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior

from thymira.agents.task_completion import (
    ToolAttemptEvidence,
    assess_task_completion,
    declared_outputs_for_attempt,
    same_tool_work,
    tool_attempts_for_task,
)
from thymira.schemas import EventType
from thymira.tools.models import ToolExecutionError
from thymira.tools.publication_progress import MAX_PYTHON_PUBLICATION_OUTPUTS
from thymira.tools.tool_preflight import validate_tool_request

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.schemas import Event
    from thymira.state import ArtifactStore
    from thymira.tools import ToolContext, ToolRegistry

MAX_IDENTICAL_INVALID_PYTHON_FAILURES = 2
MAX_PUBLICATION_FAILURES_WITHOUT_PROGRESS = 2
_PUBLICATION_IDENTITY_PARTS = 2
_SOURCE_SHA256_HEX_LENGTH = 64


class RepeatedInvalidPythonSourceError(UnexpectedModelBehavior):
    """A task repeated the same structured Python syntax failure twice."""

    def __init__(self, reason: str | None = None) -> None:
        super().__init__(
            reason
            or (
                "the task repeated the same syntactically invalid source twice for the same "
                "destination and structured diagnostic. Correct the source before trying again; "
                "the task stopped before requesting another model response."
            )
        )


class RepeatedPythonPublicationError(UnexpectedModelBehavior):
    """A task repeated the same failed output publication without enough progress."""

    def __init__(self) -> None:
        super().__init__(
            "run_python failed to publish the same declared outputs twice without reducing the "
            "known missing set. The task stopped before requesting another model response."
        )


@dataclass(frozen=True, slots=True)
class RetryStop:
    """A task-scoped retry boundary reconstructed from terminal tool evidence."""

    kind: Literal["syntax", "publication"]
    reason: str
    terminal_seq: int


def retry_stop_for_task(
    events: Sequence[Event],
    task_id: str,
    *,
    artifact_store: ArtifactStore | None,
    tool_registry: ToolRegistry | None,
    tool_context: ToolContext | None,
    current_call_id: str | None = None,
) -> RetryStop | None:
    """Find an exhausted retry group from task-owned events, never mutable message history."""
    attempts = tool_attempts_for_task(events, task_id)
    current = next((item for item in attempts if item.call_id == current_call_id), None)
    if not any(
        not attempt.succeeded and attempt.tool in {"run_python", "write_file"}
        for attempt in attempts
    ) or (current_call_id is not None and current is None):
        return None

    unresolved_ids = {
        attempt.call_id
        for attempt in assess_task_completion(events, task_id, artifact_store).unresolved_failures
    }
    unresolved = tuple(item for item in attempts if item.call_id in unresolved_ids)
    started_ids = {
        event.payload.get("tool_call_id")
        for event in events
        if event.type is EventType.TOOL_STARTED
        and event.payload.get("task_id") == task_id
        and isinstance(event.payload.get("tool_call_id"), str)
    }

    syntax_stops = _syntax_stops(
        unresolved,
        started_ids,
        tool_registry=tool_registry,
        tool_context=tool_context,
    )
    if current is not None:
        current_syntax = next(
            (stop for attempt, stop in syntax_stops if attempt.call_id == current.call_id), None
        )
        if current_syntax is not None:
            return current_syntax
    elif syntax_stops:
        return max((stop for _attempt, stop in syntax_stops), key=lambda stop: stop.terminal_seq)

    publication_stops = _publication_stops(unresolved)
    if current is not None:
        current_publication = next(
            (
                stop
                for attempts_in_group, stop in publication_stops
                if any(item.call_id == current.call_id for item in attempts_in_group)
            ),
            None,
        )
        if current_publication is not None:
            return current_publication
    elif publication_stops:
        return max(
            (stop for _attempts_in_group, stop in publication_stops),
            key=lambda stop: stop.terminal_seq,
        )
    return None


def retry_stop_reason_for_task(
    events: Sequence[Event],
    task_id: str,
    artifact_store: ArtifactStore | None,
    tool_registry: ToolRegistry | None,
    tool_context: ToolContext | None,
) -> str | None:
    """Return only the runner-facing reason from task-scoped retry evidence."""
    stop = retry_stop_for_task(
        events,
        task_id,
        artifact_store=artifact_store,
        tool_registry=tool_registry,
        tool_context=tool_context,
    )
    return stop.reason if stop is not None else None


def _syntax_stops(
    attempts: Sequence[ToolAttemptEvidence],
    started_ids: set[object],
    *,
    tool_registry: ToolRegistry | None,
    tool_context: ToolContext | None,
) -> tuple[tuple[ToolAttemptEvidence, RetryStop], ...]:
    """Group preflight failures by exact source, declared destination, and SyntaxError fields."""
    if tool_registry is None or tool_context is None:
        return ()
    identities: list[tuple[ToolAttemptEvidence, tuple[object, ...]]] = []
    for attempt in attempts:
        if attempt.call_id in started_ids or attempt.terminal.payload.get("exit_code") is not None:
            continue
        identity = _syntax_failure_identity(attempt, tool_registry, tool_context)
        if identity is not None:
            identities.append((attempt, identity))
    stops: list[tuple[ToolAttemptEvidence, RetryStop]] = []
    for attempt, identity in identities:
        count = sum(candidate == identity for _item, candidate in identities)
        if count >= MAX_IDENTICAL_INVALID_PYTHON_FAILURES:
            stops.append(
                (
                    attempt,
                    RetryStop(
                        kind="syntax",
                        reason=(
                            "run_python rejected the same syntactically invalid source twice in "
                            "this delegation. Correct the source before trying again; the "
                            "delegation stopped before requesting another model response."
                            if attempt.tool == "run_python"
                            else str(RepeatedInvalidPythonSourceError())
                        ),
                        terminal_seq=attempt.terminal.seq,
                    ),
                )
            )
    return tuple(stops)


def _syntax_failure_identity(
    attempt: ToolAttemptEvidence,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
) -> tuple[object, ...] | None:
    """Re-run the exact argument validator and require its structured SyntaxError cause."""
    if (
        attempt.tool not in {"run_python", "write_file"}
        or attempt.arguments is None
        or not isinstance(attempt.error, str)
    ):
        return None
    try:
        tool = tool_registry.get(attempt.tool)
        validate_tool_request(tool, attempt.arguments, invocation=tool_context.for_tool())
    except (ToolExecutionError, KeyError, OSError, ValueError) as exc:
        if str(exc) != attempt.error:
            return None
        syntax_error = _syntax_error_in_chain(exc)
    else:
        return None
    if syntax_error is None:
        return None
    source_identity = _source_identity(attempt)
    destinations = declared_outputs_for_attempt(attempt)
    if destinations is None and attempt.tool == "run_python":
        manifest_path = attempt.arguments.get("output_manifest_path")
        if isinstance(manifest_path, str) and manifest_path:
            # Syntax preflight happens before execution, so identify the explicit destination
            # without opening or executing a manifest that does not exist yet.
            destinations = ((manifest_path.replace("\\", "/"), "manifest"),)
    if source_identity is None or destinations is None:
        return None
    diagnostic = (
        syntax_error.msg,
        syntax_error.lineno,
        syntax_error.offset,
        syntax_error.end_lineno,
        syntax_error.end_offset,
    )
    return (attempt.tool, source_identity, destinations, diagnostic)


def _source_identity(attempt: ToolAttemptEvidence) -> str | None:
    """Identify exact inline bytes or an immutable registered source revision."""
    arguments = attempt.arguments
    if arguments is None:
        return None
    if attempt.tool == "write_file":
        content = arguments.get("content")
        return (
            "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
            if isinstance(content, str)
            else None
        )
    code = arguments.get("code")
    if isinstance(code, str):
        return "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()
    artifact_id = arguments.get("source_artifact_id")
    return "artifact:" + artifact_id if isinstance(artifact_id, str) and artifact_id else None


def _syntax_error_in_chain(error: BaseException) -> SyntaxError | None:
    """Find a validator-authored SyntaxError without using its human-readable message."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, SyntaxError):
            return current
        if isinstance(current, ValidationError):
            errors = current.errors(include_context=True, include_input=False, include_url=False)
            for item in errors:
                nested = item.get("ctx", {}).get("error")
                if isinstance(nested, BaseException):
                    pending.append(nested)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def _publication_stops(
    attempts: Sequence[ToolAttemptEvidence],
) -> tuple[tuple[tuple[ToolAttemptEvidence, ...], RetryStop], ...]:
    """Apply one-correction bands, resetting only on a strict monotonic missing-set reduction."""
    failures = tuple(item for item in attempts if _is_publication_failure(item))
    stops: list[tuple[tuple[ToolAttemptEvidence, ...], RetryStop]] = []
    for anchor in failures:
        declared = declared_outputs_for_attempt(anchor)
        if not declared:
            continue
        group = tuple(
            item
            for item in failures
            if item.terminal.seq >= anchor.terminal.seq
            and _same_publication_retry_work(anchor, item)
        )
        if not group or group[0].call_id != anchor.call_id:
            continue
        best_missing = set(declared)
        failures_in_band = 0
        exhausted: ToolAttemptEvidence | None = None
        for attempt in group:
            attempt_outputs = declared_outputs_for_attempt(attempt)
            progress = (
                _publication_progress(attempt, attempt_outputs)
                if attempt_outputs is not None and set(attempt_outputs) == set(declared)
                else None
            )
            failures_in_band += 1
            if progress is not None:
                missing, _present = progress
                anchor_obligations = set(declared)
                remaining = missing.intersection(anchor_obligations)
                if remaining < best_missing:
                    best_missing = remaining
                    failures_in_band = 1
            if failures_in_band >= MAX_PUBLICATION_FAILURES_WITHOUT_PROGRESS:
                exhausted = attempt
                break
        if exhausted is not None:
            stops.append(
                (
                    group,
                    RetryStop(
                        kind="publication",
                        reason=str(RepeatedPythonPublicationError()),
                        terminal_seq=exhausted.terminal.seq,
                    ),
                )
            )
    return tuple(stops)


def _same_publication_retry_work(left: ToolAttemptEvidence, right: ToolAttemptEvidence) -> bool:
    """Group fresh-manifest retries by exact registered source, not changing output subsets."""
    if same_tool_work(left, right):
        return True
    left_arguments = left.arguments
    right_arguments = right.arguments
    if (
        left.tool != "run_python"
        or right.tool != "run_python"
        or left_arguments is None
        or right_arguments is None
        or not left_arguments.get("output_manifest_path")
        or not right_arguments.get("output_manifest_path")
    ):
        return False
    left_source = _source_identity(left)
    right_source = _source_identity(right)
    return (
        left_source is not None and left_source == right_source
    ) or _same_registered_manifest_source_path(left, right)


def _same_registered_manifest_source_path(
    left: ToolAttemptEvidence, right: ToolAttemptEvidence
) -> bool:
    """Group revisions only when this task/run uses the same registered source path."""
    left_arguments = left.arguments
    right_arguments = right.arguments
    if (
        left.task_id != right.task_id
        or not left.task_id
        or left.terminal.run_id != right.terminal.run_id
        or left_arguments is None
        or right_arguments is None
        or not isinstance(left_arguments.get("output_manifest_path"), str)
        or not isinstance(right_arguments.get("output_manifest_path"), str)
        or not left_arguments.get("output_manifest_path")
        or not right_arguments.get("output_manifest_path")
    ):
        return False
    source_names = (
        left_arguments.get("source_artifact_name"),
        right_arguments.get("source_artifact_name"),
    )
    source_ids = (
        left_arguments.get("source_artifact_id"),
        right_arguments.get("source_artifact_id"),
    )
    source_hashes = (
        left_arguments.get("source_artifact_sha256"),
        right_arguments.get("source_artifact_sha256"),
    )
    return (
        isinstance(source_names[0], str)
        and bool(source_names[0])
        and source_names[0] == source_names[1]
        and all(isinstance(item, str) and item for item in source_ids)
        and all(_is_sha256(item) for item in source_hashes)
    )


def _is_sha256(value: object) -> bool:
    """Check manager-recorded source hashes before grouping registered revisions."""
    return (
        isinstance(value, str)
        and len(value) == _SOURCE_SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_publication_failure(attempt: ToolAttemptEvidence) -> bool:
    """Recognize a failed run_python call after a zero-exit interpreter with fixed outputs."""
    outputs = declared_outputs_for_attempt(attempt)
    value = attempt.value
    failure_value = value.get("value") if value is not None else None
    failure_envelope = (
        value is not None
        and value.get("kind") == "failure"
        and isinstance(failure_value, dict)
        and isinstance(failure_value.get("error"), str)
        and bool(failure_value["error"])
    )
    return (
        attempt.tool == "run_python"
        and not attempt.succeeded
        and attempt.terminal.payload.get("exit_code") == 0
        and bool(outputs)
        and failure_envelope
    )


def _publication_progress(
    attempt: ToolAttemptEvidence,
    declared: tuple[tuple[str, str | None], ...] | None,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]] | None:
    """Read only a valid manager-recorded path/kind partition matching this call's declarations."""
    raw = attempt.terminal.payload.get("publication_progress")
    if declared is None or not isinstance(raw, dict) or set(raw) != {"missing", "present"}:
        return None
    if not isinstance(raw.get("missing"), list) or not isinstance(raw.get("present"), list):
        return None
    if len(raw["missing"]) + len(raw["present"]) > MAX_PYTHON_PUBLICATION_OUTPUTS:
        return None

    def identities(items: list[object]) -> set[tuple[str, str]] | None:
        parsed: list[tuple[str, str]] = []
        for item in items:
            if (
                not isinstance(item, list)
                or len(item) != _PUBLICATION_IDENTITY_PARTS
                or not isinstance(item[0], str)
                or not item[0]
                or not isinstance(item[1], str)
            ):
                return None
            parsed.append((item[0].replace("\\", "/"), item[1]))
        result = set(parsed)
        return result if len(result) == len(parsed) else None

    missing = identities(raw["missing"])
    present = identities(raw["present"])
    if missing is None or present is None or not missing or missing.intersection(present):
        return None
    expected = {(path, kind) for path, kind in declared if kind is not None}
    if len(expected) != len(declared) or missing.union(present) != expected:
        return None
    return missing, present


__all__ = [
    "MAX_IDENTICAL_INVALID_PYTHON_FAILURES",
    "MAX_PUBLICATION_FAILURES_WITHOUT_PROGRESS",
    "RepeatedInvalidPythonSourceError",
    "RepeatedPythonPublicationError",
    "RetryStop",
    "retry_stop_for_task",
    "retry_stop_reason_for_task",
]
