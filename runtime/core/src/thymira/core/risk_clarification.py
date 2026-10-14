"""Bounded, event-backed clarification for a parked Run-level risk review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from thymira.core.execution_review import (
    EXECUTION_START_RESUME_TARGET,
    RISK_UNCERTAINTY_RESUME_TARGET,
    human_approval_outcome,
    parked_review_decision,
    review_resume_target,
)
from thymira.core.risk_answer_publication import (
    clarification_question_text,
    question_is_current_unanswered,
    question_owns_information_wait,
)
from thymira.core.risk_profile_records import (
    CLARIFIABLE_RISK_FIELDS,
    MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
    RISK_CLARIFICATION_RESUME_FROM,
    RiskClarificationError,
)
from thymira.core.risk_profile_records import (
    field_question_count as _field_question_count,
)
from thymira.schemas import (
    ActionIntent,
    ActionKind,
    ActivityProfile,
    Actor,
    ActorKind,
    Decision,
    EventType,
    Run,
    RunCondition,
    RunStage,
    WaitReason,
    new_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.core.control_plane import MiraControlPlane
    from thymira.core.run_state import RunProjection
    from thymira.schemas import Event
    from thymira.state import LocalRunStore


RISK_CLARIFICATION_TARGETS = frozenset(
    {EXECUTION_START_RESUME_TARGET, RISK_UNCERTAINTY_RESUME_TARGET}
)

_FIELD_ALIASES = {
    "intended_use": "purpose",
    "decision_role": "decision_effect",
    "deployment_context": "autonomy",
}


@dataclass(frozen=True, slots=True)
class RiskClarificationRequest:
    """The exact review and one fixed profile field named by a clarification request."""

    field: str
    expected_decision_id: str
    question_event_id: str

    def __post_init__(self) -> None:
        """Reject unbounded fields or incomplete event identities."""
        if self.field not in CLARIFIABLE_RISK_FIELDS:
            raise RiskClarificationError("field must name one of the fixed activity-profile facts")
        if not self.expected_decision_id.strip():
            raise RiskClarificationError("expected_decision_id must not be empty")
        if not self.question_event_id.strip():
            raise RiskClarificationError("question_event_id must not be empty")

    def to_json_dict(self) -> dict[str, str]:
        """Return the canonical transition marker for this clarification."""
        return {
            "field": self.field,
            "expected_decision_id": self.expected_decision_id,
            "question_event_id": self.question_event_id,
        }


def clarification_field_for_missing_information(values: tuple[str, ...]) -> str | None:
    """Resolve an explicit field name or known alias to a human-answerable profile fact."""
    for value in values:
        normalized = "_".join(value.strip().lower().replace("-", " ").split())
        field = _FIELD_ALIASES.get(normalized, normalized)
        if field in CLARIFIABLE_RISK_FIELDS:
            return field
    return None


def clarify_recorded_field[QuestionT](
    store: LocalRunStore,
    control_plane: MiraControlPlane,
    run: Run,
    profile: ActivityProfile,
    *,
    field: str,
    fixed_question: str,
    recorded_concern: str | None,
    expected_decision_id: str | None,
    actor: Actor | None,
    max_questions: int,
    pending_question: Callable[[], QuestionT | None],
    request_question: Callable[[str], QuestionT],
    ensure_waiting: Callable[[QuestionT], None],
    question_from_event: Callable[[Event], QuestionT],
) -> QuestionT | None:
    """Orchestrate one bounded automatic question or exact parked-review clarification."""
    question = clarification_question_text(
        field,
        fixed_question,
        recorded_concern=recorded_concern,
    )
    if expected_decision_id is not None:
        event = request_parked_review_clarification(
            store,
            control_plane,
            run,
            profile,
            field=field,
            expected_decision_id=expected_decision_id,
            question=question,
            max_questions=max_questions,
            requester=actor or Actor.system(),
        )
        return question_from_event(event)

    events = store.events(run.id)
    pending = pending_question()
    if pending is not None:
        ensure_waiting(pending)
        return pending
    if (
        sum(event.type is EventType.ACTIVITY_PROFILE_QUESTIONED for event in events)
        >= max_questions
        or _field_question_count(events, field) >= MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD
    ):
        return None
    pending = request_question(question)
    ensure_waiting(pending)
    return pending


def append_risk_question(
    store: LocalRunStore,
    run: Run,
    profile: ActivityProfile,
    field: str,
    question: str,
    *,
    question_number: int,
    question_source: str,
    requester: Actor | None = None,
    expected_decision_id: str | None = None,
    follow_up_to_answer_event_id: str | None = None,
    risk_uncertainty: dict[str, str | int] | None = None,
) -> Event:
    """Persist one fixed-field question and its Gate request before changing Run state."""
    if field not in CLARIFIABLE_RISK_FIELDS:
        raise RiskClarificationError("field must name one of the fixed activity-profile facts")
    clarification_marker = (
        {"field": field, "expected_decision_id": expected_decision_id}
        if expected_decision_id is not None
        else None
    )
    intent_payload: dict[str, Any] = {
        "field": field,
        "activity_profile_id": profile.id,
        "activity_profile_version": profile.version,
    }
    if clarification_marker is not None:
        intent_payload["risk_clarification"] = clarification_marker
    if risk_uncertainty is not None:
        _validate_risk_uncertainty_marker(profile, risk_uncertainty)
        intent_payload["risk_uncertainty"] = risk_uncertainty
    intent = ActionIntent(
        id=new_id("intent"),
        run_id=run.id,
        requester=requester or Actor.system(),
        action_kind=ActionKind.REQUEST_INFORMATION,
        subject_kind="run",
        subject_id=run.id,
        purpose=question,
        payload=intent_payload,
        idempotency_key=(
            f"risk-interview:{run.id}:{profile.version}:{field}"
            if expected_decision_id is None
            else f"risk-clarification:{run.id}:{expected_decision_id}:{field}"
        )
        + (
            f":follow-up:{follow_up_to_answer_event_id}"
            if follow_up_to_answer_event_id is not None
            else ""
        ),
    )
    payload: dict[str, Any] = {
        "field": field,
        "question": question,
        "profile_id": profile.id,
        "profile_version": profile.version,
        "question_number": question_number,
        "question_source": question_source,
        "intent": intent.model_dump(mode="json"),
    }
    if clarification_marker is not None:
        payload["risk_clarification"] = clarification_marker
    if risk_uncertainty is not None:
        payload["risk_uncertainty"] = risk_uncertainty
    if requester is not None:
        payload["requested_by"] = requester.to_json_dict()
    if follow_up_to_answer_event_id is not None:
        payload["follow_up_to_answer_event_id"] = follow_up_to_answer_event_id
    return store.append(
        run.id,
        EventType.ACTIVITY_PROFILE_QUESTIONED,
        Actor.system(),
        payload,
        expected_version=store.version(run.id),
        subject_id=profile.id,
        producer="thymira.core",
        producer_version="0.1",
    )


def _validate_risk_uncertainty_marker(
    profile: ActivityProfile, marker: dict[str, str | int]
) -> None:
    """Require a risk question to name the exact immutable assessment/profile scope."""
    assessment_id = marker.get("risk_assessment_id")
    profile_id = marker.get("activity_profile_id")
    profile_version = marker.get("activity_profile_version")
    question_round = marker.get("question_round")
    if (
        not isinstance(assessment_id, str)
        or not assessment_id
        or profile_id != profile.id
        or profile_version != profile.version
        or isinstance(profile_version, bool)
        or not isinstance(profile_version, int)
        or isinstance(question_round, bool)
        or not isinstance(question_round, int)
        or question_round < 1
    ):
        raise RiskClarificationError("risk uncertainty question scope does not match its profile")


def request_parked_review_clarification(
    store: LocalRunStore,
    control_plane: MiraControlPlane,
    run: Run,
    profile: ActivityProfile,
    *,
    field: str,
    expected_decision_id: str,
    question: str,
    max_questions: int,
    requester: Actor,
) -> Event:
    """Create or reuse one question, then Gate-authorize replacement of its exact approval park."""
    if requester.kind is not ActorKind.HUMAN or not requester.authenticated:
        raise RiskClarificationError("risk clarification requires an authenticated human requester")
    state = control_plane.controller.current_state(run.id)
    events = store.events(run.id)
    prior_transition = next(
        (
            event
            for event in reversed(events)
            if event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("command") == "wait_for_information"
            and event.payload.get("resume_from") == RISK_CLARIFICATION_RESUME_FROM
            and event.payload.get("supersedes_decision_id") == expected_decision_id
        ),
        None,
    )
    if prior_transition is not None:
        marker = prior_transition.payload.get("risk_clarification")
        if (
            state.condition is RunCondition.WAITING
            and state.wait_reason is WaitReason.INFORMATION
            and isinstance(marker, dict)
            and marker.get("field") == field
        ):
            question_id = marker.get("question_event_id")
            matching = next(
                (item for item in events if item.event_id == question_id),
                None,
            )
            if (
                matching is not None
                and question_is_current_unanswered(events, matching)
                and question_owns_information_wait(events, matching.event_id)
                and matching.payload.get("risk_clarification")
                == {"field": field, "expected_decision_id": expected_decision_id}
            ):
                return matching
        raise RiskClarificationError("this review was already clarified with another field")
    validate_active_risk_review(
        events,
        state,
        run_id=run.id,
        expected_decision_id=expected_decision_id,
    )
    answered_question_ids = {
        event.payload.get("question_event_id")
        for event in events
        if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
    }
    open_clarifications = [
        event
        for event in events
        if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and event.event_id not in answered_question_ids
        and isinstance(event.payload.get("risk_clarification"), dict)
        and event.payload["risk_clarification"].get("expected_decision_id") == expected_decision_id
    ]
    event = next(
        (item for item in reversed(open_clarifications) if item.payload.get("field") == field),
        None,
    )
    if event is None:
        if open_clarifications:
            raise RiskClarificationError(
                "a different clarification is already pending for this review"
            )
        if sum(
            item.type is EventType.ACTIVITY_PROFILE_QUESTIONED for item in events
        ) >= max_questions or _field_question_count(events, field) >= (
            MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD
        ):
            raise RiskClarificationError("the bounded risk-interview question limit was reached")
        event = append_risk_question(
            store,
            run,
            profile,
            field,
            question,
            question_number=1
            + sum(item.type is EventType.ACTIVITY_PROFILE_QUESTIONED for item in events),
            question_source="risk_review_clarification",
            requester=requester,
            expected_decision_id=expected_decision_id,
        )
    request_marker = {"field": field, "expected_decision_id": expected_decision_id}
    if event.payload.get("risk_clarification") != request_marker:
        raise RiskClarificationError("persisted clarification question does not match this request")
    raw_intent = event.payload.get("intent")
    if not isinstance(raw_intent, dict):
        raise RiskClarificationError("persisted clarification question has no Gate request")
    intent = ActionIntent.model_validate(raw_intent)
    intent = intent.model_copy(
        update={
            "payload": {
                **intent.payload,
                "question_event_id": event.event_id,
            }
        }
    )
    result = control_plane.handle(intent)
    if (
        result.state is None
        or result.state.condition is not RunCondition.WAITING
        or result.state.wait_reason is not WaitReason.INFORMATION
    ):
        raise RiskClarificationError("Gate did not authorize the risk clarification park")
    return event


def validate_active_risk_review(
    events: Sequence[Event],
    state: RunProjection,
    *,
    run_id: str,
    expected_decision_id: str,
) -> None:
    """Require the exact unresolved planning review that clarification may supersede."""
    if (
        state.stage is not RunStage.PLANNING
        or state.condition is not RunCondition.WAITING
        or state.wait_reason is not WaitReason.APPROVAL
    ):
        raise RiskClarificationError("risk clarification requires a planning approval park")
    decision = parked_review_decision(events, run_id)
    if (
        decision is None
        or decision.id != expected_decision_id
        or decision.run_id != run_id
        or decision.subject_kind != "run"
        or decision.subject_id != run_id
        or decision.decision is not Decision.REQUIRE_HUMAN_REVIEW
        or review_resume_target(events, decision.id) not in RISK_CLARIFICATION_TARGETS
        or human_approval_outcome(events, decision) is not None
    ):
        raise RiskClarificationError("expected_decision_id is not the active risk-start review")


def request_marker_from_intent(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Read the small request marker embedded in a Gate-authorized ActionIntent."""
    value = payload.get("risk_clarification")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RiskClarificationError("risk clarification intent marker is invalid")
    field = value.get("field")
    decision_id = value.get("expected_decision_id")
    if (
        not isinstance(field, str)
        or field not in CLARIFIABLE_RISK_FIELDS
        or not isinstance(decision_id, str)
        or not decision_id.strip()
        or set(value) != {"field", "expected_decision_id"}
    ):
        raise RiskClarificationError("risk clarification intent marker is invalid")
    return field, decision_id


def transition_request_from_event(payload: dict[str, Any]) -> RiskClarificationRequest | None:
    """Read and cross-check the special transition marker used during authoritative replay."""
    value = payload.get("risk_clarification")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RiskClarificationError("risk clarification transition marker is invalid")
    try:
        request = RiskClarificationRequest(
            field=value["field"],
            expected_decision_id=value["expected_decision_id"],
            question_event_id=value["question_event_id"],
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise RiskClarificationError("risk clarification transition marker is invalid") from exc
    intent = payload.get("intent")
    if not isinstance(intent, dict):
        raise RiskClarificationError("risk clarification transition has no scoped intent")
    intent_payload = intent.get("payload")
    if not isinstance(intent_payload, dict):
        raise RiskClarificationError("risk clarification transition intent payload is invalid")
    marker = request_marker_from_intent(intent_payload)
    if (
        marker != (request.field, request.expected_decision_id)
        or intent_payload.get("question_event_id") != request.question_event_id
        or payload.get("requested_by") != intent.get("requester")
        or payload.get("resume_from") != RISK_CLARIFICATION_RESUME_FROM
        or payload.get("supersedes_decision_id") != request.expected_decision_id
    ):
        raise RiskClarificationError("risk clarification transition marker does not match intent")
    return request


def validate_ordinary_transition(payload: dict[str, Any]) -> None:
    """Forbid clarification markers outside the dedicated controller transition."""
    if transition_request_from_event(payload) is not None:
        raise RiskClarificationError("risk clarification marker requires a wait transition")


def validate_parked_clarification(
    events: Sequence[Event],
    state: RunProjection,
    *,
    run_id: str,
    request: RiskClarificationRequest,
) -> None:
    """Require the same unresolved planning review and earlier unanswered question event."""
    validate_active_risk_review(
        events,
        state,
        run_id=run_id,
        expected_decision_id=request.expected_decision_id,
    )
    decision = parked_review_decision(events, run_id)
    if decision is None or decision.id != request.expected_decision_id:
        raise RiskClarificationError("expected_decision_id is not the active risk-start review")
    answered_question_ids = {
        event.payload.get("question_event_id")
        for event in events
        if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
    }
    question = next(
        (
            event
            for event in events
            if event.event_id == request.question_event_id
            and event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        ),
        None,
    )
    marker = question.payload.get("risk_clarification") if question is not None else None
    question_intent = question.payload.get("intent") if question is not None else None
    if (
        question is None
        or question.event_id in answered_question_ids
        or question.payload.get("field") != request.field
        or not isinstance(marker, dict)
        or marker.get("field") != request.field
        or marker.get("expected_decision_id") != request.expected_decision_id
        or not isinstance(question_intent, dict)
        or question.payload.get("requested_by") != question_intent.get("requester")
        or not isinstance(question_intent.get("requester"), dict)
        or question_intent["requester"].get("kind") != ActorKind.HUMAN.value
        or question_intent["requester"].get("authenticated") is not True
    ):
        raise RiskClarificationError("risk clarification question is missing, stale, or mismatched")


def validate_replayed_clarification(
    events_before_transition: Sequence[Event],
    state: RunProjection,
    *,
    run_id: str,
    payload: dict[str, Any],
) -> RiskClarificationRequest:
    """Validate a special wait transition against prior Gate and question evidence."""
    request = transition_request_from_event(payload)
    if request is None:
        raise RiskClarificationError("missing risk clarification transition marker")
    validate_parked_clarification(
        events_before_transition,
        state,
        run_id=run_id,
        request=request,
    )
    return request
