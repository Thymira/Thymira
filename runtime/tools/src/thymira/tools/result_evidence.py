"""Normalize tool-controlled result fields before recording them."""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from thymira.schemas import SandboxEnforcement, SandboxMode
from thymira.tools.builtins.python_arguments import RunPythonOutputArtifact
from thymira.tools.builtins.python_output_manifest import load_output_manifest
from thymira.tools.models import ToolExecutionError, ToolResultCode
from thymira.tools.publication_progress import (
    validated_python_output_declarations,
    validated_python_publication_progress,
)
from thymira.tools.results import (
    ToolResultValidationError,
    canonical_result,
)
from thymira.tools.sandbox.base import ResolvedExecutionSpec, WorkspaceQuotaEvidence
from thymira.tools.sandbox.termination import TerminationEvidence

if TYPE_CHECKING:
    from thymira.tools.models import Tool, ToolCall, ToolInvocation, ToolResult

_SHA256 = re.compile(r"[0-9a-f]{64}")

_TERMINATION_TEXT_KEYS = ("outcome", "exit_source", "control_channel", "reap", "tree_scope")
_TERMINATION_FLAG_KEYS = (
    "control_channel_validated",
    "deadline_exceeded",
    "timed_out",
    "signalled_after_reap",
)
_TERMINATION_NUMBER_KEYS = ("deadline_s", "duration_s", "reap_deadline_s", "reap_duration_s")
_QUOTA_DIGEST_KEYS = ("source_tree_sha256", "final_tree_sha256", "exported_tree_sha256")
_QUOTA_NUMBER_KEYS = (
    "requested_bytes",
    "observed_capacity_bytes",
    "inode_limit",
    "observed_inode_capacity",
    "source_logical_bytes",
    "final_logical_bytes",
    "exported_logical_bytes",
    "source_entries",
    "final_entries",
    "exported_entries",
)


def _recordable_quota(evidence: WorkspaceQuotaEvidence) -> dict[str, Any] | None:
    """Return quota evidence only when every field has a canonical, bounded shape."""
    payload = evidence.as_payload()
    if (
        payload.get("mechanism") != "docker_local_tmpfs_volume"
        or any(
            isinstance(payload[key], bool) or not isinstance(payload[key], int) or payload[key] <= 0
            for key in ("requested_bytes", "inode_limit")
        )
        or any(
            payload[key] is not None
            and (
                isinstance(payload[key], bool)
                or not isinstance(payload[key], int)
                or payload[key] < 0
            )
            for key in _QUOTA_NUMBER_KEYS[1:]
        )
        or any(
            payload[key] is not None and not isinstance(payload[key], str)
            for key in ("observed_filesystem", *_QUOTA_DIGEST_KEYS)
        )
        or any(
            payload[key] is not None and not _SHA256.fullmatch(payload[key])
            for key in _QUOTA_DIGEST_KEYS
        )
        or not all(
            isinstance(payload[key], bool) for key in ("volume_inspected", "worker_mount_inspected")
        )
        or not isinstance(payload["publication"], str)
        or not isinstance(payload["cleanup"], dict)
        or not all(isinstance(value, bool) for value in payload["cleanup"].values())
    ):
        return None
    return payload


def _recordable_termination(evidence: TerminationEvidence) -> dict[str, Any] | None:
    """Return a canonicalizable termination payload, or ``None`` when any field is unusable.

    ``TerminationEvidence`` is a plain dataclass a tool could have built by hand, and the log's
    canonicalizer runs after ``tool.started`` -- a value it cannot serialize would raise inside
    the very gap the manager exists to close. The payload is checked here instead, where a
    rejected value becomes recorded evidence: a partly-readable record is dropped whole rather
    than written half-true, and MIRA reads the absence as missing evidence and fails closed.
    """
    payload = evidence.as_payload()
    exit_code = payload["child_exit_code"]
    probe = payload["probe"]
    if (
        any(not isinstance(payload[key], str) for key in _TERMINATION_TEXT_KEYS)
        or any(not isinstance(payload[key], bool) for key in _TERMINATION_FLAG_KEYS)
        or any(
            payload[key] is not None and not isinstance(payload[key], float)
            for key in _TERMINATION_NUMBER_KEYS
        )
        or (
            exit_code is not None
            and (isinstance(exit_code, bool) or not isinstance(exit_code, int))
        )
        or (probe is not None and not isinstance(probe, dict))
    ):
        return None
    return payload


def recordable_result(  # noqa: PLR0912  # each persisted scalar has an independent type guard
    result: ToolResult,
) -> dict[str, Any]:
    """Validate envelope fields before they enter the durable event chain.

    A malformed value is a failed tool result, rather than a value that this helper silently
    coerces or drops.  The manager catches :class:`ToolResultValidationError`, replaces the
    producer value with a typed failure, and still emits the closing lifecycle event.
    """
    rejected: list[str] = []
    if not isinstance(result.success, bool):
        rejected.append("success")
    if not isinstance(result.stdout, str):
        rejected.append("stdout")
    if not isinstance(result.stderr, str):
        rejected.append("stderr")
    if (
        isinstance(result.artifact_ids, (str, bytes))
        or not isinstance(result.artifact_ids, (tuple, list))
        or any(not isinstance(value, str) or not value for value in result.artifact_ids)
    ):
        rejected.append("artifact_ids")
    exit_code = result.exit_code
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        rejected.append("exit_code")
    digest = result.result_sha256
    if digest is not None and not _SHA256.fullmatch(digest):
        rejected.append("result_sha256")
    error = result.error
    if error is not None and not isinstance(error, str):
        rejected.append("error")
    if result.code is not None and not isinstance(result.code, ToolResultCode):
        rejected.append("code")
    if result.timeout_s is not None and (
        type(result.timeout_s) not in (int, float)
        or not math.isfinite(result.timeout_s)
        or result.timeout_s <= 0
    ):
        rejected.append("timeout_s")
    if not isinstance(result.aborted, bool):
        rejected.append("aborted")
    mode = result.sandbox_mode if isinstance(result.sandbox_mode, SandboxMode) else None
    if result.sandbox_mode is not None and mode is None:
        rejected.append("sandbox_mode")
    enforcement = (
        result.sandbox_enforcement
        if isinstance(result.sandbox_enforcement, SandboxEnforcement)
        else None
    )
    if result.sandbox_enforcement is not None and enforcement is None:
        rejected.append("sandbox_enforcement")
    spec = result.sandbox_spec if isinstance(result.sandbox_spec, ResolvedExecutionSpec) else None
    if result.sandbox_spec is not None and spec is None:
        rejected.append("sandbox_spec")
    cleanup_confirmed = result.sandbox_cleanup_confirmed
    if cleanup_confirmed is not None and not isinstance(cleanup_confirmed, bool):
        rejected.append("sandbox_cleanup_confirmed")
        cleanup_confirmed = None
    termination = (
        _recordable_termination(result.sandbox_termination)
        if isinstance(result.sandbox_termination, TerminationEvidence)
        else None
    )
    if result.sandbox_termination is not None and termination is None:
        rejected.append("sandbox_termination")
    quota = (
        _recordable_quota(result.quota_evidence)
        if isinstance(result.quota_evidence, WorkspaceQuotaEvidence)
        else None
    )
    if result.quota_evidence is not None and quota is None:
        rejected.append("quota_evidence")
    if rejected:
        note = f"unrecordable result fields rejected: {', '.join(dict.fromkeys(rejected))}"
        raise ToolResultValidationError(note)
    return {
        "exit_code": exit_code,
        "result_sha256": digest,
        "error": error,
        "result_code": result.code,
        "timeout_s": result.timeout_s,
        "aborted": result.aborted,
        "sandbox_mode": mode,
        "sandbox_mode_invalid": "sandbox_mode" in rejected,
        "sandbox_enforcement": enforcement,
        "sandbox_spec": spec.as_payload() if spec is not None else None,
        "sandbox_cleanup_confirmed": cleanup_confirmed,
        "sandbox_termination": termination,
        "sandbox_quota_evidence": quota,
    }


def recordable_terminal_result(
    tool_name: str,
    result: ToolResult,
    *,
    tool: Tool,
    arguments: dict[str, Any],
    invocation: ToolInvocation,
) -> tuple[dict[str, Any], dict[str, object] | None]:
    """Validate the canonical result and bind output declarations to this exact call."""
    recorded = recordable_result(result)
    value_payload = (
        canonical_result(result.value, success=result.success) if result.value is not None else None
    )
    declarations = _recordable_output_declarations(tool_name, tool, arguments, invocation, result)
    if declarations is not None:
        recorded["output_declarations"] = declarations
    recordable_publication_progress_fields(tool_name, result)
    return recorded, value_payload


def _recordable_output_declarations(
    tool_name: str,
    tool: Tool,
    arguments: dict[str, Any],
    invocation: ToolInvocation,
    result: ToolResult,
) -> list[dict[str, object]] | None:
    """Bind a result sidecar to fixed arguments or the executed fresh manifest."""
    raw = result.output_declarations
    is_python = (
        tool_name == "run_python"
        and type(tool).__module__ == "thymira.tools.builtins.run_python"
        and type(tool).__name__ == "RunPython"
        and getattr(tool, "name", None) == "run_python"
    )
    if raw is None:
        if (
            is_python
            and result.success
            and (arguments.get("output_manifest_path") or arguments.get("output_artifacts"))
        ):
            raise ToolResultValidationError(
                "successful run_python result omitted its output declarations"
            )
        if result.publication_progress is not None:
            raise ToolResultValidationError("Python publication progress has no declarations")
        return None
    if not is_python or result.exit_code != 0:
        raise ToolResultValidationError(
            "Python output declarations are outside their call contract"
        )
    try:
        declarations = validated_python_output_declarations(
            raw, manifest_path=arguments.get("output_manifest_path")
        )
        expected = _expected_output_declarations(arguments, invocation)
    except (OSError, ToolExecutionError, TypeError, ValueError) as exc:
        raise ToolResultValidationError("Python output declarations do not match the call") from exc
    if not expected or _descriptor_identity(declarations) != _descriptor_identity(expected):
        raise ToolResultValidationError("Python output declarations do not match the call")
    return [item.model_dump(mode="json", exclude_none=True) for item in declarations]


def _expected_output_declarations(
    arguments: dict[str, Any], invocation: ToolInvocation
) -> tuple[RunPythonOutputArtifact, ...]:
    """Resolve the declaration contract from manifest bytes or this call's fixed arguments."""
    manifest_path = arguments.get("output_manifest_path")
    if manifest_path:
        return load_output_manifest(invocation, str(manifest_path))
    raw_fixed = arguments.get("output_artifacts")
    if not isinstance(raw_fixed, (list, tuple)):
        raise TypeError("fixed output declarations are invalid")
    return (
        validated_python_output_declarations(
            tuple(RunPythonOutputArtifact.model_validate(item) for item in raw_fixed)
        )
        if raw_fixed
        else ()
    )


def _descriptor_identity(
    declarations: tuple[RunPythonOutputArtifact, ...],
) -> tuple[tuple[object, ...], ...]:
    """Compare every declared path, kind, media type, and explicit reuse identity."""
    return tuple(
        (item.path, item.kind.value, item.media_type, item.reuse_artifact_id)
        for item in declarations
    )


def recordable_publication_progress_fields(tool_name: str, result: ToolResult) -> dict[str, object]:
    """Serialize the one runtime-owned sidecar only after revalidating its typed payload."""
    progress = result.publication_progress
    if progress is None:
        return {}
    if tool_name != "run_python" or result.success or result.exit_code != 0:
        raise ToolResultValidationError(
            "Python publication progress is outside its result contract"
        )
    try:
        normalized = validated_python_publication_progress(progress)
    except ValueError as exc:
        raise ToolResultValidationError(str(exc)) from exc
    try:
        declarations = validated_python_output_declarations(result.output_declarations)
    except ValueError as exc:
        raise ToolResultValidationError(
            "Python publication progress has invalid declarations"
        ) from exc
    expected = {(item.path, item.kind) for item in declarations}
    observed = {*normalized.missing, *normalized.present}
    if observed != expected:
        raise ToolResultValidationError(
            "Python publication progress does not partition the declared outputs"
        )
    return {"publication_progress": normalized.model_dump(mode="json")}


def recordable_tool_completed_payload(
    tool_name: str,
    completed: ToolCall,
    requested_sandbox_mode: SandboxMode | None,
    recorded: dict[str, Any],
    discovery_witness_artifact_id: str | None,
    value_payload: dict[str, object] | None,
    result: ToolResult,
) -> dict[str, object]:
    """Build the canonical terminal event payload without changing the failure value envelope."""
    return {
        "tool_call_id": completed.id,
        "task_id": completed.task_id,
        "tool": tool_name,
        "status": completed.status,
        "exit_code": completed.exit_code,
        "result_code": recorded["result_code"],
        "timeout_s": recorded["timeout_s"],
        "aborted": recorded["aborted"],
        "artifact_ids": list(completed.artifact_ids),
        "result_sha256": completed.result_sha256,
        "error": completed.error,
        "sandbox_mode": recorded["sandbox_mode"],
        "sandbox_mode_invalid": recorded["sandbox_mode_invalid"],
        "requested_sandbox_mode": requested_sandbox_mode,
        "sandbox_enforcement": completed.sandbox_enforcement,
        "sandbox_spec": recorded["sandbox_spec"],
        "sandbox_cleanup_confirmed": recorded["sandbox_cleanup_confirmed"],
        "sandbox_termination": recorded["sandbox_termination"],
        "sandbox_quota_evidence": recorded["sandbox_quota_evidence"],
        "discovery_witness_artifact_id": discovery_witness_artifact_id,
        "value": value_payload,
        **(
            {"output_declarations": recorded["output_declarations"]}
            if "output_declarations" in recorded
            else {}
        ),
        **recordable_publication_progress_fields(tool_name, result),
    }


__all__ = [
    "recordable_publication_progress_fields",
    "recordable_result",
    "recordable_terminal_result",
    "recordable_tool_completed_payload",
]
