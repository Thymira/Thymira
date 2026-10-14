"""Classify failed agent steps without mistaking provider failures for turn exhaustion."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict

from thymira.agents.llm.base import LLMCallError
from thymira.agents.llm.provider_diagnostics import extract_provider_diagnostics
from thymira.agents.tool_bridge import (
    RepeatedInvalidPythonSourceError,
    RepeatedPythonPublicationError,
)

if TYPE_CHECKING:
    from pydantic_ai.exceptions import UnexpectedModelBehavior


class AgentEndReason(StrEnum):
    """Why a step ended, recorded as ``end_reason`` on every ``agent.completed``.

    ``COMPLETED``: a validated output. ``MAX_TURNS``: PydanticAI exhausted the step's retries or
    request limit. ``AWAITING_APPROVAL``: a tool call needs a human's answer; the step remains
    in-flight after its ``agent.parked`` checkpoint and the task is PENDING until the runtime
    re-runs it. ``TOOL_FAILURE``: the validated output followed an unresolved failed tool call.
    ``CONTEXT_BUDGET``: durable compaction could not reduce the prompt enough for dispatch, so
    the provider was not called. ``RESUME_HISTORY``: a parked history could not be transformed
    into a checkpoint-aware conversation without silently dropping or duplicating user context.
    ``PROVIDER_ERROR``: a provider call failed before producing a usable response.
    """

    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    AWAITING_APPROVAL = "awaiting_approval"
    TOOL_FAILURE = "tool_failure"
    CONTEXT_BUDGET = "context_budget"
    RESUME_HISTORY = "resume_history"
    PROVIDER_ERROR = "provider_error"


class FailureDetails(TypedDict, total=False):
    """Optional specific diagnosis for the existing terminal event recorder."""

    reason: str
    end_reason: AgentEndReason


def model_failure_details(error: LLMCallError | UnexpectedModelBehavior) -> FailureDetails:
    """Return safe code-owned diagnostics, never raw provider bodies or exception causes."""
    if isinstance(error, LLMCallError):
        reason = (
            "LLM provider request failed before a usable response was received. "
            "Check the provider configuration and tool schemas before retrying."
        )
        if details := extract_provider_diagnostics(error).summary():
            reason = f"{reason} Provider response details: {details}."
        return {
            "reason": reason,
            "end_reason": AgentEndReason.PROVIDER_ERROR,
        }
    if isinstance(error, (RepeatedInvalidPythonSourceError, RepeatedPythonPublicationError)):
        return {"reason": str(error), "end_reason": AgentEndReason.TOOL_FAILURE}
    return {}
