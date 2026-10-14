"""Select one bounded, profile-grounded question for an uncertain MIRA assessment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thymira.agents.llm.routing import Role
from thymira.core.risk_interview_model import (
    RiskInterviewModelContext,
    activity_fact_definition,
    instrumented_interview_provider,
    prepare_interview_model_call,
    record_interview_structured_failure,
)
from thymira.events import canonical_json, scrub_credentials
from thymira.schemas import ActivityProfile, Actor, EventType, RiskAssessment, Run

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.schemas import Event


MAX_RISK_UNCERTAINTY_QUESTIONS = 3
"""Bound MIRA-uncertainty clarification across all profile revisions in one Run."""

MAX_QUESTION_LENGTH = 600
MAX_PRIOR_QUESTIONS = 12
_MAX_CONTEXT_TEXT = 1_200
_MIN_USEFUL_QUESTION_LENGTH = 10


class RiskUncertaintyQuestionSelection(BaseModel):
    """One allowed fact and its question, or an explicit statement that none would help."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str | None = None
    question: str | None = Field(default=None, max_length=MAX_QUESTION_LENGTH)

    @model_validator(mode="after")
    def validate_pair(self) -> RiskUncertaintyQuestionSelection:
        """Require a complete field/question pair unless the model found no useful question."""
        if (self.field is None) != (self.question is None):
            raise ValueError("field and question must either both be set or both be empty")
        if self.field is not None and not self.field.strip():
            raise ValueError("field must not be empty")
        if self.question is not None and len(self.question.strip()) < _MIN_USEFUL_QUESTION_LENGTH:
            raise ValueError("question must contain at least ten non-whitespace characters")
        return self


_SYSTEM_PROMPT = """\
You select one concise, answerable human question to clarify the exact uncertain risk
assessment or execution classification. Choose only an allowed activity-profile field. Read the
request, assessment, activity profile (including its existing value for any chosen field), each
allowed field's canonical definition, and prior interview questions and answers. Ask only about an
unresolved fact that could materially clarify this assessment. A synthetic missing value such as
`risk_classification` is not itself a human-answerable fact; use the supplied evidence to choose a
concrete allowed profile field when one can help. If all relevant facts are already explicit or no
answer could reduce the uncertainty, return both field and question as null. These questions
clarify inherent risk in the activity; control effectiveness and mitigation measures are assessed
separately. Do not ask for controls or mitigations as a substitute for an inherent-risk fact such
as potential consequences. Do not invent a live decision, credit approval, person, harm, deployment,
or production use. Honor explicit scope statements. Ask at most one question, keep it grounded in
the supplied evidence, and do not reveal hidden reasoning.
"""


def select_risk_uncertainty_question(
    run: Run,
    assessment: RiskAssessment,
    profile: ActivityProfile,
    events: Sequence[Event],
    allowed_fields: tuple[str, ...],
    model_context: RiskInterviewModelContext | None,
) -> tuple[RiskUncertaintyQuestionSelection | None, str]:
    """Ask the routed model for one exact-scope question, returning ``None`` on unusable output."""
    if (
        assessment.run_id != run.id
        or assessment.activity_profile_id != profile.id
        or assessment.activity_profile_version != profile.version
        or profile.run_id != run.id
    ):
        return None, "scope_mismatch"
    if model_context is None or not allowed_fields:
        return None, "model_unavailable" if model_context is None else "no_allowed_fields"

    current_request = run.conversation.requested_work if run.conversation else run.prompt
    prompt = canonical_json(
        {
            "risk_assessment": assessment.model_dump(mode="json"),
            "current_request": current_request,
            "current_activity_profile": profile.model_dump(mode="json"),
            "allowed_fields": list(allowed_fields),
            "allowed_field_definitions": {
                field: activity_fact_definition(field) for field in allowed_fields
            },
            "prior_interview_questions_and_answers": _prior_question_answers(events),
        }
    )
    return _select_question(prompt, allowed_fields, model_context, role=Role.MIRA)


def select_classification_question(
    run: Run,
    profile: ActivityProfile,
    classification: Event,
    events: Sequence[Event],
    allowed_fields: tuple[str, ...],
    model_context: RiskInterviewModelContext,
) -> tuple[RiskUncertaintyQuestionSelection | None, str]:
    """Select a question for a persisted execution classifier's exact profile version."""
    if (
        classification.type is not EventType.RISK_CLASSIFIED
        or classification.run_id != run.id
        or profile.run_id != run.id
        or classification.payload.get("activity_profile_id") != profile.id
        or classification.payload.get("activity_profile_version") != profile.version
    ):
        return None, "scope_mismatch"
    if not allowed_fields:
        return None, "no_allowed_fields"
    prompt = canonical_json(
        {
            "execution_classification": classification.payload,
            "classification_event_id": classification.event_id,
            "current_request": run.conversation.requested_work if run.conversation else run.prompt,
            "current_activity_profile": profile.model_dump(mode="json"),
            "allowed_fields": list(allowed_fields),
            "allowed_field_definitions": {
                field: activity_fact_definition(field) for field in allowed_fields
            },
            "prior_interview_questions_and_answers": _prior_question_answers(events),
        }
    )
    return _select_question(prompt, allowed_fields, model_context, role=Role.AGENT)


def _select_question(
    prompt: str,
    allowed_fields: tuple[str, ...],
    model_context: RiskInterviewModelContext,
    *,
    role: Role,
) -> tuple[RiskUncertaintyQuestionSelection | None, str]:
    """Apply the same routed provider, evidence recording and output bounds to both classifiers."""
    try:
        choice = prepare_interview_model_call(model_context, "classify", role=role)
        if model_context.provider.model != choice.model:
            model_context.event_log.append(
                EventType.MODEL_ROUTE_DENIED,
                Actor.system(),
                {
                    "route": model_context.provider.model,
                    "selected_route": choice.model,
                    "reason": "provider_route_differs_from_selection",
                    "policy_sha256": model_context.route_policy.sha256
                    if model_context.route_policy is not None
                    else None,
                },
            )
            return None, "provider_route_mismatch"
        parsed, response = instrumented_interview_provider(model_context).complete_structured(
            scrub_credentials(prompt),
            schema=RiskUncertaintyQuestionSelection,
            system=scrub_credentials(_SYSTEM_PROMPT),
        )
        if model_context.record_model_usage is not None:
            model_context.record_model_usage(response)
        selection = RiskUncertaintyQuestionSelection.model_validate(parsed.model_dump())
    except Exception as exc:  # noqa: BLE001  # an optional question must fail closed to review
        record_interview_structured_failure(model_context, exc)
        return None, "model_unavailable_or_invalid"

    if selection.field is None or selection.question is None:
        result = None, "no_useful_question"
    else:
        question = selection.question.strip()
        if selection.field not in allowed_fields or len(question) < _MIN_USEFUL_QUESTION_LENGTH:
            result = None, "invalid_selection"
        else:
            result = selection.model_copy(update={"question": question}), "model"
    return result


def uncertainty_question_count(events: Sequence[Event]) -> int:
    """Count persisted questions that belong to MIRA-uncertainty clarification rounds."""
    return sum(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and isinstance(event.payload.get("risk_uncertainty"), dict)
        for event in events
    )


def uncertainty_question_rounds(events: Sequence[Event]) -> tuple[dict[str, object], ...]:
    """Return compact history for an explicit, bounded risk-uncertainty review request."""
    answers = {
        event.payload.get("question_event_id"): event
        for event in events
        if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
        and isinstance(event.payload.get("question_event_id"), str)
    }
    rounds: list[dict[str, object]] = []
    for question_event in events:
        if question_event.type is not EventType.ACTIVITY_PROFILE_QUESTIONED:
            continue
        marker = question_event.payload.get("risk_uncertainty")
        if not isinstance(marker, dict):
            continue
        answer_event = answers.get(question_event.event_id)
        answer: dict[str, object] | None = None
        if answer_event is not None:
            sufficiency = answer_event.payload.get("sufficiency")
            answer = {
                "event_id": answer_event.event_id,
                "answer": _bounded_text(answer_event.payload.get("answer")),
                "sufficiency": sufficiency if isinstance(sufficiency, dict) else None,
            }
        rounds.append(
            {
                "question_event_id": question_event.event_id,
                "risk_assessment_id": marker.get("risk_assessment_id"),
                "activity_profile_id": marker.get("activity_profile_id"),
                "activity_profile_version": marker.get("activity_profile_version"),
                "field": question_event.payload.get("field"),
                "question": _bounded_text(question_event.payload.get("question")),
                "answer": answer,
            }
        )
    return tuple(rounds)


def _prior_question_answers(events: Sequence[Event]) -> list[dict[str, object]]:
    """Render the latest answered interview turns without unbounded user text."""
    answers = {
        event.payload.get("question_event_id"): event
        for event in events
        if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
        and isinstance(event.payload.get("question_event_id"), str)
    }
    history: list[dict[str, object]] = []
    for question_event in events:
        if question_event.type is not EventType.ACTIVITY_PROFILE_QUESTIONED:
            continue
        answer_event = answers.get(question_event.event_id)
        if answer_event is None:
            continue
        sufficiency = answer_event.payload.get("sufficiency")
        history.append(
            {
                "field": question_event.payload.get("field"),
                "question": _bounded_text(question_event.payload.get("question")),
                "answer": _bounded_text(answer_event.payload.get("answer")),
                "sufficiency": sufficiency if isinstance(sufficiency, dict) else None,
            }
        )
    return history[-MAX_PRIOR_QUESTIONS:]


def _bounded_text(value: object) -> str | None:
    """Keep persisted human text useful to the next model while limiting prompt growth."""
    return value[:_MAX_CONTEXT_TEXT] if isinstance(value, str) else None


__all__ = [
    "MAX_RISK_UNCERTAINTY_QUESTIONS",
    "RiskUncertaintyQuestionSelection",
    "select_risk_uncertainty_question",
    "uncertainty_question_count",
    "uncertainty_question_rounds",
]
