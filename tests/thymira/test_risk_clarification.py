"""Event-level tests for bounded risk clarification and review races."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

import thymira.core.risk_interview as risk_interview_module
from thymira.agents.llm import ScriptedProvider
from thymira.core import RunService, SessionService
from thymira.core.control_plane import MiraControlPlane, RunController, RunEventLog
from thymira.core.dispatch import ExecutionDispatcher, InlineDispatcher
from thymira.core.graph.adapters import MiraSubgraph
from thymira.core.graph.checkpoint import StateCheckpointer
from thymira.core.graph.compose import build_runtime_graph
from thymira.core.graph.protocol import SubgraphDeps
from thymira.core.graph.state import RuntimeState, initial_runtime_state
from thymira.core.risk_clarification import (
    append_risk_question,
    clarification_field_for_missing_information,
)
from thymira.core.risk_interview import RiskInterviewService, SufficiencyJudgment
from thymira.core.risk_interview_model import RiskInterviewModelContext
from thymira.core.risk_profile_records import append_profile_answer
from thymira.core.risk_uncertainty_questions import select_risk_uncertainty_question
from thymira.core.run_state import (
    InvalidRunTransitionError,
    RunTransitionCommand,
    RunTransitionKind,
    apply_transition,
)
from thymira.core.usage import UsageLedger
from thymira.events import verify_events
from thymira.mira.preflight import (
    MODEL_UNCERTAINTY_JUSTIFICATION_PREFIX,
    RiskJudgment,
    load_default_packs,
)
from thymira.policies import (
    ActionRule,
    Gate,
    Policy,
    PolicyEngine,
    RiskProfile,
    load_default_policy,
)
from thymira.schemas import (
    ActivityProfile,
    Actor,
    Decision,
    EventType,
    Id,
    ModelRoutePolicy,
    RiskAssessment,
    RiskLevel,
    Run,
    RunOutcome,
    WaitReason,
    new_id,
)
from thymira.state import (
    LocalArtifactStore,
    LocalCheckpointRepository,
    LocalRecordRepository,
    LocalRunStore,
    LocalSessionRepository,
)
from thymira.tools import ToolManager, ToolRegistry

if TYPE_CHECKING:
    from pathlib import Path

_REVIEWER = Actor(kind="human", id="risk-owner", authenticated=True)
_TEST_ROUTE_POLICY = ModelRoutePolicy.from_routes(
    ("test-model",), authority="risk-clarification-tests"
)


def test_synthetic_risk_classification_does_not_map_to_a_profile_fact() -> None:
    """A synthetic classifier uncertainty has no fixed human-answerable field."""
    assert clarification_field_for_missing_information(("risk_classification",)) is None


def _profile(run: Run) -> ActivityProfile:
    """A complete profile whose already-declared decision effect can be clarified."""
    return ActivityProfile(
        id=new_id("profile"),
        activity_id=new_id("activity"),
        version=1,
        run_id=run.id,
        purpose="Prioritise applications for human review.",
        affected_population="Credit applicants.",
        decision_effect="Changes the review queue; a person makes the final decision.",
        autonomy="Recommendation only.",
        human_oversight="An analyst can override every recommendation.",
        jurisdiction="ES",
        data_categories=("credit_history",),
        sensitive_attributes=(),
        potential_consequences=("An urgent application could be reviewed late.",),
    )


def _require_start_review_policy() -> Policy:
    """Ask human review at execution start while leaving information requests policy-gated."""
    return Policy(
        name="risk-clarification-test",
        version="1.0",
        action_rules=(
            ActionRule(
                id="START-REVIEW",
                action_types=("execution.start",),
                decision=Decision.REQUIRE_HUMAN_REVIEW,
                reason="Review the execution start.",
            ),
        ),
    )


def _pass_start_policy() -> Policy:
    """Allow the reconstructed test graph to continue after its injected first review."""
    return Policy(
        name="risk-clarification-pass-start",
        version="1.0",
        action_rules=(
            ActionRule(
                id="START-PASS",
                action_types=("execution.start",),
                decision=Decision.PASS,
                reason="The reconstructed test graph may continue.",
            ),
        ),
    )


def _parked_review(
    tmp_path: Path, *, target: str = "risk_uncertainty"
) -> tuple[Run, LocalRunStore, RiskInterviewService, RunService, str]:
    """Create a real Gate execution-start review for one planning Run."""
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Review the activity context.",
    )
    store = LocalRunStore(tmp_path / "runs")
    store.create(run, actor=Actor.system())
    policy = PolicyEngine(load_default_policy())
    control_plane = MiraControlPlane(store, policy)
    risk_interview = RiskInterviewService(store, control_plane)
    profile = _profile(run)
    risk_interview.begin(run, profile)
    controller = RunController(store)
    controller.advance(run.id, RunTransitionKind.START)
    review_gate = Gate(PolicyEngine(_require_start_review_policy()), RunEventLog(store, run.id))
    decision = review_gate.authorize_execution(
        RiskProfile(risk_level="limited", activity_category="analysis", confidence=1.0), ()
    )
    assert decision.decision is Decision.REQUIRE_HUMAN_REVIEW
    controller.park_for_review(run.id, decision, resume_from=target)
    service = RunService(
        store,
        SessionService(LocalSessionRepository(tmp_path / "sessions")),
        risk_interview=risk_interview,
    )
    return run, store, risk_interview, service, decision.id


@dataclass(frozen=True, slots=True)
class _UncertainMiraScenario:
    """An active Run with prior human Q&A and a prepared event-backed MIRA preflight."""

    run: Run
    store: LocalRunStore
    controller: RunController
    event_log: RunEventLog
    policy: PolicyEngine
    interview: RiskInterviewService
    profile: ActivityProfile
    deps: SubgraphDeps


def _uncertain_mira_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _UncertainMiraScenario:
    """Build an active offline-test Run whose full profile includes one prior human answer."""
    for variable in (
        "THYMIRA_MODEL_FAST",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FRONTIER",
    ):
        monkeypatch.setenv(variable, "test-model")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=(
            "This is a test dataset and not a production model; the offline test has no live "
            "decision effect."
        ),
        model_route_policy=_TEST_ROUTE_POLICY,
    )
    store = LocalRunStore(tmp_path / "runs")
    store.create(run, actor=Actor.system())
    controller = RunController(store)
    controller.advance(run.id, RunTransitionKind.START)
    event_log = RunEventLog(store, run.id)
    policy = PolicyEngine(load_default_policy())
    interview = RiskInterviewService(store, MiraControlPlane(store, policy))
    profile = _profile(run).model_copy(
        update={
            "purpose": "Offline test of the credit-risk dataset.",
            "decision_effect": "No live decision effect; offline test only.",
            "potential_consequences": ("None; offline test has no operational consequences.",),
        }
    )
    interview.begin(run, profile)
    prior_question = interview.clarify_field(run, field="autonomy")
    assert prior_question is not None
    profile = interview.answer(
        run,
        answer="Offline test only; a person performs every later decision.",
        actor=_REVIEWER,
        expected_question_id=prior_question.event_id,
    )
    controller.advance(run.id, RunTransitionKind.RESUME, payload={"resume_from": "information"})
    deps = SubgraphDeps(
        event_log=event_log,
        gate=Gate(policy, event_log),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", run.id),
        tool_manager=ToolManager(ToolRegistry()),
        record_repository=LocalRecordRepository(tmp_path / "records"),
        usage_ledger=UsageLedger(),
    )
    return _UncertainMiraScenario(
        run,
        store,
        controller,
        event_log,
        policy,
        interview,
        profile,
        deps,
    )


def _run_uncertain_mira_preflight(
    scenario: _UncertainMiraScenario,
    extra_responses: list[Any],
    *,
    profile: ActivityProfile | None = None,
    provider_model: str = "test-model",
) -> tuple[ScriptedProvider, MiraSubgraph, RiskJudgment]:
    """Run one offline uncertain MIRA assessment and any scripted question-selection response."""
    active_profile = profile or scenario.profile
    uncertain = RiskJudgment(
        risk_level="unknown",
        activity_category="unknown",
        confidence=0.5,
        missing_information=("risk_classification",),
        justification=f"{MODEL_UNCERTAINTY_JUSTIFICATION_PREFIX}: borderline confidence.",
    )
    provider = ScriptedProvider([uncertain, *extra_responses], model=provider_model)
    mira = MiraSubgraph(
        scenario.run,
        active_profile,
        load_default_packs(),
        Actor.system(),
        provider=provider,
        route_policy=_TEST_ROUTE_POLICY,
        control_plane=scenario.interview._control_plane,
        risk_interview=scenario.interview,
    )
    mira.preflight(initial_runtime_state(scenario.run), deps=scenario.deps)
    return provider, mira, uncertain


def test_manual_clarification_replaces_only_the_exact_review_park(tmp_path: Path) -> None:
    """One Gate transition parks the Run on a concrete question without changing its profile."""
    run, store, interview, service, decision_id = _parked_review(tmp_path)
    before = interview.current_profile(run, _profile(run))

    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=decision_id,
        actor=_REVIEWER,
    )

    state = RunController(store).current_state(run.id)
    transitions = [
        event for event in store.events(run.id) if event.type is EventType.RUN_TRANSITIONED
    ]
    clarification = transitions[-1]
    question = next(
        event
        for event in store.events(run.id)
        if event.event_id == clarification.payload["risk_clarification"]["question_event_id"]
    )
    assert state.condition.value == "waiting"
    assert state.wait_reason is WaitReason.INFORMATION
    assert clarification.payload["command"] == "wait_for_information"
    assert clarification.payload["resume_from"] == "risk_clarification"
    assert clarification.payload["supersedes_decision_id"] == decision_id
    assert question.payload["field"] == "decision_effect"
    assert question.payload["question"] == (
        "How can this activity influence a decision about a person or organisation?"
    )
    assert question.payload["requested_by"] == _REVIEWER.to_json_dict()
    assert clarification.payload["requested_by"] == _REVIEWER.to_json_dict()
    assert interview.current_profile(run, before) == before
    assert verify_events(store.events(run.id)).valid


def test_old_clarification_retry_cannot_replay_after_a_new_question_is_pending(
    tmp_path: Path,
) -> None:
    """An old approval is stale once its question is answered and a new one becomes pending."""
    run, store, interview, service, decision_id = _parked_review(tmp_path)
    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=decision_id,
        actor=_REVIEWER,
    )
    first_question = interview.status(run, _profile(run)).pending_question
    assert first_question is not None
    first_profile = interview.current_profile(run, _profile(run))
    updated = interview.answer(
        run,
        answer="Changes queue order only; a person makes each decision.",
        actor=_REVIEWER,
        expected_question_id=first_question.event_id,
    )
    RunController(store).advance(
        run.id,
        RunTransitionKind.RESUME,
        payload={"resume_from": "information"},
    )
    next_question = interview._request_question(
        run,
        updated,
        "decision_effect",
        "Clarify the new decision-effect version.",
        question_source="risk_classification",
    )
    interview._ensure_waiting_for_information(run.id, next_question)
    event_count = len(store.events(run.id))

    with pytest.raises(ValueError, match="already clarified"):
        service.clarify_risk_interview(
            run.id,
            field="decision_effect",
            expected_decision_id=decision_id,
            actor=_REVIEWER,
        )

    assert interview.current_profile(run, first_profile).id == updated.id
    assert len(store.events(run.id)) == event_count


def test_manual_clarification_enforces_the_per_field_question_bound(tmp_path: Path) -> None:
    """Manual review clarification observes the same three-question field cap as intake."""
    run, store, interview, service, decision_id = _parked_review(tmp_path)
    profile = interview.current_profile(run, _profile(run))
    for number in range(3):
        question = append_risk_question(
            store,
            run,
            profile,
            "decision_effect",
            f"Earlier decision-effect question {number + 1}",
            question_number=number + 1,
            question_source="risk_classification",
        )
        store.append(
            run.id,
            EventType.ACTIVITY_PROFILE_ANSWERED,
            _REVIEWER,
            {"question_event_id": question.event_id},
            expected_version=store.version(run.id),
            subject_id=profile.id,
        )
    event_count = len(store.events(run.id))

    with pytest.raises(ValueError, match="question limit"):
        service.clarify_risk_interview(
            run.id,
            field="decision_effect",
            expected_decision_id=decision_id,
            actor=_REVIEWER,
        )

    assert len(store.events(run.id)) == event_count
    assert not any(
        event.type is EventType.RUN_TRANSITIONED
        and event.payload.get("resume_from") == "risk_clarification"
        for event in store.events(run.id)
    )


def test_mira_unknown_classification_asks_the_model_for_a_grounded_profile_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real MIRA preflight turns its generic uncertainty into a concrete fact question."""
    scenario = _uncertain_mira_scenario(tmp_path, monkeypatch)
    run = scenario.run
    store = scenario.store
    controller = scenario.controller
    interview = scenario.interview
    profile = scenario.profile
    prior_question_event = next(
        event
        for event in store.events(run.id)
        if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and event.payload.get("field") == "autonomy"
    )
    selected_question = {
        "field": "decision_effect",
        "question": (
            "Does this offline test's output stay out of every live decision, including any "
            "later deployment?"
        ),
    }
    provider, _mira, uncertain = _run_uncertain_mira_preflight(scenario, [selected_question])

    assessment = interview.latest_assessment(run.id, profile)
    assert assessment is not None
    assert assessment.justification.startswith(MODEL_UNCERTAINTY_JUSTIFICATION_PREFIX), (
        assessment.justification,
        assessment.missing_information,
        len(provider.calls),
    )
    question = interview.status(run, profile).pending_question
    assert question is not None, (
        interview.latest_assessment(run.id, profile),
        provider.calls,
        controller.current_state(run.id),
    )
    assert question.field == "decision_effect"
    assert question.question == selected_question["question"]
    assert len(provider.calls) == 2
    uncertainty_prompt = provider.calls[1]["prompt"]
    assert run.prompt in uncertainty_prompt
    assert uncertain.justification in uncertainty_prompt
    assert profile.decision_effect in uncertainty_prompt
    assert "allowed_field_definitions" in uncertainty_prompt
    assert "including indirect effects" in uncertainty_prompt
    assert "inherent impact; it does not ask for controls" in uncertainty_prompt
    assert "mitigation measures are assessed separately" in " ".join(
        provider.calls[1]["system"].split()
    )
    assert "None; offline test has no operational consequences." in uncertainty_prompt
    assert prior_question_event.payload["question"] in uncertainty_prompt
    assert "Offline test only; a person performs every later decision." in uncertainty_prompt
    assert "risk_classification" in uncertainty_prompt
    selection_call = next(
        event for event in reversed(store.events(run.id)) if event.type is EventType.MODEL_SELECTED
    )
    assert selection_call.payload["role"] == "mira"
    assert selection_call.payload["task"] == "classify"
    assert controller.current_state(run.id).wait_reason is WaitReason.INFORMATION
    assert EventType.HUMAN_APPROVAL_REQUESTED not in [event.type for event in store.events(run.id)]
    assert any(event.type is EventType.RISK_ASSESSMENT_RECORDED for event in store.events(run.id))
    question_event = next(
        event
        for event in store.events(run.id)
        if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and event.event_id == question.event_id
    )
    assert question_event.payload["risk_uncertainty"] == {
        "risk_assessment_id": assessment.id,
        "activity_profile_id": profile.id,
        "activity_profile_version": profile.version,
        "question_round": 1,
    }

    replay_provider = ScriptedProvider([])
    replay_mira = MiraSubgraph(
        run,
        profile,
        load_default_packs(),
        Actor.system(),
        provider=replay_provider,
        route_policy=_TEST_ROUTE_POLICY,
        control_plane=interview._control_plane,
        risk_interview=interview,
    )
    replay_mira.preflight(initial_runtime_state(run), deps=scenario.deps)

    assert replay_provider.calls == []
    assert interview.status(run, profile).pending_question == question


def test_mira_uncertainty_selector_denies_a_provider_that_differs_from_the_selected_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selector records route denial and makes no provider call on model mismatch."""
    scenario = _uncertain_mira_scenario(tmp_path, monkeypatch)
    profile = scenario.profile
    assessment = RiskAssessment(
        id=new_id("assessment"),
        run_id=scenario.run.id,
        activity_profile_id=profile.id,
        activity_profile_version=profile.version,
        assessor=Actor.system(),
        subject_kind="activity",
        subject_id=profile.activity_id,
        risk_level=RiskLevel.UNKNOWN,
        activity_category="unknown",
        confidence=0.5,
        missing_information=("risk_classification",),
        justification="MIRA could not classify this assessment with confidence.",
        assessment_method="mira.inherent-risk",
        assessment_method_version="1",
    )
    provider = ScriptedProvider(
        [{"field": "purpose", "question": "What exact purpose does this test serve?"}],
        model="unapproved-model",
    )
    context = RiskInterviewModelContext(
        provider=provider,
        event_log=scenario.event_log,
        route_policy=_TEST_ROUTE_POLICY,
    )

    selection, reason = select_risk_uncertainty_question(
        scenario.run,
        assessment,
        profile,
        scenario.event_log.events(),
        risk_interview_module.INTERVIEW_FIELDS,
        context,
    )

    assert selection is None
    assert reason == "provider_route_mismatch"
    assert provider.calls == []
    denied = next(
        event
        for event in reversed(scenario.event_log.events())
        if event.type is EventType.MODEL_ROUTE_DENIED
    )
    assert denied.payload == {
        "route": "unapproved-model",
        "selected_route": "test-model",
        "reason": "provider_route_differs_from_selection",
        "policy_sha256": _TEST_ROUTE_POLICY.sha256,
    }


@pytest.mark.parametrize(
    ("question_responses", "expected_provider_calls"),
    [
        ([{"field": "not_allowed", "question": "Invent an unrelated activity fact?"}], 2),
        ([{"field": None, "question": None}], 2),
        ([], 2),
    ],
    ids=("invalid-field", "no-useful-question", "model-unavailable"),
)
def test_mira_uncertainty_falls_back_to_exact_human_review_when_no_question_is_usable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question_responses: list[Any],
    expected_provider_calls: int,
) -> None:
    """Unusable question output parks the exact risk assessment for a human decision."""
    scenario = _uncertain_mira_scenario(tmp_path, monkeypatch)
    provider, _mira, _uncertain = _run_uncertain_mira_preflight(scenario, question_responses)

    assert len(provider.calls) == expected_provider_calls
    assert scenario.interview.status(scenario.run, scenario.profile).pending_question is None
    state = scenario.controller.current_state(scenario.run.id)
    assert state.condition.value == "waiting"
    assert state.wait_reason is WaitReason.APPROVAL
    assessment = scenario.interview.latest_assessment(scenario.run.id, scenario.profile)
    assert assessment is not None
    approval = next(
        event
        for event in reversed(scenario.store.events(scenario.run.id))
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    request_payload = approval.payload["request_payload"]
    assert request_payload["risk_assessment_id"] == assessment.id
    assert request_payload["activity_profile_id"] == scenario.profile.id
    assert request_payload["activity_profile_version"] == scenario.profile.version
    assert request_payload["known_facts"]["decision_effect"] == scenario.profile.decision_effect
    assert request_payload["clarification_rounds"] == {
        "total": 0,
        "max_total": 3,
        "items": [],
    }
    assert set(request_payload["decision_effects"]) == {"approve", "reject"}
    assert not any(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and isinstance(event.payload.get("risk_uncertainty"), dict)
        for event in scenario.store.events(scenario.run.id)
    )


def test_mira_uncertainty_questions_are_bounded_across_profile_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revising the profile cannot restart the three-question uncertainty budget."""
    scenario = _uncertain_mira_scenario(tmp_path, monkeypatch)
    profile = scenario.profile
    rounds = (
        ("purpose", "What specific purpose does this offline test serve?", "Compare test records."),
        (
            "decision_effect",
            "Could this test's result influence any live decision later?",
            "No; results remain offline and do not affect live decisions.",
        ),
        (
            "potential_consequences",
            "What concrete operational consequence could this test cause?",
            "None; this test has no operational consequence.",
        ),
    )

    for field, question_text, answer in rounds:
        _provider, _mira, _uncertain = _run_uncertain_mira_preflight(
            scenario,
            [{"field": field, "question": question_text}],
            profile=profile,
        )
        pending = scenario.interview.status(scenario.run, profile).pending_question
        assert pending is not None
        assert pending.field == field
        profile = scenario.interview.answer(
            scenario.run,
            answer=answer,
            actor=_REVIEWER,
            expected_question_id=pending.event_id,
            provider=ScriptedProvider(
                [{"sufficient": True, "reason": "The scoped fact is clear."}]
            ),
            event_log=scenario.event_log,
        )
        scenario.controller.advance(
            scenario.run.id,
            RunTransitionKind.RESUME,
            payload={"resume_from": "information"},
        )

    final_provider, _mira, _uncertain = _run_uncertain_mira_preflight(scenario, [], profile=profile)

    assert len(final_provider.calls) == 1  # No fourth question-selection call.
    assert scenario.interview.status(scenario.run, profile).pending_question is None
    state = scenario.controller.current_state(scenario.run.id)
    assert state.wait_reason is WaitReason.APPROVAL
    approval = next(
        event
        for event in reversed(scenario.store.events(scenario.run.id))
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    rounds_payload = approval.payload["request_payload"]["clarification_rounds"]
    assert rounds_payload["total"] == 3
    assert rounds_payload["max_total"] == 3
    assert [item["field"] for item in rounds_payload["items"]] == [
        field for field, _question_text, _answer in rounds
    ]
    assert all(item["answer"] is not None for item in rounds_payload["items"])


@pytest.mark.parametrize("recover_interrupted_answer", [False, True])
@pytest.mark.parametrize("reuse_field", [False, True], ids=["distinct-fields", "same-field"])
def test_rejected_third_uncertainty_answer_closes_instead_of_asking_a_fourth_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recover_interrupted_answer: bool,
    reuse_field: bool,
) -> None:
    """The final rejected question closes through both normal and crash-recovery paths."""
    scenario = _uncertain_mira_scenario(tmp_path, monkeypatch)
    profile = scenario.profile
    for field, question_text, answer in (
        (
            "decision_effect" if reuse_field else "purpose",
            "What exact purpose does this offline test serve?",
            "Compare test records.",
        ),
        (
            "decision_effect",
            "Could this offline test affect any later live decision?",
            "No; it stays offline and affects no live decision.",
        ),
    ):
        _run_uncertain_mira_preflight(
            scenario,
            [{"field": field, "question": question_text}],
            profile=profile,
        )
        question = scenario.interview.status(scenario.run, profile).pending_question
        assert question is not None
        profile = scenario.interview.answer(
            scenario.run,
            answer=answer,
            actor=_REVIEWER,
            expected_question_id=question.event_id,
            provider=ScriptedProvider(
                [{"sufficient": True, "reason": "The scoped fact is clear."}],
                model="test-model",
            ),
            event_log=scenario.event_log,
            route_policy=_TEST_ROUTE_POLICY,
        )
        scenario.controller.advance(
            scenario.run.id,
            RunTransitionKind.RESUME,
            payload={"resume_from": "information"},
        )

    _run_uncertain_mira_preflight(
        scenario,
        [
            {
                "field": "decision_effect" if reuse_field else "potential_consequences",
                "question": "What concrete operational consequence could this test cause?",
            }
        ],
        profile=profile,
    )
    third = scenario.interview.status(scenario.run, profile).pending_question
    assert third is not None
    rejected_answer = "I am not sure; maybe something happens."
    if recover_interrupted_answer:
        append_profile_answer(
            scenario.store,
            scenario.run,
            field=third.field,
            question=third.question,
            question_event_id=third.event_id,
            answer=rejected_answer,
            actor=_REVIEWER,
            profile_id=profile.id,
            profile_version=profile.version,
            answer_source="live_human",
            source_ref=None,
            judgment=SufficiencyJudgment(sufficient=False, reason="The consequence is unclear."),
            judgment_source="test",
        )
        recovered_profile = scenario.interview.answer(
            scenario.run,
            answer=rejected_answer,
            actor=_REVIEWER,
            expected_question_id=third.event_id,
        )
        assert recovered_profile == profile
    else:
        answer_provider = ScriptedProvider(
            [
                {"sufficient": False, "reason": "The consequence is unclear."},
                "Please name one concrete consequence.",
            ],
            model="test-model",
        )
        unchanged_profile = scenario.interview.answer(
            scenario.run,
            answer=rejected_answer,
            actor=_REVIEWER,
            expected_question_id=third.event_id,
            provider=answer_provider,
            event_log=scenario.event_log,
            route_policy=_TEST_ROUTE_POLICY,
        )
        assert unchanged_profile == profile
        assert len(answer_provider.calls) == 1

    status = scenario.interview.begin(scenario.run, profile)
    assert status.pending_question is None
    assert status.requires_human_review is False
    assert not any(
        event.type is EventType.HUMAN_APPROVAL_REQUESTED
        for event in scenario.store.events(scenario.run.id)
    )
    scenario.controller.advance(
        scenario.run.id,
        RunTransitionKind.RESUME,
        payload={"resume_from": "information"},
    )
    final_provider, _mira, _uncertain = _run_uncertain_mira_preflight(scenario, [], profile=profile)

    assert all("allowed_fields" not in call["prompt"] for call in final_provider.calls)
    state = scenario.controller.current_state(scenario.run.id)
    assert state.wait_reason is WaitReason.APPROVAL
    approval = next(
        event
        for event in reversed(scenario.store.events(scenario.run.id))
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    request_payload = approval.payload["request_payload"]
    assert request_payload["reason"] == "risk_classification_uncertain"
    rounds_payload = request_payload["clarification_rounds"]
    assert rounds_payload["total"] == 3
    assert rounds_payload["max_total"] == 3
    assert rounds_payload["items"][-1]["answer"]["sufficiency"]["sufficient"] is False


@pytest.mark.parametrize("different_requester", [False, True])
def test_failed_gate_park_leaves_review_answerable_and_retry_reuses_question(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    different_requester: bool,
) -> None:
    """A failed transition records no profile mutation and retries the same event question."""
    run, store, interview, service, decision_id = _parked_review(tmp_path)
    prior_profile = interview.current_profile(run, _profile(run))
    original_handle = interview._control_plane.handle
    calls = 0

    def fail_before_transition(_intent: Any) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("simulated Gate interruption")

    monkeypatch.setattr(interview._control_plane, "handle", fail_before_transition)
    with pytest.raises(RuntimeError, match="simulated Gate interruption"):
        service.clarify_risk_interview(
            run.id,
            field="decision_effect",
            expected_decision_id=decision_id,
            actor=_REVIEWER,
        )

    assert RunController(store).current_state(run.id).wait_reason is WaitReason.APPROVAL
    assert interview.current_profile(run, prior_profile) == prior_profile
    first_question = next(
        event
        for event in store.events(run.id)
        if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and isinstance(event.payload.get("risk_clarification"), dict)
    )
    monkeypatch.setattr(interview._control_plane, "handle", original_handle)
    retry_actor = (
        _REVIEWER.model_copy(update={"id": "another-writer"}) if different_requester else _REVIEWER
    )
    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=decision_id,
        actor=retry_actor,
    )

    questions = [
        event
        for event in store.events(run.id)
        if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
        and isinstance(event.payload.get("risk_clarification"), dict)
    ]
    assert calls == 1
    assert len(questions) == 1
    assert questions[0].event_id == first_question.event_id
    assert questions[0].payload["requested_by"] == _REVIEWER.to_json_dict()
    assert RunController(store).current_state(run.id).wait_reason is WaitReason.INFORMATION


def test_ordinary_transition_cannot_change_an_approval_wait_to_information(
    tmp_path: Path,
) -> None:
    """The special waiting-state exception is unavailable to ordinary progress callers."""
    run, store, _, _, _ = _parked_review(tmp_path)

    with pytest.raises(InvalidRunTransitionError):
        apply_transition(
            RunController(store).current_state(run.id),
            RunTransitionCommand(
                RunTransitionKind.WAIT_FOR_INFORMATION,
                RunController(store).current_state(run.id).version,
            ),
        )


def test_approval_and_clarification_serialize_on_the_run_review_lock(tmp_path: Path) -> None:
    """The two competing decisions cannot both consume the same parked risk review."""
    run, store, _, service, decision_id = _parked_review(tmp_path)
    policy = PolicyEngine(load_default_policy())
    approval_gate = Gate(
        policy,
        RunEventLog(store, run.id),
        approver=lambda _request: True,
        human=_REVIEWER,
    )

    def clarify() -> str:
        try:
            service.clarify_risk_interview(
                run.id,
                field="decision_effect",
                expected_decision_id=decision_id,
                actor=_REVIEWER,
            )
        except ValueError:
            return "stale"
        else:
            return "clarified"

    def approve() -> str:
        try:
            service.resolve_approval(
                run.id,
                gate=approval_gate,
                actor=_REVIEWER,
                expected_decision_id=decision_id,
            )
        except ValueError:
            return "stale"
        else:
            return "approved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda action: action(), (clarify, approve)))

    assert set(outcomes) in ({"clarified", "stale"}, {"approved", "stale"})
    events = store.events(run.id)
    assert not (
        any(
            event.type is EventType.HUMAN_APPROVAL
            and event.payload.get("policy_decision_id") == decision_id
            and event.payload.get("approved") is True
            for event in events
        )
        and any(
            event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("resume_from") == "risk_clarification"
            for event in events
        )
    )


class _GraphBoundary:
    """Minimal subgraph whose calls prove a reconstructed dispatcher reached execution."""

    name = "risk-clarification-test"

    def __init__(self, calls: list[str], *, stage: str) -> None:
        self.calls = calls
        self.stage = stage

    def graph_version(self) -> str:
        """Return a fixed test graph version."""
        return "1"

    def invoke(self, state: RuntimeState, *, deps: SubgraphDeps) -> RuntimeState:
        """Record that the reconstructed runtime graph reached this stage."""
        self.calls.append(self.stage)
        if self.stage == "mira":
            prior_events = deps.event_log.events()
            prior_hash = prior_events[-1].hash if prior_events else None
            revision = sum(event.type is EventType.AUDIT_COMPLETED for event in prior_events) + 1
            deps.event_log.append(
                EventType.AUDIT_COMPLETED,
                Actor.system(),
                {
                    "status": "passed",
                    "audit_revision": revision,
                    "audit_report": {
                        "run_id": state.run_id,
                        "status": "passed",
                        "controls": [],
                        "findings": [],
                        "terminal_hash": prior_hash,
                        "policy_sha256": deps.gate.engine.policy_sha256,
                    },
                },
                subject_id=state.run_id,
                producer="test.mira",
            )
            return state.model_copy(update={"findings": ()})
        return state


class _CountingDispatcher(ExecutionDispatcher):
    """Count one lifecycle resume without running unrelated graph nodes."""

    def __init__(self) -> None:
        self.submissions = 0
        self.resumes = 0

    def submit(self, run_id: Id) -> None:
        del run_id
        self.submissions += 1

    def resume(self, run_id: Id) -> None:
        del run_id
        self.resumes += 1


def _run_service_with_dispatcher(
    tmp_path: Path,
    run: Run,
    store: LocalRunStore,
    interview: RiskInterviewService,
    dispatcher: _CountingDispatcher,
) -> RunService:
    """Build a lifecycle service around the test's durable Run store."""
    return RunService(
        store,
        SessionService(LocalSessionRepository(tmp_path / "sessions")),
        dispatcher=dispatcher,
        risk_interview=interview,
    )


def test_plain_resume_publishes_a_sufficient_answer_after_profile_append_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending answer is published before plain resume may dispatch the old profile."""
    run, store, interview, _service, decision_id = _parked_review(tmp_path)
    service = _run_service_with_dispatcher(tmp_path, run, store, interview, _CountingDispatcher())
    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=decision_id,
        actor=_REVIEWER,
    )
    question = interview.status(run, _profile(run)).pending_question
    assert question is not None
    original_append = store.append
    failed = False

    def fail_profile_publication(
        run_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        nonlocal failed
        if (
            not failed
            and event_type is EventType.ACTIVITY_PROFILE_RECORDED
            and payload is not None
            and payload.get("activity_profile", {}).get("version") == 2
        ):
            failed = True
            raise RuntimeError("simulated crash after sufficient answer")
        return original_append(run_id, event_type, actor, payload, **kwargs)

    monkeypatch.setattr(store, "append", fail_profile_publication)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.answer_risk_interview(
            run.id,
            answer="Changes review order only; a person makes each decision.",
            actor=_REVIEWER,
            expected_question_id=question.event_id,
        )
    assert interview.has_pending_question(run.id)

    service.resume(run.id, actor=_REVIEWER)

    assert interview.current_profile(run, _profile(run)).version == 2
    assert (
        sum(event.type is EventType.ACTIVITY_PROFILE_ANSWERED for event in store.events(run.id))
        == 1
    )
    assert isinstance(service._dispatcher, _CountingDispatcher)
    assert service._dispatcher.resumes == 1
    assert not interview.has_pending_question(run.id)


def test_exact_answer_retry_after_profile_append_does_not_rejudge_or_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry after profile publication reuses the event and performs only one resume."""
    run, store, interview, _service, decision_id = _parked_review(tmp_path)
    dispatcher = _CountingDispatcher()
    service = _run_service_with_dispatcher(tmp_path, run, store, interview, dispatcher)
    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=decision_id,
        actor=_REVIEWER,
    )
    question = interview.status(run, _profile(run)).pending_question
    assert question is not None
    original_append = store.append
    failed = False

    def fail_resume_transition(
        run_id: str,
        event_type: EventType,
        actor: Actor,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        nonlocal failed
        if (
            not failed
            and event_type is EventType.RUN_TRANSITIONED
            and payload is not None
            and payload.get("command") == "resume"
        ):
            failed = True
            raise RuntimeError("simulated crash before resume transition")
        return original_append(run_id, event_type, actor, payload, **kwargs)

    monkeypatch.setattr(store, "append", fail_resume_transition)
    answer_text = "Changes review order only; a person makes each decision."
    with pytest.raises(RuntimeError, match="before resume"):
        service.answer_risk_interview(
            run.id,
            answer=answer_text,
            actor=_REVIEWER,
            expected_question_id=question.event_id,
        )
    assert interview.current_profile(run, _profile(run)).version == 2
    monkeypatch.setattr(store, "append", original_append)

    def fail_judgment(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an exact retry must not judge the same answer again")

    monkeypatch.setattr(risk_interview_module, "judge_sufficiency", fail_judgment)
    service.answer_risk_interview(
        run.id,
        answer=answer_text,
        actor=_REVIEWER,
        expected_question_id=question.event_id,
    )

    assert (
        sum(event.type is EventType.ACTIVITY_PROFILE_ANSWERED for event in store.events(run.id))
        == 1
    )
    assert (
        sum(
            event.type is EventType.ACTIVITY_PROFILE_RECORDED
            and event.payload.get("activity_profile", {}).get("version") == 2
            for event in store.events(run.id)
        )
        == 1
    )
    assert dispatcher.resumes == 1


def test_insufficient_answer_crash_recovers_one_fixed_follow_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted insufficient answer reuses its judgment and publishes one next question."""
    run, store, interview, _service, decision_id = _parked_review(tmp_path)
    service = _run_service_with_dispatcher(tmp_path, run, store, interview, _CountingDispatcher())
    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=decision_id,
        actor=_REVIEWER,
    )
    question = interview.status(run, _profile(run)).pending_question
    assert question is not None
    answer_text = "Maybe."
    original_request = interview._request_question
    failed = False

    def fail_follow_up(*args: Any, **kwargs: Any) -> Any:
        nonlocal failed
        if kwargs.get("follow_up_to_answer_event_id") is not None and not failed:
            failed = True
            raise RuntimeError("simulated crash before follow-up")
        return original_request(*args, **kwargs)

    monkeypatch.setattr(
        risk_interview_module,
        "judge_sufficiency",
        lambda *_args, **_kwargs: (
            SufficiencyJudgment(
                sufficient=False,
                reason="The answer does not say whether the system decides eligibility.",
            ),
            "test",
        ),
    )
    monkeypatch.setattr(interview, "_request_question", fail_follow_up)
    with pytest.raises(RuntimeError, match="before follow-up"):
        interview.answer(
            run,
            answer=answer_text,
            actor=_REVIEWER,
            expected_question_id=question.event_id,
        )
    assert interview.has_pending_question(run.id)

    monkeypatch.setattr(interview, "_request_question", original_request)
    monkeypatch.setattr(
        risk_interview_module,
        "judge_sufficiency",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the stored judgment must be reused")
        ),
    )
    interview.answer(
        run,
        answer=answer_text,
        actor=_REVIEWER,
        expected_question_id=question.event_id,
    )

    next_question = interview.status(run, _profile(run)).pending_question
    assert next_question is not None
    assert next_question.event_id != question.event_id
    assert "does not say whether the system decides eligibility" in next_question.question
    assert (
        sum(event.type is EventType.ACTIVITY_PROFILE_ANSWERED for event in store.events(run.id))
        == 1
    )


@pytest.mark.parametrize("repark", [False, True])
def test_old_answer_retry_is_rejected_after_follow_up_question_is_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repark: bool,
) -> None:
    """An exact old answer retry cannot resume a Run while its follow-up is unanswered."""
    run, store, interview, _service, decision_id = _parked_review(tmp_path)
    dispatcher = _CountingDispatcher()
    service = _run_service_with_dispatcher(tmp_path, run, store, interview, dispatcher)
    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=decision_id,
        actor=_REVIEWER,
    )
    first_question = interview.status(run, _profile(run)).pending_question
    assert first_question is not None
    answer = "Maybe."
    monkeypatch.setattr(
        risk_interview_module,
        "judge_sufficiency",
        lambda *_args, **_kwargs: (
            SufficiencyJudgment(
                sufficient=False,
                reason="The answer does not say whether the system decides eligibility.",
            ),
            "test",
        ),
    )
    interview.answer(
        run,
        answer=answer,
        actor=_REVIEWER,
        expected_question_id=first_question.event_id,
    )
    follow_up = interview.status(run, _profile(run)).pending_question
    assert follow_up is not None
    assert follow_up.event_id != first_question.event_id
    if repark:
        controller = RunController(store)
        controller.advance(
            run.id,
            RunTransitionKind.RESUME,
            payload={"resume_from": "information"},
        )
        interview._ensure_waiting_for_information(run.id, follow_up)
    event_count = len(store.events(run.id))

    with pytest.raises(ValueError, match=r"(?i)(question|current|stale|answered)"):
        service.answer_risk_interview(
            run.id,
            answer=answer,
            actor=_REVIEWER,
            expected_question_id=first_question.event_id,
        )

    assert len(store.events(run.id)) == event_count
    assert dispatcher.resumes == 0
    assert interview.status(run, _profile(run)).pending_question == follow_up


def test_answer_reconstructs_graph_reclassifies_profile_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real dispatcher resumes after a human correction and classifies its new profile."""
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Sort synthetic demo records for display; no real decisions are involved.",
        model_route_policy=_TEST_ROUTE_POLICY,
    )
    store = LocalRunStore(tmp_path / "runs")
    store.create(run, actor=Actor.system())
    policy = PolicyEngine(load_default_policy())
    interview = RiskInterviewService(store, MiraControlPlane(store, policy))
    # Keep the post-clarification example deliberately low impact. The separate explicit
    # execution.start rule below must be enough for the run to continue after fresh
    # classification; a profile that affects credit applicants correctly triggers the
    # classifier's independent human-review safeguard even under a PASS start rule.
    profile = _profile(run).model_copy(
        update={
            "purpose": "Organize synthetic status records for a demo.",
            "affected_population": "none",
            "decision_effect": "Changes only the display order in the demo.",
            "autonomy": "Automatic sorting in a disposable demo.",
            "human_oversight": "The demo owner can reset the display order.",
            "jurisdiction": "Not applicable to synthetic demo records.",
            "data_categories": ("synthetic_status_labels",),
            "potential_consequences": ("No real person or organization is affected.",),
        }
    )
    controller = RunController(store)
    checkpoints = LocalCheckpointRepository(tmp_path / "checkpoints")
    tool_manager = ToolManager(ToolRegistry())
    deps = SubgraphDeps(
        event_log=RunEventLog(store, run.id),
        gate=Gate(PolicyEngine(_pass_start_policy()), RunEventLog(store, run.id)),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", run.id),
        tool_manager=tool_manager,
        record_repository=LocalRecordRepository(tmp_path / "records"),
        usage_ledger=UsageLedger(),
    )
    calls: list[str] = []
    preflight_calls = 0
    provider = ScriptedProvider(
        [
            {
                "risk_level": "low",
                "activity_category": "decision_support",
                "risk_factors": (),
                "missing_information": (),
                "confidence": 0.9,
                "needs_human_review": False,
                "summary": "Supports a human review queue.",
                "criteria": (),
            }
        ]
    )

    def interview_node(state: RuntimeState, _deps: SubgraphDeps) -> RuntimeState:
        interview.begin(run, profile)
        return state

    def preflight_node(state: RuntimeState, runtime_deps: SubgraphDeps) -> RuntimeState:
        nonlocal preflight_calls
        preflight_calls += 1
        if preflight_calls == 1:
            review_gate = Gate(
                PolicyEngine(_require_start_review_policy()),
                runtime_deps.event_log,
            )
            decision = review_gate.authorize_execution(
                RiskProfile(
                    risk_level="limited",
                    activity_category="analysis",
                    confidence=1.0,
                ),
                (),
            )
            controller.park_for_review(
                run.id,
                decision,
                resume_from="execution_start",
            )
        return state

    def classify_node(state: RuntimeState, runtime_deps: SubgraphDeps) -> RuntimeState:
        classified = interview.classify(
            run,
            profile,
            runtime_deps.event_log,
            provider,
            route_policy=_TEST_ROUTE_POLICY,
        )
        return state.model_copy(update={"risk_profile": classified})

    def execution_gate(state: RuntimeState, runtime_deps: SubgraphDeps) -> RuntimeState:
        decision = runtime_deps.gate.authorize_execution(
            state.risk_profile or RiskProfile(),
            runtime_deps.tool_manager.capabilities(),
        )
        return state.model_copy(
            update={
                "execution_constraints": decision.execution_constraints,
                "execution_decision": decision,
            }
        )

    def factory(_run: Run) -> Any:
        """Rebuild a fresh composed graph while retaining its durable checkpointer."""
        return build_runtime_graph(
            _GraphBoundary(calls, stage="thy"),
            _GraphBoundary(calls, stage="mira"),
            checkpointer=StateCheckpointer(checkpoints),
            deps=deps,
            controller=controller,
            interview=interview_node,
            preflight=preflight_node,
            classify=classify_node,
            execution_gate=execution_gate,
        )

    dispatcher = InlineDispatcher(store, factory)
    service = RunService(
        store,
        SessionService(LocalSessionRepository(tmp_path / "sessions")),
        dispatcher=dispatcher,
        risk_interview=interview,
    )
    dispatcher.submit(run.id)
    parked_decision = next(
        event.payload["policy_decision"]["id"]
        for event in reversed(store.events(run.id))
        if event.type is EventType.RUN_TRANSITIONED
        and event.payload.get("command") == "wait_for_approval"
    )
    service.clarify_risk_interview(
        run.id,
        field="decision_effect",
        expected_decision_id=parked_decision,
        actor=_REVIEWER,
    )
    question = interview.status(run, profile).pending_question
    assert question is not None
    assert question.field == "decision_effect"

    service.answer_risk_interview(
        run.id,
        answer=(
            "Changes only which synthetic demo record appears first; no real decision is affected."
        ),
        actor=_REVIEWER,
        expected_question_id=question.event_id,
    )

    events = store.events(run.id)
    classifications = [event for event in events if event.type is EventType.RISK_CLASSIFIED]
    assert len(classifications) == 1
    assert classifications[0].payload["activity_profile_version"] == 2
    assert set(calls) == {"thy", "mira"}
    assert not any(event.type is EventType.HUMAN_APPROVAL for event in events)
    new_execution_decisions = [
        event
        for event in events
        if event.type is EventType.POLICY_DECISION and event.payload.get("rule_id") == "START-PASS"
    ]
    assert len(new_execution_decisions) == 1
    assert new_execution_decisions[0].payload["id"] != parked_decision
    assert RunController(store).current_state(run.id).outcome is RunOutcome.COMPLETED
    assert verify_events(events).valid
