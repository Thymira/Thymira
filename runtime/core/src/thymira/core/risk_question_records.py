"""Validated projections and immutable scope markers for persisted risk questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.core.risk_answer_publication import pending_question_event
from thymira.core.risk_profile_records import (
    CLARIFIABLE_RISK_FIELDS,
    MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
    RiskInterviewError,
)
from thymira.schemas import EventType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.schemas import ActivityProfile, Event, RiskAssessment


_MAX_HISTORY_TEXT = 1_200


@dataclass(frozen=True, slots=True)
class PendingRiskQuestion:
    """One event-backed question a caller may answer for the current profile."""

    event_id: str
    field: str
    question: str
    profile_id: str
    profile_version: int
    question_number: int


def question_from_event(event: Event) -> PendingRiskQuestion:
    """Validate one persisted question event before exposing it to a caller."""
    payload = event.payload
    field = payload.get("field")
    question = payload.get("question")
    profile_id = payload.get("profile_id")
    profile_version = payload.get("profile_version")
    question_number = payload.get("question_number")
    if (
        not isinstance(field, str)
        or field not in CLARIFIABLE_RISK_FIELDS
        or not isinstance(question, str)
        or not question
        or not isinstance(profile_id, str)
        or not profile_id
        or not isinstance(profile_version, int)
        or isinstance(profile_version, bool)
        or profile_version < 1
        or not isinstance(question_number, int)
        or isinstance(question_number, bool)
        or question_number < 1
    ):
        raise RiskInterviewError(f"risk-interview question event {event.event_id} is invalid")
    return PendingRiskQuestion(
        event_id=event.event_id,
        field=field,
        question=question,
        profile_id=profile_id,
        profile_version=profile_version,
        question_number=question_number,
    )


def event_for_question(events: Sequence[Event], event_id: str) -> Event | None:
    """Return the exact persisted question event used to reconstruct a pending turn."""
    return next(
        (
            event
            for event in events
            if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED and event.event_id == event_id
        ),
        None,
    )


def pending_risk_question(
    events: Sequence[Event],
    active_profile: ActivityProfile | None,
    *,
    max_questions: int,
    limit_review_pending: bool,
) -> PendingRiskQuestion | None:
    """Project the one live question only while no bounded interview review is pending."""
    if limit_review_pending:
        return None
    event = pending_question_event(events, active_profile, max_questions=max_questions)
    return question_from_event(event) if event is not None else None


def prior_answered_question_context(
    events: Sequence[Event],
    profile: ActivityProfile,
    current_question: PendingRiskQuestion,
) -> tuple[dict[str, object], ...]:
    """Return bounded prior turns for the same Run, field, and immutable profile version."""
    answers = {
        event.payload.get("question_event_id"): event
        for event in events
        if event.run_id == profile.run_id
        and event.type is EventType.ACTIVITY_PROFILE_ANSWERED
        and isinstance(event.payload.get("question_event_id"), str)
    }
    turns: list[dict[str, object]] = []
    for question in events:
        if (
            question.run_id != profile.run_id
            or question.type is not EventType.ACTIVITY_PROFILE_QUESTIONED
            or question.event_id == current_question.event_id
            or question.payload.get("field") != current_question.field
            or question.payload.get("profile_id") != profile.id
            or question.payload.get("profile_version") != profile.version
        ):
            continue
        answer = answers.get(question.event_id)
        if answer is None:
            continue
        sufficiency = answer.payload.get("sufficiency")
        judgment = (
            {
                "sufficient": sufficiency.get("sufficient"),
                "reason": _bounded_text(sufficiency.get("reason")),
            }
            if isinstance(sufficiency, dict)
            else None
        )
        turns.append(
            {
                "question": _bounded_text(question.payload.get("question")),
                "answer": _bounded_text(answer.payload.get("answer")),
                "sufficiency": judgment,
            }
        )
    return tuple(turns[-MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD:])


def _bounded_text(value: object) -> str | None:
    """Bound arbitrary question and answer text before it enters another model prompt."""
    return value[:_MAX_HISTORY_TEXT] if isinstance(value, str) else None


def risk_uncertainty_marker(event: Event | None) -> dict[str, str | int] | None:
    """Return the validated MIRA assessment scope persisted with a question."""
    if event is None:
        return None
    value = event.payload.get("risk_uncertainty")
    if not isinstance(value, dict):
        return None
    assessment_id = value.get("risk_assessment_id")
    profile_id = value.get("activity_profile_id")
    profile_version = value.get("activity_profile_version")
    question_round = value.get("question_round")
    if (
        not isinstance(assessment_id, str)
        or not isinstance(profile_id, str)
        or isinstance(profile_version, bool)
        or not isinstance(profile_version, int)
        or isinstance(question_round, bool)
        or not isinstance(question_round, int)
    ):
        return None
    return {
        "risk_assessment_id": assessment_id,
        "activity_profile_id": profile_id,
        "activity_profile_version": profile_version,
        "question_round": question_round,
    }


def risk_uncertainty_scope(
    assessment: RiskAssessment, profile: ActivityProfile
) -> dict[str, str | int]:
    """Return the exact immutable risk-assessment/profile binding for one question."""
    return {
        "risk_assessment_id": assessment.id,
        "activity_profile_id": profile.id,
        "activity_profile_version": profile.version,
    }
