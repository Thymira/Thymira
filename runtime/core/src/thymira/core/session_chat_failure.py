"""Closed, safe user-facing failure messages for the Session chat boundary."""

from __future__ import annotations

from thymira.agents.llm.base import LLMCallError, LLMConfigurationError
from thymira.agents.route_policy import ModelRouteDeniedError
from thymira.agents.usage import UsageLimitExceededError
from thymira.core.session_chat_budget import (
    MAX_SESSION_MODEL_REQUESTS,
    MAX_SESSION_MODEL_TOKENS,
    SessionChatBudgetError,
)
from thymira.core.session_chat_clarifications import (
    ClarificationContextError,
    ClarificationReplacementError,
)
from thymira.core.session_chat_selection import SessionDiscussionContextError


def safe_session_chat_failure(error: Exception) -> str:  # noqa: PLR0911  # closed failure categories
    """Explain a recoverable failure without persisting provider messages or source data."""
    if isinstance(error, ClarificationContextError):
        if isinstance(error, ClarificationReplacementError):
            return (
                "The pending request cannot be replaced by model interpretation. "
                "Select Request work and restate the complete replacement task and dataset. "
                "No work was started."
            )
        return (
            "The earlier clarification cannot be safely continued. "
            "Please restate the complete task and dataset. No work was started."
        )
    if isinstance(error, SessionDiscussionContextError):
        return (
            "The relevant prior discussion does not fit the context limit. "
            "No human message was shortened and no model request was sent."
        )
    if isinstance(error, ModelRouteDeniedError):
        return (
            "Session chat's model route is not allowed by this Session's saved configuration. "
            "Check the configured model and allowed routes. No work was started."
        )
    if isinstance(error, LLMConfigurationError):
        return (
            "Session chat has no usable model configuration. "
            "Check model settings. No work was started."
        )
    if isinstance(error, LLMCallError):
        return "The model request failed. No work was started. Send a new message to try again."
    if isinstance(error, SessionChatBudgetError):
        phase = {
            "citation repair": "citation repair",
            "recovery": "request recovery",
        }.get(error.purpose, "interpretation")
        details = (
            f"Reason: {error.reason}; requests {error.request_count}/{MAX_SESSION_MODEL_REQUESTS}; "
            f"{error.used_tokens}/{MAX_SESSION_MODEL_TOKENS} tokens used; "
            f"{error.available_tokens} available"
        )
        if error.projected_tokens is not None:
            details += f"; projected request {error.projected_tokens}"
        if error.usage_unknown:
            return (
                f"No answer was published because {phase} has unknown provider usage. "
                f"The remaining balance was reserved conservatively. {details}. "
                "This message will not be dispatched again automatically."
            )
        return (
            f"No answer was published because {phase} failed its model budget safety checks. "
            f"{details}."
        )
    if isinstance(error, UsageLimitExceededError):
        return (
            "The explanation exceeded its model budget. "
            "No work was started. Ask a shorter question."
        )
    return (
        "The session explanation could not be verified or was interrupted. No work was started. "
        "Send a new question to try again."
    )
