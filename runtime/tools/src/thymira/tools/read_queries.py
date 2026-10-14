"""Policy-gated Session read queries without a Run or ordinary tool execution context."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.events import canonical_json, sha256_text
from thymira.policies import Policy, RiskProfile, ToolCapability, evaluate_capability
from thymira.schemas import Actor, ActorKind, Decision, EventType, new_id
from thymira.schemas.session_chat import SESSION_READ_OPERATIONS, SessionReadOperation
from thymira.tools.models import ToolExecutionError

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.state.session_discussions import SessionProviderLog

_SHA256_HEX_LENGTH = 64


def execute_session_read_query[ReadValue](
    operation: SessionReadOperation,
    *,
    policy: Policy,
    log: SessionProviderLog,
    request_sha256: str,
    callback: Callable[[], ReadValue],
) -> ReadValue:
    """Authorize one fixed Session read capability and record its tool lifecycle."""
    if operation not in SESSION_READ_OPERATIONS:
        raise ValueError("unknown Session read operation")
    if len(request_sha256) != _SHA256_HEX_LENGTH or any(
        value not in "0123456789abcdef" for value in request_sha256
    ):
        raise ValueError("Session read request digest is invalid")
    capability = ToolCapability(
        id=operation,
        data_access=("workspace",),
        side_effects=(),
        external_effects=(),
        reversibility="reversible",
    )
    risk = RiskProfile(
        risk_level="low",
        activity_category="session_read",
        confidence=1.0,
    )
    evaluation = evaluate_capability(policy, capability, risk)
    query_id = new_id("tool")
    log.append(
        EventType.POLICY_DECISION,
        Actor.system(),
        {
            "subject_kind": "session_read_query",
            "operation": operation,
            "decision": evaluation.decision.value,
            "rule_id": evaluation.rule_id,
            "reason": evaluation.reason,
            "constraints": evaluation.execution_constraints.to_json_dict(),
            "policy_sha256": sha256_text(canonical_json(policy.to_json_dict())),
            "request_sha256": request_sha256,
        },
        subject_id=query_id,
        producer="thymira.tools",
    )
    refusal = evaluation.execution_constraints.denies_tool(operation, ())
    constraints = evaluation.execution_constraints
    if constraints.requires_human_review:
        refusal = "the Session read policy requires human review"
    elif constraints.required_evidence:
        refusal = "the Session read policy requires unavailable evidence"
    elif (
        constraints.max_tool_calls is not None
        and _prior_read_calls(log) >= constraints.max_tool_calls
    ):
        refusal = "the Session read policy tool-call limit has been reached"
    if evaluation.decision not in {Decision.PASS, Decision.WARNING} or refusal is not None:
        reason = refusal or "the Session read policy did not grant automatic permission"
        log.append(
            EventType.TOOL_DENIED,
            _tool_actor(operation),
            {
                "tool": operation,
                "request_sha256": request_sha256,
                "reason": reason,
                "decision": evaluation.decision.value,
                "rule_id": evaluation.rule_id,
            },
            subject_id=query_id,
            producer="thymira.tools",
        )
        raise ToolExecutionError(reason)

    log.append(
        EventType.TOOL_STARTED,
        _tool_actor(operation),
        {"tool": operation, "request_sha256": request_sha256},
        subject_id=query_id,
        producer="thymira.tools",
    )
    try:
        result = callback()
    except Exception as exc:  # log every callback failure, then preserve it
        log.append(
            EventType.TOOL_COMPLETED,
            _tool_actor(operation),
            {
                "tool": operation,
                "status": "failed",
                "request_sha256": request_sha256,
                "error_type": type(exc).__name__,
            },
            subject_id=query_id,
            producer="thymira.tools",
        )
        raise
    log.append(
        EventType.TOOL_COMPLETED,
        _tool_actor(operation),
        {"tool": operation, "status": "completed", "request_sha256": request_sha256},
        subject_id=query_id,
        producer="thymira.tools",
    )
    return result


def _prior_read_calls(log: SessionProviderLog) -> int:
    """Count attempted read operations in this exact Session discussion log."""
    return sum(
        event.type in {EventType.TOOL_STARTED, EventType.TOOL_DENIED}
        and event.payload.get("tool") in SESSION_READ_OPERATIONS
        for event in log.events()
    )


def _tool_actor(operation: SessionReadOperation) -> Actor:
    """Return the truthful tool identity for one locally implemented operation."""
    return Actor(kind=ActorKind.TOOL, id=operation, authenticated=True)
