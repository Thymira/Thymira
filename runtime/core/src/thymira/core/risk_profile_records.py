"""Immutable risk-profile records and their event-backed revisions."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from thymira.core.risk_interview_model import normalise
from thymira.events import canonical_json
from thymira.policies import RiskProfile
from thymira.schemas import ActivityProfile, Actor, EventType, Evidence, RiskAssessment

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.core.risk_interview_model import SufficiencyJudgment
    from thymira.schemas import Event, Run
    from thymira.state import LocalRunStore

CLARIFIABLE_RISK_FIELDS: tuple[str, ...] = (
    "purpose",
    "affected_population",
    "decision_effect",
    "autonomy",
    "human_oversight",
    "jurisdiction",
    "data_categories",
    "sensitive_attributes",
    "potential_consequences",
)
"""The fixed profile fields that a human may clarify while risk review is parked."""

RISK_CLARIFICATION_RESUME_FROM = "risk_clarification"
MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD = 3


class RiskInterviewError(ValueError):
    """Base error for an invalid activity-profile interview action."""


class RiskInterviewNotPendingError(RiskInterviewError):
    """Raised when a submitted answer does not target a current question."""


class RiskClarificationError(ValueError):
    """Raised when a request does not target a current bounded risk review."""


def append_profile_answer(
    store: LocalRunStore,
    run: Run,
    *,
    field: str,
    question: str,
    question_event_id: str | None,
    answer: str,
    actor: Actor,
    profile_id: str,
    profile_version: int,
    answer_source: str,
    source_ref: str | None,
    judgment: SufficiencyJudgment,
    judgment_source: str,
) -> Event:
    """Append one answer fact, whether it revises the profile or requests clarification."""
    return store.append(
        run.id,
        EventType.ACTIVITY_PROFILE_ANSWERED,
        actor,
        {
            "field": field,
            "question": question,
            "question_event_id": question_event_id,
            "answer": answer,
            "answered_by": actor.model_dump(mode="json"),
            "answer_source": answer_source,
            "source_ref": source_ref,
            "sufficiency": judgment.model_dump(mode="json"),
            "sufficiency_source": judgment_source,
            "profile_id": profile_id,
            "profile_version": profile_version,
        },
        expected_version=store.version(run.id),
        subject_id=profile_id,
        producer="thymira.core",
        producer_version="0.1",
    )


def recorded_profile(store: LocalRunStore, run_id: str) -> ActivityProfile | None:
    """Return the latest validated activity-profile record without creating one."""
    for event in reversed(store.events(run_id)):
        if event.type is not EventType.ACTIVITY_PROFILE_RECORDED:
            continue
        try:
            profile = ActivityProfile.model_validate_json(
                canonical_json(event.payload["activity_profile"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"run {run_id}: invalid activity_profile.recorded event {event.event_id}"
            ) from exc
        if profile.run_id != run_id:
            raise ValueError(f"run {run_id}: activity profile {profile.id} belongs to another Run")
        return profile
    return None


def record_profile(store: LocalRunStore, profile: ActivityProfile) -> None:
    """Append the exact immutable profile that subsequent evidence refers to."""
    assert profile.run_id is not None  # noqa: S101  # validated by ActivityProfile.
    store.append(
        profile.run_id,
        EventType.ACTIVITY_PROFILE_RECORDED,
        Actor.system(),
        {"activity_profile": profile.model_dump(mode="json")},
        expected_version=store.version(profile.run_id),
        subject_id=profile.id,
        producer="thymira.core",
        producer_version="0.1",
    )


def recorded_classification(
    store: LocalRunStore, run_id: str, profile: ActivityProfile
) -> RiskProfile | None:
    """Return an already-recorded classification for the exact immutable profile."""
    for event in reversed(store.events(run_id)):
        if event.type is not EventType.RISK_CLASSIFIED:
            continue
        if (
            event.payload.get("activity_profile_id") != profile.id
            or event.payload.get("activity_profile_version") != profile.version
        ):
            continue
        try:
            return RiskProfile.model_validate(event.payload["risk_profile"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"run {run_id}: invalid risk.classified event {event.event_id}"
            ) from exc
    return None


def updated_profile(
    profile: ActivityProfile,
    field: str,
    answer: str,
    profile_id: str,
    answer_event: Event,
) -> ActivityProfile:
    """Return the next immutable profile version anchored to the answer event."""
    if field not in CLARIFIABLE_RISK_FIELDS:
        raise RiskInterviewError(f"unknown activity-profile field {field!r}")
    value: str | tuple[str, ...]
    if field in {"data_categories", "sensitive_attributes", "potential_consequences"}:
        value = _split_values(answer)
    else:
        value = answer
    evidence = Evidence(kind="event", ref=f"seq:{answer_event.seq}", sha256=answer_event.hash)
    data = profile.model_dump(mode="python")
    data.update(
        {
            "id": profile_id,
            "version": profile.version + 1,
            "supersedes_id": profile.id,
            "evidence_refs": (*profile.evidence_refs, evidence),
            field: value,
        }
    )
    return ActivityProfile.model_validate(data)


def _split_values(answer: str) -> tuple[str, ...]:
    """Preserve a declared absence or split a plain list without inventing categories."""
    if normalise(answer) in {"none", "no", "ninguno", "ninguna", "ningun"}:
        return ()
    return tuple(part.strip() for part in re.split(r"[,;\n]", answer) if part.strip())


def field_question_count(events: Sequence[Event], field: str) -> int:
    """Count durable questions against the same fixed profile fact."""
    return sum(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED and event.payload.get("field") == field
        for event in events
    )


def recorded_assessment(
    store: LocalRunStore, run_id: str, profile: ActivityProfile
) -> RiskAssessment | None:
    """Return MIRA's persisted inherent-risk assessment for exactly this profile version."""
    for event in reversed(store.events(run_id)):
        if event.type is not EventType.RISK_ASSESSMENT_RECORDED:
            continue
        try:
            assessment = RiskAssessment.model_validate_json(
                canonical_json(event.payload["risk_assessment"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"run {run_id}: invalid risk_assessment.recorded event {event.event_id}"
            ) from exc
        if (
            assessment.activity_profile_id == profile.id
            and assessment.activity_profile_version == profile.version
        ):
            return assessment
    return None
