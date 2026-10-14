"""Replay and freshness checks for THY plan reviews bound to the Gate event log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.core.execution_review import (
    PLAN_RESUME_TARGET,
    parked_review_decision,
    pending_review_decision,
    review_resume_target,
)
from thymira.schemas import Decision, EventType, PolicyDecision, RunCondition, RunStage, WaitReason
from thymira.thy.models import PlanOutput, ThyPlanReview
from thymira.thy.plan_review import (
    plan_candidate_sha256,
    plan_candidate_snapshot,
    plan_review_scope,
    plan_review_scope_sha256,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.core.control_plane import RunController
    from thymira.schemas import Event, ExecutionConstraints, Run


def plan_review_from_events(events: Sequence[Event], run_id: str) -> ThyPlanReview | None:
    """Recover the exact parked plan candidate from the authoritative Gate request."""
    decision = parked_review_decision(events, run_id)
    if decision is None or review_resume_target(events, decision.id) != PLAN_RESUME_TARGET:
        return None
    return _plan_review_for_decision(events, decision)


def unparked_plan_review_from_events(events: Sequence[Event], run_id: str) -> ThyPlanReview | None:
    """Recover a Gate request committed just before a crash but not yet parked by Core."""
    decision = pending_review_decision(events, run_id)
    if decision is None or review_resume_target(events, decision.id) is not None:
        return None
    request = next(
        (
            event
            for event in reversed(events)
            if event.type is EventType.HUMAN_APPROVAL_REQUESTED
            and event.payload.get("decision_id") == decision.id
            and event.payload.get("action_type") == "plan.proposed"
        ),
        None,
    )
    if request is None:
        return None
    return _plan_review_for_decision(events, decision)


def _plan_review_for_decision(events: Sequence[Event], decision: PolicyDecision) -> ThyPlanReview:
    """Rebuild and validate one candidate from its Gate request event."""
    request = next(
        (
            event
            for event in reversed(events)
            if event.type is EventType.HUMAN_APPROVAL_REQUESTED
            and event.payload.get("decision_id") == decision.id
            and event.payload.get("action_type") == "plan.proposed"
        ),
        None,
    )
    if request is None or request.hash is None:
        raise PlanReviewError("parked plan review has no durable candidate request")
    metadata = request.payload.get("plan_review")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("scope"), dict):
        raise PlanReviewError("parked plan review has no durable scope binding")
    review = ThyPlanReview(
        candidate=PlanOutput.model_validate(request.payload.get("plan")),
        decision=decision,
        request_event_id=request.event_id,
        request_event_hash=request.hash,
        candidate_sha256=metadata.get("candidate_sha256"),
        scope_sha256=metadata.get("scope_sha256"),
        scope=metadata["scope"],
    )
    validate_plan_review_request(events, review)
    return review


def plan_review_execution_context(
    events: Sequence[Event], review: ThyPlanReview
) -> tuple[PolicyDecision | None, ExecutionConstraints | None]:
    """Recover the exact execution Gate decision captured by a plan review's scope."""
    raw_decision = review.scope.get("execution_decision")
    if raw_decision is None:
        if review.scope.get("execution_constraints") is not None:
            raise PlanReviewError("plan review has constraints without an execution decision")
        return None, None
    try:
        decision = PolicyDecision.model_validate(raw_decision)
    except (TypeError, ValueError) as exc:
        raise PlanReviewError("plan review has an invalid execution decision") from exc
    if not any(
        event.type is EventType.POLICY_DECISION
        and event.run_id == review.decision.run_id
        and event.payload == decision.model_dump(mode="json")
        for event in events
    ):
        raise PlanReviewError("plan review execution decision is missing from the event log")
    if review.scope.get("execution_constraints") != decision.execution_constraints.model_dump(
        mode="json"
    ):
        raise PlanReviewError("plan review execution constraints do not match their decision")
    return decision, decision.execution_constraints


def validate_current_plan_review(
    events: Sequence[Event],
    run: Run,
    decision: PolicyDecision,
    *,
    policy_sha256: str,
) -> ThyPlanReview:
    """Validate the parked candidate against current Run, decision and policy evidence."""
    review = plan_review_from_events(events, run.id)
    if review is None or review.decision != decision:
        raise PlanReviewError("the pending plan candidate no longer matches this approval")
    execution_decision, execution_constraints = plan_review_execution_context(events, review)
    validate_plan_review_scope(
        review,
        run,
        execution_decision,
        execution_constraints,
        policy_sha256=policy_sha256,
    )
    return review


class PlanReviewError(ValueError):
    """The persisted candidate or its approval scope no longer matches the current Run."""


def validate_plan_review_request(events: Sequence[Event], review: ThyPlanReview) -> Event:
    """Verify that THY's typed pending review is the exact persisted Gate request."""
    request = next(
        (event for event in events if event.event_id == review.request_event_id),
        None,
    )
    if (
        request is None
        or request.type is not EventType.HUMAN_APPROVAL_REQUESTED
        or request.hash != review.request_event_hash
        or request.run_id != review.decision.run_id
        or request.subject_id != review.decision.subject_id
    ):
        raise PlanReviewError("plan approval request is missing or changed")
    if (
        request.payload.get("action_type") != "plan.proposed"
        or request.payload.get("decision_id") != review.decision.id
        or request.payload.get("plan") != plan_candidate_snapshot(review.candidate)
    ):
        raise PlanReviewError("plan approval request does not contain the exact candidate")
    metadata = request.payload.get("plan_review")
    if not isinstance(metadata, dict):
        raise PlanReviewError("plan approval request has no review binding")
    scope = metadata.get("scope")
    if (
        metadata.get("version") != 1
        or metadata.get("candidate_sha256") != review.candidate_sha256
        or metadata.get("scope_sha256") != review.scope_sha256
        or metadata.get("scope") != review.scope
        or not isinstance(scope, dict)
        or plan_candidate_sha256(review.candidate) != review.candidate_sha256
        or plan_review_scope_sha256(scope) != review.scope_sha256
    ):
        raise PlanReviewError("plan approval request has an invalid candidate or scope digest")
    if not any(
        event.type is EventType.POLICY_DECISION
        and event.run_id == review.decision.run_id
        and event.payload == review.decision.model_dump(mode="json")
        for event in events
    ):
        raise PlanReviewError("plan review has no matching Gate decision")
    return request


def validate_plan_review_scope(
    review: ThyPlanReview,
    run: Run,
    execution_decision: PolicyDecision | None,
    execution_constraints: ExecutionConstraints | None,
    *,
    policy_sha256: str,
) -> None:
    """Reject a review after its request, execution authorization or policy scope changes."""
    if review.decision.run_id != run.id or review.decision.policy_sha256 != policy_sha256:
        raise PlanReviewError("plan review no longer matches the current Run policy")
    current_scope = plan_review_scope(
        run,
        execution_decision,
        execution_constraints,
        policy_sha256=policy_sha256,
    )
    if (
        review.scope != current_scope
        or plan_review_scope_sha256(current_scope) != review.scope_sha256
    ):
        raise PlanReviewError("plan review scope changed before approval")


def park_plan_review(
    controller: RunController,
    events: Sequence[Event],
    run_id: str,
    review: ThyPlanReview,
) -> None:
    """Persist the exact candidate review while the Run remains in the Planning stage."""
    validate_plan_review_request(events, review)
    if (
        review.decision.run_id != run_id
        or review.decision.decision is not Decision.REQUIRE_HUMAN_REVIEW
    ):
        raise PlanReviewError("pending plan review does not match the active Run")
    current = controller.current_state(run_id).state
    if current.condition is RunCondition.WAITING:
        if current.wait_reason is not WaitReason.APPROVAL:
            raise PlanReviewError("Run is waiting for a non-approval while a plan is pending")
        parked = parked_review_decision(events, run_id)
        if parked is None or parked.id != review.decision.id:
            raise PlanReviewError("Run is already parked for a different review")
        return
    if current.stage is not RunStage.PLANNING:
        raise PlanReviewError("plan approval must be requested before execution begins")
    controller.park_for_review(run_id, review.decision, resume_from="plan")


__all__ = [
    "PlanReviewError",
    "park_plan_review",
    "plan_review_execution_context",
    "plan_review_from_events",
    "unparked_plan_review_from_events",
    "validate_current_plan_review",
    "validate_plan_review_request",
    "validate_plan_review_scope",
]
