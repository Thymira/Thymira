"""Tool bridge: agent tool-calling through the Tool Manager + Gate under an allowlist (THY-04).

Enforces 'every tool call goes through the Tool Manager + Permission Policy'. Only
`spec.tool_allowlist` is ever exposed to the model as a callable tool — a tool the registry holds
but the spec does not list is never built, so an agent cannot even attempt it. A denied capability
comes back from `ToolManager.execute` as a `ToolExecution` whose result carries the reason, never
an exception, so a PydanticAI tool call here can only ever produce a bounded error the model can
read and adapt to, not a crash of the run — unless a human still has to answer it, in which case
the step ends with `ApprovalRequired` instead (`ToolExecution.pending_approval`, Task 1).

`usage` (THY-07) is optional: given, every call — attempted, denied or failed alike, since each one
went through the Tool Manager pipeline regardless of outcome — charges one against
`RunUsage.tool_calls`, raising `UsageLimitExceededError` the moment `max_tool_calls` is crossed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic_ai import ApprovalRequired, Tool, ToolReturn
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from thymira.agents.history import TOOL_RETURN_SUCCESS_METADATA_KEY
from thymira.agents.prompt_framing import frame_untrusted
from thymira.agents.retry_policy import (
    MAX_IDENTICAL_INVALID_PYTHON_FAILURES,
    RepeatedInvalidPythonSourceError,
    RepeatedPythonPublicationError,
    retry_stop_for_task,
)
from thymira.observability import tool as tool_observation
from thymira.schemas import EventType, ToolCallStatus
from thymira.tools import ToolExecution, ToolManager, canonical_value, input_schema, value_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage

    from thymira.agents.spec import AgentSpec
    from thymira.agents.usage import RunUsage
    from thymira.tools import Tool as ToolContract
    from thymira.tools import ToolContext, ToolRegistry

DENIED_MARKER = "[denied]"
"""The last line of every denied call's rendering: the fact the model anchors on to tell "not
allowed" from "failed". A denied call is not retried unchanged; the agent adapts or reports."""

_PYTHON_SYNTAX_PREFLIGHT_PREFIX = (
    "invalid arguments for tool 'run_python': code: Value error, invalid Python syntax at line "
)


def invalid_python_source_counts(messages: Sequence[ModelMessage]) -> dict[str, int]:
    """Count exact-source ``run_python`` syntax failures in verified conversation history."""
    sources: dict[str, str] = {}
    counts: dict[str, int] = {}
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if not isinstance(part, ToolCallPart) or part.tool_name != "run_python":
                    continue
                arguments = _tool_call_arguments(part)
                identity = _python_source_identity(arguments)
                if identity is not None:
                    sources[part.tool_call_id] = identity
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if (
                    not isinstance(part, ToolReturnPart)
                    or part.tool_name != "run_python"
                    or not _is_invalid_python_syntax_return(part)
                ):
                    continue
                digest = sources.pop(part.tool_call_id, None)
                if digest is not None:
                    counts[digest] = counts.get(digest, 0) + 1
    return counts


def repeated_invalid_python_source_reason(messages: Sequence[ModelMessage]) -> str | None:
    """Return the terminal diagnostic if a restored conversation already hit the limit."""
    if any(
        count >= MAX_IDENTICAL_INVALID_PYTHON_FAILURES
        for count in invalid_python_source_counts(messages).values()
    ):
        return str(RepeatedInvalidPythonSourceError())
    return None


def _tool_call_arguments(part: ToolCallPart) -> dict[str, Any] | None:
    """Decode a source call's object arguments without accepting non-object payloads."""
    if isinstance(part.args, dict):
        return part.args
    if not isinstance(part.args, str):
        return None
    try:
        parsed = json.loads(part.args)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _python_source_identity(arguments: dict[str, Any] | None) -> str | None:
    """Identify exact inline bytes or one immutable registered source revision."""
    if arguments is None:
        return None
    source_id = arguments.get("source_artifact_id")
    if isinstance(source_id, str) and source_id:
        return "artifact:" + source_id
    code = arguments.get("code")
    return hashlib.sha256(code.encode("utf-8")).hexdigest() if isinstance(code, str) else None


def _is_invalid_python_syntax_return(part: ToolReturnPart) -> bool:
    """Require runtime-confirmed failure metadata plus the exact argument-parser diagnostic."""
    metadata = part.metadata
    return (
        isinstance(metadata, Mapping)
        and metadata.get(TOOL_RETURN_SUCCESS_METADATA_KEY) is False
        and isinstance(part.content, str)
        and _PYTHON_SYNTAX_PREFLIGHT_PREFIX in part.content
    )


def build_agent_tools(
    spec: AgentSpec,
    registry: ToolRegistry,
    context: ToolContext,
    *,
    usage: RunUsage | None = None,
    message_history: Sequence[ModelMessage] = (),
) -> tuple[Tool[None], ...]:
    """Build the PydanticAI tools `spec.tool_allowlist` authorizes, bound to one `ToolContext`.

    Raises:
        KeyError: `spec.tool_allowlist` names a tool `registry` does not hold — a spec/registry
            mismatch this refuses to paper over by silently registering fewer tools than declared.
    """
    manager = ToolManager(registry)
    # Keep the public history argument for compatibility with callers. Retry enforcement is
    # reconstructed from task-scoped terminal events by the shared policy below.
    del message_history
    tools: list[Tool[None]] = []
    for tool_name in spec.tool_allowlist:
        registered_tool = registry.get(tool_name)
        tools.append(
            _build_one_tool(
                manager,
                context,
                registry,
                registered_tool,
                usage,
            )
        )
    return tuple(tools)


TRACE_OUTPUT_LIMIT = 1024 * 1024
"""How much of a tool's output reaches the trace. The agent still receives all of it.

`write_file` refuses more than a megabyte before it writes, but `run_python` captures whatever the
subprocess printed with no cap at all, and both feed the same observation. A step that forgets to
bound a `print` — a whole dataframe, or the SVG buffer the Visualization agent is told to emit —
would hand Langfuse a multi-megabyte output, run it through a full redaction scan on the way, and
meet Langfuse Cloud's 5 MB per-request ceiling with nothing able to truncate it: the SDK batches by
event count, never by size, and dropped its own size cap in v3.
"""

_SHA256_LENGTH = 64


def _bounded(output: str) -> str:
    """What the trace records — the agent's own context is never shortened by this."""
    if len(output) <= TRACE_OUTPUT_LIMIT:
        return output
    return f"{output[:TRACE_OUTPUT_LIMIT]}…[truncated, {len(output)} characters in full]"


STDERR_TAIL_LINES = 40
"""How many trailing stderr lines the model sees; the full text stays on the ToolCall."""


def render_tool_output(execution: ToolExecution) -> str:
    """Render the one text projection of a tool result the model reads.

    The order is fixed so a reader (model or parser) can anchor on the last line: a denied or
    failed call is ``Error: <reason>``; a successful call is its stdout or ``(no output)``; a
    non-empty stderr follows as a bounded tail; the exit-code marker comes next, the way dsh's
    ``[exit code: N]`` marker is anchored, when the tool ran a process; a denied call appends
    ``[denied]`` after everything else, so ``[denied]`` -- not the exit-code marker -- is the
    last line whenever the call was denied.
    """
    result = execution.result
    lines: list[str] = []
    text = value_text(result.value) if result.value is not None else result.stdout
    # Once a typed value exists, the model must see only fields reconstructable from the
    # canonical value.  Legacy envelope stderr is retained for direct scalar callers, but a
    # producer's unmodelled auxiliary field must not cross this bridge.
    stderr = result.stderr
    if result.value is not None:
        value_payload = canonical_value(result.value)
        projected_stderr = value_payload.get("stderr", "")
        stderr = projected_stderr if isinstance(projected_stderr, str) else ""
    if not result.success:
        lines.append(f"Error: {text or result.error or 'tool call failed'}")
    else:
        lines.append(text.rstrip("\n") if text.strip() else "(no output)")
    if stderr.strip():
        stderr_lines = stderr.rstrip("\n").splitlines()
        if len(stderr_lines) > STDERR_TAIL_LINES:
            stderr_lines = [
                f"[stderr truncated to the last {STDERR_TAIL_LINES} lines]",
                *stderr_lines[-STDERR_TAIL_LINES:],
            ]
        lines.append("")
        lines.append("stderr:")
        lines.extend(stderr_lines)
    if result.exit_code is not None:
        lines.append(f"[exit code: {result.exit_code}]")
    if execution.call.status is ToolCallStatus.DENIED:
        lines.append(DENIED_MARKER)
    return "\n".join(lines)


def _build_one_tool(
    manager: ToolManager,
    context: ToolContext,
    registry: ToolRegistry,
    tool: ToolContract,
    usage: RunUsage | None,
) -> Tool[None]:
    tool_name = tool.name

    def _call(**arguments: Any) -> ToolReturn[str]:
        with tool_observation(name=tool_name, arguments=arguments) as observation:
            execution = manager.execute(context, tool_name, arguments)
            if usage is not None:
                usage.charge_tool()
            output = render_tool_output(execution)
            framed_output = frame_untrusted(output, label="tool-output")
            observation.update(output=_bounded(framed_output))
            if execution.pending_approval is not None:
                # The step ends here, cleanly: PydanticAI hands back `DeferredToolRequests`
                # instead of looping, the runner records the step as PENDING, and the runtime --
                # never the model -- decides when this exact call runs (`ToolManager`'s ticket).
                identity = _pending_tool_identity(context, execution.call.id)
                raise ApprovalRequired(
                    metadata={
                        "decision_id": execution.pending_approval.id,
                        "tool_call_id": execution.call.id,
                        **identity,
                    }
                )
            if (
                tool_name in {"run_python", "write_file"}
                and not execution.result.success
                and context.task_id is not None
            ):
                stop = retry_stop_for_task(
                    context.event_log.events(),
                    context.task_id,
                    artifact_store=context.artifact_store,
                    tool_registry=registry,
                    tool_context=context,
                    current_call_id=execution.call.id,
                )
                if stop is not None:
                    if stop.kind == "syntax":
                        raise RepeatedInvalidPythonSourceError(stop.reason)
                    raise RepeatedPythonPublicationError
            return ToolReturn(
                framed_output,
                metadata={TOOL_RETURN_SUCCESS_METADATA_KEY: execution.result.success},
            )

    return Tool.from_schema(
        _call,
        name=tool_name,
        description=tool.description,
        json_schema=input_schema(tool),
    )


def _pending_tool_identity(context: ToolContext, tool_call_id: str) -> dict[str, str | None]:
    """Read the exact ticket and mode the Tool Manager recorded for a pending call."""
    event = next(
        (
            event
            for event in reversed(context.event_log.events())
            if event.type is EventType.TOOL_DENIED
            and event.payload.get("tool_call_id") == tool_call_id
        ),
        None,
    )
    if event is None:
        raise RuntimeError("pending tool call has no denial evidence")
    ticket = event.payload.get("tool_intent_sha256")
    if not isinstance(ticket, str) or len(ticket) != _SHA256_LENGTH:
        raise RuntimeError("pending tool call has no valid ticket evidence")
    mode = event.payload.get("sandbox_mode")
    if mode is not None and not isinstance(mode, str):
        mode = getattr(mode, "value", None)
    if mode is not None and not isinstance(mode, str):
        raise RuntimeError("pending tool call has invalid sandbox mode evidence")
    return {"tool_intent_sha256": ticket, "sandbox_mode": mode}


__all__ = [
    "DENIED_MARKER",
    "MAX_IDENTICAL_INVALID_PYTHON_FAILURES",
    "RepeatedInvalidPythonSourceError",
    "build_agent_tools",
    "invalid_python_source_counts",
    "render_tool_output",
    "repeated_invalid_python_source_reason",
]
