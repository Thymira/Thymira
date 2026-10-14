"""Bind a human answer to the exact pending decision shown by its client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.core.execution_review import (
    EXECUTION_START_RESUME_TARGET,
    PLAN_RESUME_TARGET,
    human_approval_outcome,
    parked_review_decision,
    pending_review_decision,
    review_resume_target,
    validate_execution_resolver,
)
from thymira.core.governance_binding import final_governance_binding, policy_decision_binding
from thymira.core.plan_review import (
    PlanReviewError,
    validate_current_plan_review,
)
from thymira.core.run_state import RunTransitionKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.core.control_plane import RunController
    from thymira.policies import Gate
    from thymira.schemas import Actor, Event, Id, PolicyDecision, Run


class RunApprovalError(ValueError):
    """Base error for an invalid human decision request."""


class RunNotAwaitingApprovalError(RunApprovalError):
    """Raised when a human decision targets a Run without a pending approval."""


class RunReviewChangedError(RunApprovalError):
    """Raised when the displayed review no longer matches the Run's pending decision."""


@dataclass(frozen=True, slots=True)
class RunApprovalDependencies:
    """Bound collaborators needed to resolve one fresh Run review."""

    controller: RunController
    read_events: Callable[[Id], list[Event]]
    read_run: Callable[[Id], Run]
    check_audit_freshness: Callable[[Id, PolicyDecision], None]


def require_pending_review(
    run_id: str, decision: PolicyDecision | None, expected_decision_id: str | None
) -> PolicyDecision:
    """Reject a stale answer before the Gate records any approval or continuation."""
    if decision is None:
        raise RunNotAwaitingApprovalError(f"run {run_id!r} has no pending approval")
    if expected_decision_id is not None and decision.id != expected_decision_id:
        raise RunReviewChangedError("The pending review changed. Refresh and review it again.")
    return decision


def resolve_run_approval(
    dependencies: RunApprovalDependencies,
    run_id: Id,
    *,
    gate: Gate,
    actor: Actor,
    note: str | None,
    expected_decision_id: str | None,
) -> bool:
    """Record an answer and transition the Run; return whether execution should resume."""
    current = dependencies.controller.current_state(run_id)
    if (
        current.condition.value != "waiting"
        or current.wait_reason is None
        or current.wait_reason.value != "approval"
    ):
        raise RunNotAwaitingApprovalError(f"run {run_id!r} is not awaiting human approval")

    events = dependencies.read_events(run_id)
    decision = parked_review_decision(events, run_id) or pending_review_decision(events, run_id)
    decision = require_pending_review(run_id, decision, expected_decision_id)
    resume_target = review_resume_target(events, decision.id)
    if resume_target == EXECUTION_START_RESUME_TARGET:
        try:
            validate_execution_resolver(gate, actor)
        except ValueError as exc:
            raise RunApprovalError(str(exc)) from exc
    elif resume_target == PLAN_RESUME_TARGET:
        _validate_plan_review_approval(
            dependencies,
            run_id,
            decision,
            events,
            gate,
            actor,
        )

    dependencies.check_audit_freshness(run_id, decision)
    approved = gate.resolve_pending_approval(decision, note=note)
    if resume_target in (EXECUTION_START_RESUME_TARGET, PLAN_RESUME_TARGET) and (
        human_approval_outcome(dependencies.read_events(run_id), decision) is not approved
    ):
        raise RunApprovalError("Gate did not record this human answer")

    if decision.subject_kind == "tool_call":
        # Either answer resumes the paused tool call: approval lets the manager run that exact
        # call once, and rejection records a denial the agent can adapt to.
        dependencies.controller.advance(
            run_id,
            RunTransitionKind.RESUME,
            payload={
                ("approved_by" if approved else "rejected_by"): actor.to_json_dict(),
                "approved": approved,
                "resume_from": "execution",
            },
        )
        return True
    if approved:
        dependencies.controller.advance(
            run_id,
            RunTransitionKind.RESUME,
            payload={
                "approved_by": actor.to_json_dict(),
                "resume_from": resume_target or "approval",
            },
        )
        return True

    payload: dict[str, object] = {
        "rejected_by": actor.to_json_dict(),
        **policy_decision_binding(decision),
    }
    if decision.subject_kind == "findings":
        payload.update(final_governance_binding(run_id, decision, dependencies.read_events(run_id)))
    dependencies.controller.advance(run_id, RunTransitionKind.BLOCK, payload=payload)
    return False


def _validate_plan_review_approval(
    dependencies: RunApprovalDependencies,
    run_id: Id,
    decision: PolicyDecision,
    events: list[Event],
    gate: Gate,
    actor: Actor,
) -> None:
    """Require a human answer to the unchanged candidate and its current request scope."""
    try:
        validate_execution_resolver(gate, actor)
    except ValueError as exc:
        raise RunApprovalError(str(exc)) from exc
    try:
        validate_current_plan_review(
            events,
            dependencies.read_run(run_id),
            decision,
            policy_sha256=gate.engine.policy_sha256,
        )
    except (PlanReviewError, TypeError, ValueError) as exc:
        raise RunReviewChangedError(
            "The plan review changed. Refresh and review the current proposal again."
        ) from exc
