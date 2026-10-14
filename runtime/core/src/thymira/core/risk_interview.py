"""Minimal, event-backed intake for the activity profile that governs one Run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.agents import RiskFacts
from thymira.core.risk_answer_publication import (
    question_limit_after_rejected_answer,
    retry_answer_publication,
)
from thymira.core.risk_answer_publication import (
    recover_interrupted_answer as finish_interrupted_answer,
)
from thymira.core.risk_clarification import append_risk_question, clarify_recorded_field
from thymira.core.risk_classification import classify_activity_risk
from thymira.core.risk_interview_model import (
    RiskInterviewModelContext,
    SufficiencyJudgment,
    extract_facts,
    generate_follow_up,
    generate_question,
    has_declared_value,
    is_missing_text,
    judge_sufficiency,
    normalise,
)
from thymira.core.risk_profile_records import (
    MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD,
    RiskInterviewError,
    RiskInterviewNotPendingError,
    append_profile_answer,
    record_profile,
    recorded_assessment,
    recorded_profile,
    updated_profile,
)
from thymira.core.risk_profile_records import (
    field_question_count as _field_question_count,
)
from thymira.core.risk_question_records import (
    PendingRiskQuestion,
    prior_answered_question_context,
)
from thymira.core.risk_question_records import (
    event_for_question as _event_for_question,
)
from thymira.core.risk_question_records import (
    pending_risk_question as _pending_risk_question,
)
from thymira.core.risk_question_records import (
    question_from_event as _question_from_event,
)
from thymira.core.risk_question_records import (
    risk_uncertainty_marker as _risk_uncertainty_marker,
)
from thymira.core.risk_question_records import (
    risk_uncertainty_scope as _risk_uncertainty_scope,
)
from thymira.core.risk_review_requests import (
    ensure_question_wait,
    interview_limit_answered,
    interview_limit_approved,
    interview_limit_decision,
    request_interview_limit_review,
)
from thymira.core.risk_uncertainty_questions import (
    MAX_RISK_UNCERTAINTY_QUESTIONS,
    select_risk_uncertainty_question,
    uncertainty_question_count,
)
from thymira.schemas import (
    ActivityProfile,
    Actor,
    EventType,
    RiskAssessment,
    new_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.agents.llm import LLMProvider
    from thymira.agents.llm.base import LLMResponse
    from thymira.agents.llm.routing import ModelChoice
    from thymira.agents.request_ledger import RequestLedger
    from thymira.core.control_plane import MiraControlPlane
    from thymira.events import EventLog
    from thymira.policies import RiskProfile
    from thymira.schemas import ModelRoutePolicy, Run
    from thymira.state import LocalRunStore


INTERVIEW_FIELDS: tuple[str, ...] = (
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
"""The activity facts required before THY may begin work."""

ANSWERABLE_RISK_FIELDS: tuple[str, ...] = (
    *INTERVIEW_FIELDS,
    "intended_use",
    "decision_role",
    "deployment_context",
)
"""The only names the classifier may keep as missing information: the interview's nine facts
plus the three downstream-context aliases the classifier prompt already presents for them."""

MAX_INTERVIEW_QUESTIONS = 12
"""Bound repeated unanswered facts before escalating the Run to a human review."""

MAX_INTERVIEW_QUESTIONS_PER_FIELD = MAX_RISK_CLARIFICATION_QUESTIONS_PER_FIELD
"""Bound repeated model-rejected human answers for one fact before review."""

_QUESTIONS: dict[str, str] = {
    "purpose": "What is the concrete purpose of this activity?",
    "affected_population": (
        "Which natural persons could be affected? Answer 'none' only when no natural person can "
        "be affected."
    ),
    "decision_effect": (
        "How can this activity influence a decision about a person or organisation?"
    ),
    "autonomy": "What can the system do autonomously, without a human decision?",
    "human_oversight": "What human oversight can review, change, or stop the activity?",
    "jurisdiction": "Which jurisdiction governs this activity?",
    "data_categories": "Which categories of data will be processed? List them, or answer 'none'.",
    "sensitive_attributes": (
        "Which sensitive attributes will be processed? List them, or answer 'none'."
    ),
    "potential_consequences": (
        "What potential consequences could an incorrect result have? List them, or answer 'none'."
    ),
}

_NO_VALUE_MARKERS = frozenset({"none", "no", "ninguno", "ninguna", "ningun"})
_CONTEXT_DOCUMENT_REF = ".thymira/context.md"
_PROMPT_REF = "run.prompt"


@dataclass(frozen=True, slots=True)
class RiskInterviewStatus:
    """The current profile and exactly one pending question, when one exists."""

    profile: ActivityProfile
    pending_question: PendingRiskQuestion | None
    requires_human_review: bool


class RiskInterviewService:
    """Create, revise, and classify one immutable Run activity profile from its event log."""

    def __init__(
        self,
        store: LocalRunStore,
        control_plane: MiraControlPlane,
        *,
        max_questions: int = MAX_INTERVIEW_QUESTIONS,
        model_context_factory: Callable[[str], RiskInterviewModelContext | None] | None = None,
    ) -> None:
        if max_questions < 1:
            raise ValueError("max_questions must be at least 1")
        self._store = store
        self._control_plane = control_plane
        self._max_questions = max_questions
        self._model_context_factory = model_context_factory

    def begin(
        self,
        run: Run,
        initial_profile: ActivityProfile,
        *,
        context_document: str | None = None,
        provider: LLMProvider | None = None,
        event_log: EventLog | None = None,
        before_model_selection: Callable[[], None] | None = None,
        before_model_call: Callable[[ModelChoice], None] | None = None,
        record_model_usage: Callable[[LLMResponse], None] | None = None,
        request_ledger: RequestLedger | None = None,
        route_policy: ModelRoutePolicy | None = None,
    ) -> RiskInterviewStatus:
        """Persist the profile, fill it from known context, and ask one missing fact if needed.

        The fixed interview field list remains authoritative. Before the first question, one
        bounded model call may state the facts that the Run prompt and the project context
        document settle with certainty; every other field is asked to a human. A failed or
        absent model leaves every field for the ordinary interview.
        """
        profile = self._profile(run, initial_profile)
        model_context = self._resolve_model_context(
            run,
            provider=provider,
            event_log=event_log,
            before_model_selection=before_model_selection,
            before_model_call=before_model_call,
            record_model_usage=record_model_usage,
            request_ledger=request_ledger,
            route_policy=route_policy,
        )
        if profile.version == 1 and self._question_count(run.id) == 0:
            profile = self._apply_context(run, profile, context_document, model_context)
        pending = self._pending_question(run.id, profile)
        if pending is not None:
            self._ensure_waiting_for_information(run.id, pending)
            return RiskInterviewStatus(profile, pending, False)
        if self._limit_review_resolved(run.id):
            return RiskInterviewStatus(profile, None, False)
        missing = _missing_fields(profile)
        if question_limit_after_rejected_answer(
            self._store.events(run.id), profile, max_questions=self._max_questions
        ) or (
            missing
            and (
                self._question_count(run.id) >= self._max_questions
                or _field_question_count(self._store.events(run.id), missing[0])
                >= MAX_INTERVIEW_QUESTIONS_PER_FIELD
            )
        ):
            self._escalate_question_limit(run.id)
            return RiskInterviewStatus(profile, None, True)
        if self._limit_review_pending(run.id):
            return RiskInterviewStatus(profile, None, True)
        if not missing:
            return RiskInterviewStatus(profile, None, False)
        field = missing[0]

        question, question_source = generate_question(
            field,
            _QUESTIONS[field],
            INTERVIEW_FIELDS,
            profile,
            model_context,
        )
        pending = self._request_question(
            run,
            profile,
            field,
            question,
            question_source=question_source,
        )
        self._ensure_waiting_for_information(run.id, pending)
        return RiskInterviewStatus(profile, pending, False)

    def status(self, run: Run, initial_profile: ActivityProfile) -> RiskInterviewStatus:
        """Return the persisted profile and its question without mutating the Run."""
        profile = self.current_profile(run, initial_profile)
        missing = _missing_fields(profile)
        pending = self._pending_question(run.id)
        field_limit_reached = (
            bool(missing)
            and _field_question_count(self._store.events(run.id), missing[0])
            >= MAX_INTERVIEW_QUESTIONS_PER_FIELD
        )
        rejected_answer_limit = question_limit_after_rejected_answer(
            self._store.events(run.id), profile, max_questions=self._max_questions
        )
        return RiskInterviewStatus(
            profile,
            pending,
            pending is None
            and not self._limit_review_answered(run.id)
            and (
                self._limit_review_pending(run.id)
                or rejected_answer_limit
                or (
                    bool(missing)
                    and (self._question_count(run.id) >= self._max_questions or field_limit_reached)
                )
            ),
        )

    def has_pending_question(self, run_id: str) -> bool:
        """Return whether an unanswered fixed-field question exists for this Run.

        `resume` uses this to distinguish an interview question, including a bounded question
        created from MIRA's recorded uncertainty, from an information park that has no question
        event. Only an answer tied to the current question event may clear the former.
        """
        return self._pending_question(run_id) is not None

    def recover_answer_publication(self, run: Run) -> bool:
        """Publish a durable answer's missing profile or follow-up before any plain resume."""
        profile = self._recorded_profile(run.id)
        if profile is None:
            return False
        question = self._pending_question(run.id, profile)
        if question is None:
            return False
        event = next(
            (
                item
                for item in self._store.events(run.id)
                if item.type is EventType.ACTIVITY_PROFILE_QUESTIONED
                and item.event_id == question.event_id
            ),
            None,
        )
        if event is None:
            raise RiskInterviewNotPendingError("the pending question evidence is missing")
        return finish_interrupted_answer(
            self._store,
            profile,
            event,
            max_questions=self._max_questions,
            fixed_question=_QUESTIONS[question.field],
            record_profile=self._record_profile,
            append_follow_up=lambda answer_event, text: self._request_question(
                run,
                profile,
                question.field,
                text,
                question_source="risk_answer_recovery",
                follow_up_to_answer_event_id=answer_event.event_id,
                risk_uncertainty=_risk_uncertainty_marker(event),
            ),
        )

    def current_profile(self, run: Run, initial_profile: ActivityProfile) -> ActivityProfile:
        """Return the latest profile or the supplied initial profile without recording either."""
        if initial_profile.run_id != run.id:
            raise ValueError("initial activity profile must belong to the Run")
        return self._recorded_profile(run.id) or initial_profile

    def answer(
        self,
        run: Run,
        *,
        answer: str,
        actor: Actor,
        expected_question_id: str | None = None,
        provider: LLMProvider | None = None,
        event_log: EventLog | None = None,
        before_model_selection: Callable[[], None] | None = None,
        before_model_call: Callable[[ModelChoice], None] | None = None,
        record_model_usage: Callable[[LLMResponse], None] | None = None,
        request_ledger: RequestLedger | None = None,
        route_policy: ModelRoutePolicy | None = None,
    ) -> ActivityProfile:
        """Judge and persist one answer, returning the current immutable profile version."""
        value = answer.strip()
        if not value:
            raise ValueError("an interview answer must not be empty")
        profile = self._recorded_profile(run.id)
        if profile is None:
            raise RiskInterviewNotPendingError(
                f"run {run.id!r} has no persisted activity profile to update"
            )
        question = self._pending_question(run.id, profile)
        recovered = retry_answer_publication(
            self._store,
            profile,
            question_event_id=expected_question_id or (question.event_id if question else None),
            answer=value,
            actor=actor,
            max_questions=self._max_questions,
            fixed_questions=_QUESTIONS,
            record_profile=self._record_profile,
            append_follow_up=lambda question_event, answer_event, text: self._request_question(
                run,
                profile,
                question_event.payload["field"],
                text,
                question_source="risk_answer_recovery",
                follow_up_to_answer_event_id=answer_event.event_id,
                risk_uncertainty=_risk_uncertainty_marker(question_event),
            ),
        )
        if recovered is not None:
            return recovered
        if question is None:
            message = f"run {run.id!r} has no pending risk-interview question"
            raise RiskInterviewNotPendingError(message)
        if expected_question_id is not None and question.event_id != expected_question_id:
            raise RiskInterviewNotPendingError(
                f"run {run.id!r} pending question changed before the answer was submitted"
            )
        if (
            isinstance(
                next(
                    (
                        event.payload.get("risk_clarification")
                        for event in self._store.events(run.id)
                        if event.event_id == question.event_id
                    ),
                    None,
                ),
                dict,
            )
            and expected_question_id is None
        ):
            raise RiskInterviewNotPendingError(
                "risk clarification answer must name its exact question event"
            )
        if question.profile_id != profile.id or question.profile_version != profile.version:
            raise RiskInterviewNotPendingError(
                f"run {run.id!r} question does not match its current activity profile"
            )

        model_context = self._resolve_model_context(
            run,
            provider=provider,
            event_log=event_log,
            before_model_selection=before_model_selection,
            before_model_call=before_model_call,
            record_model_usage=record_model_usage,
            request_ledger=request_ledger,
            route_policy=route_policy,
        )
        judgment, judgment_source = judge_sufficiency(
            question.field,
            question.question,
            value,
            INTERVIEW_FIELDS,
            profile,
            model_context,
            current_request=run.conversation.requested_work if run.conversation else run.prompt,
        )
        if judgment is None:
            # Existing callers without a provider retain the original answer semantics. A human
            # answer must never become impossible merely because the optional evaluator is down.
            judgment = SufficiencyJudgment(
                sufficient=True,
                reason="No interview evaluator was available; the live human answer was retained.",
            )
            judgment_source = "fallback"

        if judgment.sufficient:
            profile_id = new_id("profile")
            answer_event = append_profile_answer(
                self._store,
                run,
                field=question.field,
                question=question.question,
                question_event_id=question.event_id,
                answer=value,
                actor=actor,
                profile_id=profile_id,
                profile_version=profile.version + 1,
                answer_source="live_human",
                source_ref=None,
                judgment=judgment,
                judgment_source=judgment_source,
            )
            updated = updated_profile(profile, question.field, value, profile_id, answer_event)
            self._record_profile(updated)
            return updated

        answer_event = append_profile_answer(
            self._store,
            run,
            field=question.field,
            question=question.question,
            question_event_id=question.event_id,
            answer=value,
            actor=actor,
            profile_id=profile.id,
            profile_version=profile.version,
            answer_source="live_human",
            source_ref=None,
            judgment=judgment,
            judgment_source=judgment_source,
        )
        events = self._store.events(run.id)
        question_event = _event_for_question(events, question.event_id)
        risk_uncertainty = _risk_uncertainty_marker(question_event)
        # The answered event closes the current question even though it does not revise the
        # profile. The follow-up question becomes the sole pending question.
        if (
            self._question_count(run.id) >= self._max_questions
            or _field_question_count(events, question.field) >= MAX_INTERVIEW_QUESTIONS_PER_FIELD
            or (
                risk_uncertainty is not None
                and uncertainty_question_count(events) >= MAX_RISK_UNCERTAINTY_QUESTIONS
            )
        ):
            return profile
        follow_up, question_source = generate_follow_up(
            question.field,
            question.question,
            value,
            judgment.reason,
            INTERVIEW_FIELDS,
            profile,
            model_context,
            current_request=run.conversation.requested_work if run.conversation else run.prompt,
            prior_question_answers=prior_answered_question_context(events, profile, question),
        )
        pending = self._request_question(
            run,
            profile,
            question.field,
            follow_up,
            question_source=question_source,
            follow_up_to_answer_event_id=answer_event.event_id,
            risk_uncertainty=risk_uncertainty,
        )
        self._ensure_waiting_for_information(run.id, pending)
        return profile

    def _resolve_model_context(
        self,
        run: Run,
        *,
        provider: LLMProvider | None,
        event_log: EventLog | None,
        before_model_selection: Callable[[], None] | None,
        before_model_call: Callable[[ModelChoice], None] | None,
        record_model_usage: Callable[[LLMResponse], None] | None,
        request_ledger: RequestLedger | None,
        route_policy: ModelRoutePolicy | None,
    ) -> RiskInterviewModelContext | None:
        """Resolve explicit model handles, or the composition-root handles for one Run."""
        default = (
            self._model_context_factory(run.id) if self._model_context_factory is not None else None
        )
        resolved_provider = provider or (default.provider if default is not None else None)
        resolved_event_log = event_log or (default.event_log if default is not None else None)
        if resolved_provider is None or resolved_event_log is None:
            return None
        return RiskInterviewModelContext(
            provider=resolved_provider,
            event_log=resolved_event_log,
            before_model_selection=(
                before_model_selection
                if before_model_selection is not None
                else (default.before_model_selection if default is not None else None)
            ),
            before_model_call=(
                before_model_call
                if before_model_call is not None
                else (default.before_model_call if default is not None else None)
            ),
            record_model_usage=(
                record_model_usage
                if record_model_usage is not None
                else (default.record_model_usage if default is not None else None)
            ),
            request_ledger=(
                request_ledger
                if request_ledger is not None
                else (default.request_ledger if default is not None else None)
            ),
            route_policy=(
                route_policy
                if route_policy is not None
                else (default.route_policy if default is not None else run.model_route_policy)
            ),
        )

    def _apply_context(
        self,
        run: Run,
        profile: ActivityProfile,
        context_document: str | None,
        model_context: RiskInterviewModelContext | None,
    ) -> ActivityProfile:
        """Record each missing fact the model states with certainty from the known context.

        Code, not the model, decides what may be recorded: only a currently missing fixed field,
        once, with a value that is not an absence placeholder.
        """
        missing = _missing_fields(profile)
        if not missing:
            return profile
        sources = ["run.conversation.requested_work" if run.conversation else _PROMPT_REF]
        request = run.conversation.requested_work if run.conversation else run.prompt
        context = f"Run request:\n{request.strip()}"
        if context_document and context_document.strip():
            sources.append(_CONTEXT_DOCUMENT_REF)
            context += f"\n\nProject context ({_CONTEXT_DOCUMENT_REF}):\n{context_document.strip()}"
        extraction, source = extract_facts(
            missing, _QUESTIONS, context, INTERVIEW_FIELDS, profile, model_context
        )
        if extraction is None:
            return profile
        stated = {
            fact.field: fact
            for fact in reversed(extraction.facts)
            if fact.field in missing and not is_missing_text(fact.value)
        }
        for field in missing:
            fact = stated.get(field)
            if fact is None:
                continue
            profile_id = new_id("profile")
            answer_event = append_profile_answer(
                self._store,
                run,
                field=field,
                question=_QUESTIONS[field],
                question_event_id=None,
                answer=fact.value,
                actor=Actor.system(),
                profile_id=profile_id,
                profile_version=profile.version + 1,
                answer_source="project_context",
                source_ref=", ".join(sources),
                judgment=SufficiencyJudgment(sufficient=True, reason=fact.reason),
                judgment_source=source,
            )
            profile = updated_profile(profile, field, fact.value, profile_id, answer_event)
            self._record_profile(profile)
        return profile

    def clarify_uncertainty(
        self,
        run: Run,
        assessment: RiskAssessment,
        model_context: RiskInterviewModelContext | None,
    ) -> PendingRiskQuestion | None:
        """Ask one model-selected question bound to an exact uncertain assessment and profile."""
        profile = self._recorded_profile(run.id)
        if profile is None:
            raise RiskInterviewNotPendingError(
                f"run {run.id!r} has no persisted activity profile to clarify"
            )
        if (
            assessment.run_id != run.id
            or assessment.activity_profile_id != profile.id
            or assessment.activity_profile_version != profile.version
        ):
            raise RiskInterviewError("risk assessment does not match the current activity profile")
        if self.latest_assessment(run.id, profile) != assessment:
            raise RiskInterviewError("risk assessment is not recorded for the current profile")

        events = self._store.events(run.id)
        pending = self._pending_question(run.id, profile)
        if pending is not None:
            event = _event_for_question(events, pending.event_id)
            if event is None:
                raise RiskInterviewError("the pending question evidence is missing")
            marker = _risk_uncertainty_marker(event)
            scope = _risk_uncertainty_scope(assessment, profile)
            if marker is not None and all(marker.get(key) == value for key, value in scope.items()):
                self._ensure_waiting_for_information(run.id, pending)
                return pending
            return None

        current = self._control_plane.controller.current_state(run.id).state
        if current.condition.value == "waiting":
            return None
        if (
            self._question_count(run.id) >= self._max_questions
            or uncertainty_question_count(events) >= MAX_RISK_UNCERTAINTY_QUESTIONS
        ):
            return None

        allowed_fields = tuple(
            field
            for field in INTERVIEW_FIELDS
            if _field_question_count(events, field) < MAX_INTERVIEW_QUESTIONS_PER_FIELD
        )
        selection, source = select_risk_uncertainty_question(
            run,
            assessment,
            profile,
            events,
            allowed_fields,
            model_context,
        )
        if selection is None or selection.field is None or selection.question is None:
            return None

        marker = _risk_uncertainty_scope(assessment, profile)
        marker["question_round"] = uncertainty_question_count(events) + 1
        pending = self._request_question(
            run,
            profile,
            selection.field,
            selection.question,
            question_source=f"risk_uncertainty_{source}",
            risk_uncertainty=marker,
        )
        self._ensure_waiting_for_information(run.id, pending)
        return pending

    def clarify_field(
        self,
        run: Run,
        *,
        field: str,
        expected_decision_id: str | None = None,
        actor: Actor | None = None,
        question_source: str = "risk_classification",
        recorded_concern: str | None = None,
    ) -> PendingRiskQuestion | None:
        """Ask one bounded risk fact, or replace the exact parked risk review with its question."""
        if field not in INTERVIEW_FIELDS:
            raise RiskInterviewError(f"unknown activity-profile field {field!r}")
        profile = self._recorded_profile(run.id)
        if profile is None:
            raise RiskInterviewNotPendingError(
                f"run {run.id!r} has no persisted activity profile to clarify"
            )

        def ensure_waiting(question: PendingRiskQuestion) -> None:
            self._ensure_waiting_for_information(run.id, question)

        return clarify_recorded_field(
            self._store,
            self._control_plane,
            run,
            profile,
            field=field,
            fixed_question=_QUESTIONS[field],
            recorded_concern=recorded_concern,
            expected_decision_id=expected_decision_id,
            actor=actor,
            max_questions=self._max_questions,
            pending_question=lambda: self._pending_question(run.id, profile),
            request_question=lambda question: self._request_question(
                run,
                profile,
                field,
                question,
                question_source=question_source,
            ),
            ensure_waiting=ensure_waiting,
            question_from_event=_question_from_event,
        )

    def _request_question(
        self,
        run: Run,
        profile: ActivityProfile,
        field: str,
        question: str,
        *,
        question_source: str,
        follow_up_to_answer_event_id: str | None = None,
        expected_decision_id: str | None = None,
        risk_uncertainty: dict[str, str | int] | None = None,
    ) -> PendingRiskQuestion:
        """Append one policy-gated question event for a missing fixed field."""
        question_number = self._question_count(run.id) + 1
        event = append_risk_question(
            self._store,
            run,
            profile,
            field,
            question,
            question_number=question_number,
            question_source=question_source,
            expected_decision_id=expected_decision_id,
            follow_up_to_answer_event_id=follow_up_to_answer_event_id,
            risk_uncertainty=risk_uncertainty,
        )
        return _question_from_event(event)

    def latest_assessment(self, run_id: str, profile: ActivityProfile) -> RiskAssessment | None:
        """Read the exact immutable assessment through the record projection seam."""
        return recorded_assessment(self._store, run_id, profile)

    def classify(
        self,
        run: Run,
        initial_profile: ActivityProfile,
        event_log: EventLog,
        provider: LLMProvider,
        *,
        before_model_selection: Callable[[], None] | None = None,
        before_model_call: Callable[[ModelChoice], None] | None = None,
        record_model_usage: Callable[[LLMResponse], None] | None = None,
        route_policy: ModelRoutePolicy | None = None,
        request_ledger: RequestLedger | None = None,
    ) -> RiskProfile:
        """Classify the current profile once, floored by its recorded MIRA assessment."""
        profile = self._profile(run, initial_profile)
        return classify_activity_risk(
            run,
            profile,
            _risk_facts(
                profile,
                requested_work=run.conversation.requested_work if run.conversation else run.prompt,
            ),
            RiskInterviewModelContext(
                provider=provider,
                event_log=event_log,
                before_model_selection=before_model_selection,
                before_model_call=before_model_call,
                record_model_usage=record_model_usage,
                route_policy=route_policy,
                request_ledger=request_ledger,
            ),
            self._store,
            self._control_plane,
            max_questions=self._max_questions,
        )

    def _profile(self, run: Run, initial_profile: ActivityProfile) -> ActivityProfile:
        """Load the latest persisted version or record the supplied initial immutable profile."""
        if initial_profile.run_id != run.id:
            raise ValueError("initial activity profile must belong to the Run")
        profile = self._recorded_profile(run.id)
        if profile is not None:
            return profile
        self._record_profile(initial_profile)
        return initial_profile

    def _recorded_profile(self, run_id: str) -> ActivityProfile | None:
        """Delegate the immutable event-record operation."""
        return recorded_profile(self._store, run_id)

    def _record_profile(self, profile: ActivityProfile) -> None:
        """Delegate the immutable event-record operation."""
        return record_profile(self._store, profile)

    def _pending_question(
        self, run_id: str, profile: ActivityProfile | None = None
    ) -> PendingRiskQuestion | None:
        """Project the pending event only while the bounded review is not already waiting."""
        return _pending_risk_question(
            self._store.events(run_id),
            profile or self._recorded_profile(run_id),
            max_questions=self._max_questions,
            limit_review_pending=self._limit_review_pending(run_id),
        )

    def _question_count(self, run_id: str) -> int:
        """Return the durable number of questions already asked for one Run."""
        return sum(
            event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
            for event in self._store.events(run_id)
        )

    def _ensure_waiting_for_information(self, run_id: str, question: PendingRiskQuestion) -> None:
        """Publish the question's existing policy-gated information wait."""
        ensure_question_wait(self._store, self._control_plane, run_id, question.event_id)

    def _escalate_question_limit(self, run_id: str) -> None:
        """Publish one review at the interview resume target, preserving its decision details."""
        if self._limit_review_resolved(run_id) or self._limit_review_pending(run_id):
            return
        request_interview_limit_review(
            self._store, self._control_plane, run_id, max_questions=self._max_questions
        )

    def _limit_review_pending(self, run_id: str) -> bool:
        """Return whether this interview already requested but has not received the limit review."""
        requested = self._limit_review_decision_id(run_id)
        if requested is None:
            return False
        return not self._limit_review_answered(run_id)

    def _limit_review_answered(self, run_id: str) -> bool:
        """Whether an authenticated answer or recorded system resolution retired the review."""
        return interview_limit_answered(self._store.events(run_id))

    def _limit_review_resolved(self, run_id: str) -> bool:
        """Whether an authenticated human approved the exact bounded interview review."""
        return interview_limit_approved(self._store.events(run_id))

    def _limit_review_decision_id(self, run_id: str) -> str | None:
        """Find the exact bounded-interview decision in the persisted record."""
        return interview_limit_decision(self._store.events(run_id))


def _missing_fields(profile: ActivityProfile) -> tuple[str, ...]:
    """Return only required fields without evidence, never inferring a replacement fact."""
    missing: list[str] = []
    for field in INTERVIEW_FIELDS:
        value = getattr(profile, field)
        if not has_declared_value(value):
            missing.append(field)
    return tuple(missing)


def _risk_facts(profile: ActivityProfile, *, requested_work: str | None = None) -> RiskFacts:
    """Extract only declared activity-profile facts for the deterministic classifier layer."""
    population = normalise(profile.affected_population)
    no_person_impact = population in _NO_VALUE_MARKERS
    has_sensitive_attributes = bool(profile.sensitive_attributes) and any(
        normalise(value) not in _NO_VALUE_MARKERS for value in profile.sensitive_attributes
    )
    present_context = tuple(
        name
        for name, value in (
            ("intended_use", profile.purpose),
            ("decision_role", profile.decision_effect),
            ("deployment_context", profile.autonomy),
            ("affected_population", profile.affected_population),
            ("human_oversight", profile.human_oversight),
            ("potential_consequences", profile.potential_consequences),
        )
        if has_declared_value(value)
    )
    return RiskFacts(
        objective=requested_work or profile.purpose,
        has_sensitive_attributes=has_sensitive_attributes,
        affects_natural_persons=not no_person_impact,
        missing_required_information=_missing_fields(profile),
        present_context_fields=present_context,
        answerable_fields=ANSWERABLE_RISK_FIELDS,
        declared_context=tuple(
            (name, value if isinstance(value, str) else "; ".join(value) or "none")
            for name, value in (
                ("intended_use", profile.purpose),
                ("affected_population", profile.affected_population),
                ("decision_effect", profile.decision_effect),
                ("decision_role", profile.decision_effect),
                ("autonomy", profile.autonomy),
                ("deployment_context", profile.autonomy),
                ("human_oversight", profile.human_oversight),
                ("jurisdiction", profile.jurisdiction),
                ("data_categories", profile.data_categories),
                ("sensitive_attributes", profile.sensitive_attributes),
                ("potential_consequences", profile.potential_consequences),
            )
            if has_declared_value(value)
        ),
    )


__all__ = [
    "INTERVIEW_FIELDS",
    "MAX_INTERVIEW_QUESTIONS",
    "MAX_INTERVIEW_QUESTIONS_PER_FIELD",
    "PendingRiskQuestion",
    "RiskInterviewError",
    "RiskInterviewModelContext",
    "RiskInterviewNotPendingError",
    "RiskInterviewService",
    "RiskInterviewStatus",
    "SufficiencyJudgment",
]
