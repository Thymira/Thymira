"""Recover interrupted risk answers and fold their durable publication state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.core.risk_profile_records import (
    CLARIFIABLE_RISK_FIELDS,
    MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
    RISK_CLARIFICATION_RESUME_FROM,
    RiskClarificationError,
    updated_profile,
)
from thymira.core.risk_profile_records import (
    field_question_count as _field_question_count,
)
from thymira.core.risk_uncertainty_questions import (
    MAX_RISK_UNCERTAINTY_QUESTIONS,
    uncertainty_question_count,
)
from thymira.events import canonical_json, scrub_credentials_value
from thymira.schemas import ActivityProfile, EventType

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.schemas import Actor, Event
    from thymira.state import LocalRunStore


def clarification_question_text(
    field: str,
    fixed_question: str,
    *,
    recorded_concern: str | None = None,
) -> str:
    """Place a bounded recorded concern before the fixed question without inventing facts."""
    concern = recorded_concern.strip() if isinstance(recorded_concern, str) else ""
    if concern:
        scrubbed = scrub_credentials_value(concern)
        bounded = scrubbed[:600].strip() if isinstance(scrubbed, str) else concern[:600]
        if bounded:
            return f"MIRA recorded this concern about {field}: {bounded}\n\n{fixed_question}"
    return fixed_question


def answer_event_for_question(events: Sequence[Event], question_event_id: str) -> Event | None:
    """Return the latest durable answer event submitted for one exact question."""
    return next(
        (
            event
            for event in reversed(events)
            if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
            and event.payload.get("question_event_id") == question_event_id
        ),
        None,
    )


def validate_answer_retry(answer_event: Event, answer: str, actor: Actor) -> None:
    """Require a retry to repeat the answer and human identity already in the event log."""
    if (
        answer_event.payload.get("answer") != answer.strip()
        or answer_event.payload.get("answered_by") != actor.to_json_dict()
    ):
        raise RiskClarificationError("the stored answer differs from this retry")


def question_owns_information_wait(events: Sequence[Event], question_event_id: str) -> bool:
    """Bind publication retries to the latest information park, not an older answered turn."""
    question = next((event for event in events if event.event_id == question_event_id), None)
    transition = next(
        (event for event in reversed(events) if event.type is EventType.RUN_TRANSITIONED), None
    )
    if question is None or transition is None:
        return False
    if any(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED and event.seq > question.seq
        for event in events
    ):
        return False
    transition_intent = transition.payload.get("intent")
    if transition.payload.get("command") != "wait_for_information" or not isinstance(
        transition_intent, dict
    ):
        return False
    # A crash may leave a durable follow-up before the dispatcher reparks for it. Trace only
    # its recorded answer chain back to this information wait, never an unrelated question.
    while question is not None:
        intent = question.payload.get("intent")
        if isinstance(intent, dict) and intent.get("id") == transition_intent.get("id"):
            return True
        answer = next(
            (
                event
                for event in events
                if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
                and event.event_id == question.payload.get("follow_up_to_answer_event_id")
                and event.seq < question.seq
            ),
            None,
        )
        question = next(
            (
                event
                for event in events
                if answer is not None
                and event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
                and event.event_id == answer.payload.get("question_event_id")
                and event.seq < answer.seq
            ),
            None,
        )
    return False


def sufficient_answer_profile(
    events: Sequence[Event],
    profile: ActivityProfile,
    *,
    question_event_id: str,
    answer: str,
    actor: Actor,
) -> ActivityProfile | None:
    """Replay an exact sufficient answer, publishing its announced immutable profile if absent."""
    answer_event = answer_event_for_question(events, question_event_id)
    if answer_event is None:
        return None
    sufficiency = answer_event.payload.get("sufficiency")
    if not isinstance(sufficiency, dict) or sufficiency.get("sufficient") is not True:
        return None
    validate_answer_retry(answer_event, answer, actor)
    question = next(
        (
            event
            for event in events
            if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
            and event.event_id == question_event_id
        ),
        None,
    )
    profile_id = answer_event.payload.get("profile_id")
    profile_version = answer_event.payload.get("profile_version")
    question_profile_id = question.payload.get("profile_id") if question is not None else None
    question_profile_version = (
        question.payload.get("profile_version") if question is not None else None
    )
    field = answer_event.payload.get("field")
    if (
        question is None
        or not isinstance(profile_id, str)
        or not isinstance(profile_version, int)
        or isinstance(profile_version, bool)
        or not isinstance(question_profile_id, str)
        or not isinstance(question_profile_version, int)
        or isinstance(question_profile_version, bool)
        or profile_version != question_profile_version + 1
        or not isinstance(field, str)
        or field not in CLARIFIABLE_RISK_FIELDS
        or not isinstance(answer_event.payload.get("answer"), str)
    ):
        raise RiskClarificationError("the stored answer no longer matches the current profile")
    published = _published_profile(events, profile_id, profile_version)
    if published is not None:
        if published.id != profile_id or published.version != profile_version:
            raise RiskClarificationError("the stored answer profile is invalid")
        if profile.id != published.id or profile.version != published.version:
            raise RiskClarificationError("the stored answer was superseded by a later profile")
        return profile
    if question_profile_id != profile.id or question_profile_version != profile.version:
        raise RiskClarificationError("the stored answer no longer matches the current profile")
    updated = updated_profile(
        profile, field, answer_event.payload["answer"], profile_id, answer_event
    )
    if updated.id != profile_id or updated.version != profile_version:
        raise RiskClarificationError("the stored answer profile marker does not match its event")
    return updated


def question_answer_is_committed(events: Sequence[Event], question_event_id: str) -> bool:
    """Whether an answer safely closes its question and any required follow-up is durable."""
    answer = answer_event_for_question(events, question_event_id)
    if answer is None:
        return False
    sufficiency = answer.payload.get("sufficiency")
    if isinstance(sufficiency, dict) and sufficiency.get("sufficient") is True:
        return (
            _published_profile(
                events,
                answer.payload.get("profile_id"),
                answer.payload.get("profile_version"),
            )
            is not None
        )
    return any(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and event.payload.get("follow_up_to_answer_event_id") == answer.event_id
        for event in events
    ) or _uncertainty_question_budget_exhausted(events, question_event_id)


def _uncertainty_question_budget_exhausted(events: Sequence[Event], question_event_id: str) -> bool:
    """Close a rejected MIRA question once its Run-wide uncertainty budget is spent."""
    question = next(
        (
            event
            for event in events
            if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
            and event.event_id == question_event_id
        ),
        None,
    )
    return (
        question is not None
        and isinstance(question.payload.get("risk_uncertainty"), dict)
        and uncertainty_question_count(events) >= MAX_RISK_UNCERTAINTY_QUESTIONS
    )


def question_is_current_unanswered(events: Sequence[Event], question: Event) -> bool:
    """Require an idempotent clarification retry to name the current profile's open question."""
    if question_answer_is_committed(events, question.event_id):
        return False
    profile_event = next(
        (event for event in reversed(events) if event.type is EventType.ACTIVITY_PROFILE_RECORDED),
        None,
    )
    profile_value = profile_event.payload.get("activity_profile") if profile_event else None
    if not isinstance(profile_value, dict):
        return False
    profile_id = profile_value.get("id")
    profile_version = profile_value.get("version")
    if (
        question.payload.get("profile_id") != profile_id
        or question.payload.get("profile_version") != profile_version
    ):
        return False
    return not any(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and event.seq > question.seq
        and event.payload.get("profile_id") == profile_id
        and event.payload.get("profile_version") == profile_version
        and not question_answer_is_committed(events, event.event_id)
        for event in events
    )


def question_limit_after_rejected_answer(
    events: Sequence[Event], profile: ActivityProfile, *, max_questions: int
) -> bool:
    """Whether a durable insufficient answer has exhausted its field or Run question bound."""
    total = sum(event.type is EventType.ACTIVITY_PROFILE_QUESTIONED for event in events)
    for question in reversed(events):
        if (
            question.type is not EventType.ACTIVITY_PROFILE_QUESTIONED
            or question.payload.get("profile_id") != profile.id
            or question.payload.get("profile_version") != profile.version
        ):
            continue
        answer = answer_event_for_question(events, question.event_id)
        if answer is None:
            continue
        sufficiency = answer.payload.get("sufficiency")
        if not isinstance(sufficiency, dict) or sufficiency.get("sufficient") is not False:
            continue
        if any(
            event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
            and event.payload.get("follow_up_to_answer_event_id") == answer.event_id
            for event in events
        ):
            continue
        if _uncertainty_question_budget_exhausted(events, question.event_id):
            # MIRA owns this exhausted review even when the field bound is also reached.
            # The closed information wait must resume before its exact uncertainty review.
            return False
        field = question.payload.get("field")
        return total >= max_questions or (
            isinstance(field, str)
            and _field_question_count(events, field) >= MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD
        )
    return False


def recover_interrupted_answer(
    store: LocalRunStore,
    profile: ActivityProfile,
    question: Event,
    *,
    max_questions: int,
    fixed_question: str,
    record_profile: Callable[[ActivityProfile], None],
    append_follow_up: Callable[[Event, str], object],
) -> bool:
    """Finish the durable part of an answer whose process stopped before its next event.

    Sufficient answers publish their announced immutable profile. Insufficient answers append one
    fixed follow-up tied to the recorded judgment unless the bound is reached, in which case the
    resumed interview requests review. Retry never runs the sufficiency model a second time.
    """
    run_id = profile.run_id
    if run_id is None:
        raise RiskClarificationError("the activity profile has no Run identity")
    answer = answer_event_for_question(store.events(run_id), question.event_id)
    if answer is None:
        return False
    sufficiency = answer.payload.get("sufficiency")
    if not isinstance(sufficiency, dict):
        raise RiskClarificationError("the stored answer has no sufficiency judgment")
    if sufficiency.get("sufficient") is True:
        value = answer.payload.get("answer")
        if not isinstance(value, str):
            raise RiskClarificationError("the stored sufficient answer is invalid")
        updated = sufficient_answer_profile(
            store.events(run_id),
            profile,
            question_event_id=question.event_id,
            answer=value,
            actor=answer.actor,
        )
        if updated is not None and updated.id != profile.id:
            record_profile(updated)
        return True
    if any(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and event.payload.get("follow_up_to_answer_event_id") == answer.event_id
        for event in store.events(run_id)
    ):
        return False
    events = store.events(run_id)
    if _uncertainty_question_budget_exhausted(events, question.event_id):
        return True
    field = question.payload.get("field")
    reason = sufficiency.get("reason")
    if not isinstance(field, str) or field not in CLARIFIABLE_RISK_FIELDS:
        raise RiskClarificationError("the stored answer question field is invalid")
    if (
        sum(event.type is EventType.ACTIVITY_PROFILE_QUESTIONED for event in events)
        >= max_questions
        or _field_question_count(events, field) >= MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD
    ):
        return False
    append_follow_up(
        answer,
        clarification_question_text(
            field,
            fixed_question,
            recorded_concern=reason if isinstance(reason, str) else None,
        ),
    )
    return True


def _published_profile(
    events: Sequence[Event], profile_id: object, profile_version: object
) -> ActivityProfile | None:
    """Find the exact immutable profile record announced by a sufficient answer event."""
    for event in reversed(events):
        if event.type is not EventType.ACTIVITY_PROFILE_RECORDED:
            continue
        value = event.payload.get("activity_profile")
        if not isinstance(value, dict):
            continue
        if value.get("id") == profile_id and value.get("version") == profile_version:
            return ActivityProfile.model_validate_json(canonical_json(value))
    return None


def retry_answer_publication(
    store: LocalRunStore,
    profile: ActivityProfile,
    *,
    question_event_id: str | None,
    answer: str,
    actor: Actor,
    max_questions: int,
    fixed_questions: dict[str, str],
    record_profile: Callable[[ActivityProfile], None],
    append_follow_up: Callable[[Event, Event, str], object],
) -> ActivityProfile | None:
    """Complete an exact durable answer retry without spending another model judgment."""
    if profile.run_id is None or question_event_id is None:
        return None
    events = store.events(profile.run_id)
    prior = answer_event_for_question(events, question_event_id)
    if prior is None:
        return None
    if not question_owns_information_wait(events, question_event_id):
        raise RiskClarificationError("the answer belongs to an older question")
    validate_answer_retry(prior, answer, actor)
    recovered = sufficient_answer_profile(
        events, profile, question_event_id=question_event_id, answer=answer, actor=actor
    )
    if recovered is not None:
        if recovered.id != profile.id:
            record_profile(recovered)
        return recovered
    question = next(event for event in events if event.event_id == question_event_id)
    recover_interrupted_answer(
        store,
        profile,
        question,
        max_questions=max_questions,
        fixed_question=fixed_questions[question.payload["field"]],
        record_profile=record_profile,
        append_follow_up=lambda event, text: append_follow_up(question, event, text),
    )
    return profile


def question_has_live_review_transition(events: Sequence[Event], question: Event) -> bool:
    """Keep a review-created question answerable only after its Gate transition committed."""
    marker = question.payload.get("risk_clarification")
    if not isinstance(marker, dict):
        return True
    return any(
        event.type is EventType.RUN_TRANSITIONED
        and event.payload.get("command") == "wait_for_information"
        and event.payload.get("resume_from") == RISK_CLARIFICATION_RESUME_FROM
        and event.payload.get("supersedes_decision_id") == marker.get("expected_decision_id")
        and isinstance(event.payload.get("risk_clarification"), dict)
        and event.payload["risk_clarification"].get("question_event_id") == question.event_id
        for event in events
    )


def pending_question_event(
    events: Sequence[Event], active_profile: ActivityProfile | None, *, max_questions: int
) -> Event | None:
    """Fold committed answers and live clarification transitions into one pending question."""
    if active_profile is not None and question_limit_after_rejected_answer(
        events, active_profile, max_questions=max_questions
    ):
        return None
    answered = {
        question_event_id
        for event in events
        if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
        and isinstance(question_event_id := event.payload.get("question_event_id"), str)
        and question_answer_is_committed(events, question_event_id)
    }
    for event in reversed(events):
        if (
            event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
            and event.event_id not in answered
            and (
                active_profile is None
                or (
                    event.payload.get("profile_id") == active_profile.id
                    and event.payload.get("profile_version") == active_profile.version
                )
            )
            and question_has_live_review_transition(events, event)
        ):
            return event
    return None
