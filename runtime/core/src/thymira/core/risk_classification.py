"""Persist execution risk classification and request bounded, actionable human clarification."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.agents import classify_risk
from thymira.agents.risk import CLASSIFICATION_FAILED_MARKER
from thymira.core.risk_answer_publication import pending_question_event
from thymira.core.risk_clarification import append_risk_question
from thymira.core.risk_interview_model import has_declared_value
from thymira.core.risk_profile_records import (
    CLARIFIABLE_RISK_FIELDS,
    MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
    field_question_count,
    recorded_assessment,
    recorded_classification,
)
from thymira.core.risk_review_requests import ensure_question_wait
from thymira.core.risk_uncertainty_questions import (
    MAX_RISK_UNCERTAINTY_QUESTIONS,
    RiskUncertaintyQuestionSelection,
    select_classification_question,
)
from thymira.schemas import Actor, EventType, RunCondition

if TYPE_CHECKING:
    from thymira.agents import RiskFacts
    from thymira.core.control_plane import MiraControlPlane
    from thymira.core.risk_interview_model import RiskInterviewModelContext
    from thymira.policies import RiskProfile
    from thymira.schemas import ActivityProfile, Run
    from thymira.state import LocalRunStore

_QUESTION_SOURCE = "execution_risk_uncertainty:"
_CLASSIFICATION_FIELD_ALIASES = {
    "intended_use": "purpose",
    "decision_role": "decision_effect",
    "deployment_context": "autonomy",
}


def classification_question_fields(risk: RiskProfile, profile: ActivityProfile) -> tuple[str, ...]:
    """Return actual profile gaps claimed by a known classification.

    Unknown and failed classifications keep their existing bounded clarification path. For a
    known classification, model-reported names are only question candidates: a field already
    declared on the immutable profile is not turned into a repeated question.
    """
    if (
        risk.risk_level == "unknown"
        or risk.activity_category == "unknown"
        or CLASSIFICATION_FAILED_MARKER in risk.missing_information
    ):
        return CLARIFIABLE_RISK_FIELDS
    claimed = {
        _CLASSIFICATION_FIELD_ALIASES.get(field, field) for field in risk.missing_information
    }
    return tuple(
        field
        for field in CLARIFIABLE_RISK_FIELDS
        if field in claimed and not has_declared_value(getattr(profile, field))
    )


def classify_activity_risk(
    run: Run,
    profile: ActivityProfile,
    facts: RiskFacts,
    model_context: RiskInterviewModelContext,
    store: LocalRunStore,
    control_plane: MiraControlPlane,
    *,
    max_questions: int,
) -> RiskProfile:
    """Reuse one classification and clarify concrete missing facts before execution review."""
    risk = recorded_classification(store, run.id, profile)
    if risk is None:
        assessment = recorded_assessment(store, run.id, profile)
        risk = classify_risk(
            facts,
            model_context.provider,
            model_context.event_log,
            run_risk_assessment=assessment,
            before_model_selection=model_context.before_model_selection,
            before_model_call=model_context.before_model_call,
            record_model_usage=model_context.record_model_usage,
            route_policy=model_context.route_policy,
            request_ledger=model_context.request_ledger,
        )
        model_context.event_log.append(
            EventType.RISK_CLASSIFIED,
            Actor.system(),
            {
                "risk_profile": risk.model_dump(mode="json"),
                "activity_profile_id": profile.id,
                "activity_profile_version": profile.version,
                "mira_risk_assessment_id": assessment.id if assessment is not None else None,
            },
            subject_id=profile.id,
            producer="thymira.core",
            producer_version="0.1",
        )
    question_fields = classification_question_fields(risk, profile)
    if risk.needs_human_review and question_fields:
        _clarify_classification(
            run,
            profile,
            model_context,
            store,
            control_plane,
            question_fields=question_fields,
            max_questions=max_questions,
        )
    return risk


def _clarify_classification(
    run: Run,
    profile: ActivityProfile,
    model_context: RiskInterviewModelContext,
    store: LocalRunStore,
    control_plane: MiraControlPlane,
    *,
    question_fields: tuple[str, ...],
    max_questions: int,
) -> None:
    """Persist at most one useful question per current classification; never authorize work."""
    events = store.events(run.id)
    pending = pending_question_event(events, profile, max_questions=max_questions)
    current = control_plane.controller.current_state(run.id)
    if current.condition not in (RunCondition.ACTIVE, RunCondition.WAITING):
        return
    if pending is not None:
        if current.condition is RunCondition.ACTIVE:
            ensure_question_wait(store, control_plane, run.id, pending.event_id)
        return
    if current.condition is RunCondition.WAITING:
        return
    questions = [event for event in events if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED]
    if (
        len(questions) >= max_questions
        or sum(
            str(event.payload.get("question_source", "")).startswith(_QUESTION_SOURCE)
            for event in questions
        )
        >= MAX_RISK_UNCERTAINTY_QUESTIONS
    ):
        return
    classification = next(
        event
        for event in reversed(events)
        if event.type is EventType.RISK_CLASSIFIED
        and event.payload.get("activity_profile_id") == profile.id
        and event.payload.get("activity_profile_version") == profile.version
    )
    allowed = tuple(
        field
        for field in question_fields
        if field_question_count(events, field) < MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD
    )
    selection_event = next(
        (
            event
            for event in reversed(events)
            if event.type is EventType.AGENT_MESSAGE
            and event.producer == "thymira.core"
            and event.actor == Actor.system()
            and event.payload.get("kind") == "risk.classification_clarification"
            and event.payload.get("classification_event_id") == classification.event_id
        ),
        None,
    )
    if selection_event is None:
        selection, source = select_classification_question(
            run, profile, classification, events, allowed, model_context
        )
        selection_event = model_context.event_log.append(
            EventType.AGENT_MESSAGE,
            Actor.system(),
            {
                "kind": "risk.classification_clarification",
                "classification_event_id": classification.event_id,
                "activity_profile_id": profile.id,
                "activity_profile_version": profile.version,
                "selection": selection.model_dump(mode="json") if selection is not None else None,
                "selection_source": source,
            },
            producer="thymira.core",
        )
    stored_selection = selection_event.payload.get("selection")
    selection = (
        RiskUncertaintyQuestionSelection.model_validate(stored_selection)
        if isinstance(stored_selection, dict)
        else None
    )
    if selection is None or selection.field is None or selection.question is None:
        return
    if selection.field not in allowed:
        return
    question = append_risk_question(
        store,
        run,
        profile,
        selection.field,
        selection.question,
        question_number=len(questions) + 1,
        question_source=f"{_QUESTION_SOURCE}{classification.event_id}",
    )
    ensure_question_wait(store, control_plane, run.id, question.event_id)
