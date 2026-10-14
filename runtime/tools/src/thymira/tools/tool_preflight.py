"""Argument and prerequisite validation before tool policy review or execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from thymira.events import scrub_credentials
from thymira.tools.builtins.python_arguments import RunPythonArguments
from thymira.tools.builtins.python_output_manifest import output_manifest_path
from thymira.tools.builtins.python_reference import load_python_source_reference
from thymira.tools.models import ToolExecutionError
from thymira.tools.sandbox.readiness import missing_runtime

if TYPE_CHECKING:
    from thymira.tools.models import Tool, ToolInvocation

_MAX_ARGUMENT_VALIDATION_ERRORS = 3
_MAX_ARGUMENT_VALIDATION_DETAIL = 512
_MAX_ARGUMENT_VALIDATION_LOCATION = 128
_MAX_ARGUMENT_VALIDATION_MESSAGE = 256


def validate_tool_request(
    tool: Tool,
    arguments: dict[str, Any],
    *,
    invocation: ToolInvocation | None = None,
) -> dict[str, Any]:
    """Validate arguments and return bounded, non-input diagnostics the model can repair."""
    if tool.arguments_model is None:
        _require_runtime(tool)
        return arguments
    try:
        validated = tool.arguments_model.model_validate(arguments)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        summaries: list[str] = []
        for error in errors[:_MAX_ARGUMENT_VALIDATION_ERRORS]:
            location = ".".join(str(item) for item in error.get("loc", ())) or "arguments"
            location = scrub_credentials(location)
            message = scrub_credentials(str(error.get("msg", "Invalid value")))
            location = _bounded_validation_fragment(location, _MAX_ARGUMENT_VALIDATION_LOCATION)
            message = _bounded_validation_fragment(message, _MAX_ARGUMENT_VALIDATION_MESSAGE)
            summaries.append(f"{location}: {message}")
        if len(errors) > _MAX_ARGUMENT_VALIDATION_ERRORS:
            summaries.append(f"{len(errors) - _MAX_ARGUMENT_VALIDATION_ERRORS} more errors")
        detail = "; ".join(summaries)[:_MAX_ARGUMENT_VALIDATION_DETAIL]
        raise ToolExecutionError(f"invalid arguments for tool '{tool.name}': {detail}") from exc
    except TypeError as exc:
        # Pydantic v2 deliberately does not wrap a validator-authored TypeError in
        # ValidationError. Plugin argument models are third-party code, so let that bug close as
        # the same pre-execution failure as an ordinary schema error instead of escaping the Tool
        # Manager without a tool.completed record. The exception may contain plugin data or a
        # credential, therefore only its scrubbed and bounded message reaches the model or log.
        message = scrub_credentials(str(exc) or type(exc).__name__)
        message = _bounded_validation_fragment(message, _MAX_ARGUMENT_VALIDATION_MESSAGE)
        detail = _bounded_validation_fragment(
            f"arguments: validator raised TypeError: {message}",
            _MAX_ARGUMENT_VALIDATION_DETAIL,
        )
        raise ToolExecutionError(f"invalid arguments for tool '{tool.name}': {detail}") from exc
    validated_arguments = validated.model_dump(mode="python")
    if isinstance(validated, RunPythonArguments):
        validated_arguments = _prepare_python_arguments(validated, invocation)
    _require_runtime(tool)
    return validated_arguments


def _prepare_python_arguments(
    validated: RunPythonArguments, invocation: ToolInvocation | None
) -> dict[str, Any]:
    """Bind source identity and fresh output manifests without changing legacy ticket shapes."""
    validated_arguments = validated.model_dump(mode="python")
    if validated.output_manifest_path is None:
        validated_arguments.pop("output_manifest_path", None)
    elif invocation is None:
        raise ToolExecutionError("output_manifest_path requires Tool Manager context")
    else:
        output_manifest_path(invocation, validated.output_manifest_path, fresh=True)
    artifact_id = validated.source_artifact_id
    if artifact_id is None:
        # Keep the pre-existing code-only ticket shape: the new optional field is not added
        # as `source_artifact_id=None` to legacy arguments.
        validated_arguments.pop("source_artifact_id", None)
    else:
        if invocation is None:
            raise ToolExecutionError(
                "run_python source_artifact_id requires Tool Manager invocation context"
            )
        try:
            reference = load_python_source_reference(invocation, artifact_id)
        except ValueError as exc:
            detail = scrub_credentials(str(exc))
            detail = _bounded_validation_fragment(detail, _MAX_ARGUMENT_VALIDATION_MESSAGE)
            raise ToolExecutionError(
                f"invalid arguments for tool 'run_python': code: Value error, {detail}"
            ) from exc
        validated_arguments["code"] = reference.source
        validated_arguments["source_artifact_id"] = reference.artifact_id
        validated_arguments["source_artifact_name"] = reference.name
        validated_arguments["source_artifact_sha256"] = reference.sha256
    return validated_arguments


def _bounded_validation_fragment(value: str, limit: int) -> str:
    """Bound one diagnostic fragment while marking that it was shortened."""
    return value if len(value) <= limit else f"{value[: limit - 1]}â€¦"


def _require_runtime(tool: Tool) -> None:
    """Return a recoverable failure before approval when container prerequisites are absent."""
    error = missing_runtime(tool)
    if error is not None:
        raise ToolExecutionError(error)
