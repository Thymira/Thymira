"""Policy-gated publication of bounded risk questions and human review requests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.core.execution_review import (
    INTERVIEW_LIMIT_RESUME_TARGET,
    RISK_UNCERTAINTY_RESUME_TARGET,
)
from thymira.core.risk_interview_model import has_declared_value
from thymira.core.risk_profile_records import (
    CLARIFIABLE_RISK_FIELDS,
    MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
    RiskInterviewError,
    field_question_count,
    recorded_profile,
)
from thymira.core.risk_uncertainty_questions import (
    MAX_RISK_UNCERTAINTY_QUESTIONS,
    uncertainty_question_rounds,
)
from thymira.schemas import (
    ActionIntent,
    ActionKind,
    Actor,
    ActorKind,
    Decision,
    EventType,
    RunCondition,
    WaitReason,
    approval_names_decision,
    new_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.core.control_plane import MiraControlPlane
    from thymira.events import EventLog
    from thymira.schemas import ActivityProfile, Event, RiskAssessment, Run
    from thymira.state import LocalRunStore

INTERVIEW_LIMIT_SUMMARY = "Risk interview reached its question limit; human review is required."


def ensure_question_wait(
    store: LocalRunStore, control_plane: MiraControlPlane, run_id: str, question_event_id: str
) -> None:
    """Ask the policy-gated control plane to park the Run for this question."""
    current = control_plane.controller.current_state(run_id)
    if current.condition.value == "waiting" and current.wait_reason is not None:
        if current.wait_reason.value == "information":
            return
        raise RiskInterviewError(f"run {run_id!r} is waiting for {current.wait_reason.value}")
    event = next(
        (item for item in store.events(run_id) if item.event_id == question_event_id),
        None,
    )
    if event is None:
        raise RiskInterviewError(f"run {run_id!r} question evidence is missing")
    try:
        intent = ActionIntent.model_validate(event.payload["intent"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RiskInterviewError(f"run {run_id!r} question intent is invalid") from exc
    result = control_plane.handle(intent)
    if result.state is None or result.state.wait_reason is None:
        raise RiskInterviewError("the information request was not authorized to park the Run")
    if result.state.wait_reason.value != "information":
        raise RiskInterviewError("the information request parked the Run for the wrong reason")


def request_interview_limit_review(
    store: LocalRunStore, control_plane: MiraControlPlane, run_id: str, *, max_questions: int
) -> None:
    """Publish the concrete interview-limit decision and park at its existing resume target."""
    events = store.events(run_id)
    questions = [event for event in events if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED]
    last_question_event = questions[-1] if questions else None
    last_field = (
        last_question_event.payload.get("field") if last_question_event is not None else None
    )
    last_answer_event = next(
        (
            event
            for event in reversed(events)
            if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
            and last_question_event is not None
            and event.payload.get("question_event_id") == last_question_event.event_id
        ),
        None,
    )
    last_answer: dict[str, object] | None = None
    if last_answer_event is not None:
        sufficiency = last_answer_event.payload.get("sufficiency")
        last_answer = {
            "event_id": last_answer_event.event_id,
            "answer": last_answer_event.payload.get("answer"),
            "sufficiency": sufficiency if isinstance(sufficiency, dict) else None,
        }
    field_total = field_question_count(events, last_field) if isinstance(last_field, str) else 0
    profile = recorded_profile(store, run_id)
    remaining_fields = (
        tuple(
            field
            for field in CLARIFIABLE_RISK_FIELDS
            if not has_declared_value(getattr(profile, field))
        )
        if profile is not None
        else ()
    )
    reason = (
        "per_field_question_limit"
        if field_total >= MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD
        else "total_question_limit"
    )
    intent = ActionIntent(
        id=new_id("intent"),
        run_id=run_id,
        requester=Actor.system(),
        action_kind=ActionKind.REQUEST_APPROVAL,
        subject_kind="run",
        subject_id=run_id,
        purpose=INTERVIEW_LIMIT_SUMMARY,
        payload={
            "max_questions": max_questions,
            "max_questions_per_field": MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
            "field": last_field,
            "questions_asked": {
                "total": len(questions),
                "max_total": max_questions,
                "field_total": field_total,
                "max_per_field": MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
            },
            "last_question": (
                {
                    "event_id": last_question_event.event_id,
                    "field": last_field,
                    "question": last_question_event.payload.get("question"),
                }
                if last_question_event is not None
                else None
            ),
            "last_answer": last_answer,
            "remaining_fields": list(remaining_fields),
            "decision_effects": {
                "approve": (
                    "Continue this Run with unresolved activity facts still recorded; all "
                    "other policy and human approval gates continue to apply."
                ),
                "reject": (
                    "Block this Run; THY does not continue analysis using this unresolved "
                    "activity profile."
                ),
            },
            "reason": reason,
            "review_kind": "missing_activity_profile",
        },
        idempotency_key=f"risk-interview-limit:{run_id}:{max_questions}",
    )
    result = control_plane.handle(intent)
    if result.decision.decision is not Decision.REQUIRE_HUMAN_REVIEW:
        raise RiskInterviewError("question-limit escalation did not require human review")
    if result.state is None:
        control_plane.controller.park_for_review(
            run_id,
            result.decision,
            resume_from=INTERVIEW_LIMIT_RESUME_TARGET,
        )


def request_uncertainty_review(
    control_plane: MiraControlPlane,
    event_log: EventLog,
    run: Run,
    assessment: RiskAssessment,
    profile: ActivityProfile,
    actor: Actor,
    *,
    summary: str,
    max_attempts: int,
) -> None:
    """Publish the exact uncertain assessment, known facts and review consequences."""
    rounds = uncertainty_question_rounds(tuple(event_log.events()))
    current = control_plane.controller.current_state(run.id).state
    if current.condition is RunCondition.WAITING and current.wait_reason in {
        WaitReason.INFORMATION,
        WaitReason.APPROVAL,
    }:
        return
    intent = ActionIntent(
        id=new_id("intent"),
        run_id=run.id,
        requester=actor,
        action_kind=ActionKind.REQUEST_APPROVAL,
        subject_kind="run",
        subject_id=run.id,
        purpose=summary,
        payload={
            "risk_assessment_id": assessment.id,
            "activity_profile_id": assessment.activity_profile_id,
            "activity_profile_version": assessment.activity_profile_version,
            "max_attempts": max_attempts,
            "reason": "risk_classification_uncertain",
            "assessment_justification": assessment.justification,
            "missing_information": list(assessment.missing_information),
            "known_facts": {field: getattr(profile, field) for field in CLARIFIABLE_RISK_FIELDS},
            "clarification_rounds": {
                "total": len(rounds),
                "max_total": MAX_RISK_UNCERTAINTY_QUESTIONS,
                "items": list(rounds),
            },
            "decision_effects": {
                "approve": (
                    "Continue after an authenticated human accepts this exact uncertain "
                    "assessment; later policy and human approval gates still apply."
                ),
                "reject": ("Block this Run before subsequent analysis or execution continues."),
            },
        },
        idempotency_key=(
            f"mira-risk-uncertainty-limit:{run.id}:{assessment.id}:"
            f"{assessment.activity_profile_id}:{assessment.activity_profile_version}"
        ),
    )
    result = control_plane.handle(intent)
    if result.decision.decision is not Decision.REQUIRE_HUMAN_REVIEW:
        raise RuntimeError("risk-uncertainty escalation did not require human review")
    if result.state is None:
        control_plane.controller.park_for_review(
            run.id,
            result.decision,
            resume_from=RISK_UNCERTAINTY_RESUME_TARGET,
        )


def _closes_review(event: Event) -> bool:
    """Whether an approval event retires the bounded interview review request."""
    if not isinstance(event.payload.get("approved"), bool):
        return False
    if event.actor.kind is ActorKind.SYSTEM:
        return True
    return (
        event.actor.kind is ActorKind.HUMAN
        and event.actor.authenticated
        and ("automatic" not in event.payload or event.payload["automatic"] is False)
    )


def interview_limit_decision(events: Sequence[Event]) -> str | None:
    """Find the decision id created for the one bounded-interview review request."""
    for event in reversed(events):
        if event.type is not EventType.HUMAN_APPROVAL_REQUESTED:
            continue
        if event.payload.get("summary") != INTERVIEW_LIMIT_SUMMARY:
            continue
        decision_id = event.payload.get("decision_id")
        return decision_id if isinstance(decision_id, str) else None
    return None


def interview_limit_answered(events: Sequence[Event]) -> bool:
    """Return whether a human answer retired the bounded interview review request."""
    requested = interview_limit_decision(events)
    if requested is None:
        return False
    return any(
        event.type is EventType.HUMAN_APPROVAL
        and approval_names_decision(event.payload, requested)
        and _closes_review(event)
        for event in events
    )


def interview_limit_approved(events: Sequence[Event]) -> bool:
    """Return whether a human has answered the bounded interview's review request."""
    requested = interview_limit_decision(events)
    if requested is None:
        return False
    return any(
        event.type is EventType.HUMAN_APPROVAL
        and approval_names_decision(event.payload, requested)
        and event.payload.get("approved") is True
        and ("automatic" not in event.payload or event.payload["automatic"] is False)
        and event.actor.kind is ActorKind.HUMAN
        and event.actor.authenticated
        for event in events
    )
