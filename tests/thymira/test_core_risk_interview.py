"""Unit and composition tests for the event-backed activity-profile interview."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

import thymira.core.risk_interview_model as interview_model
from tests.thymira.api_support import TEST_CREDENTIAL
from thymira.agents import ModelRouteDeniedError, RequestLedger, RiskClassification
from thymira.agents.llm import LLMResponse, ScriptedProvider
from thymira.agents.risk import CLASSIFICATION_FAILED_MARKER
from thymira.api import RuntimeDeps, build_default_deps
from thymira.core import (
    GraphFactory,
    MiraControlPlane,
    RiskInterviewService,
    RunController,
    RunTransitionKind,
    build_runtime_graph_factory,
    default_activity_profile,
)
from thymira.core.control_plane import RunEventLog
from thymira.core.risk_uncertainty_questions import RiskUncertaintyQuestionSelection
from thymira.events import InMemoryEventLog, verify_events
from thymira.mira.checks import replay_request_ledger
from thymira.policies import PolicyEngine, load_default_policy
from thymira.schemas import (
    ActivityProfile,
    Actor,
    ActorKind,
    ConversationInput,
    EventType,
    ModelRoutePolicy,
    ResponseOutcome,
    RiskLevel,
    Run,
    WaitReason,
    new_id,
)
from thymira.state import LocalRunStore
from thymira.thy.agents import full_agent_catalog
from thymira.tools import ToolManager

if TYPE_CHECKING:
    from pathlib import Path

    from langgraph.graph.state import CompiledStateGraph


TEST_ROUTE_POLICY = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")


@pytest.fixture(autouse=True)
def _configured_test_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind direct interview calls to one deterministic allowed route."""
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")


def _interview(
    tmp_path: Path,
    responses: list[Any],
    *,
    max_questions: int = 12,
) -> tuple[Run, RiskInterviewService, ScriptedProvider, RunEventLog, LocalRunStore]:
    """Build one started Run and an offline interview service."""
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="analyze",
        model_route_policy=TEST_ROUTE_POLICY,
    )
    store = LocalRunStore(tmp_path / "runs")
    store.create(run, actor=Actor.system())
    controller = RunController(store)
    controller.advance(run.id, RunTransitionKind.START)
    service = RiskInterviewService(
        store,
        MiraControlPlane(store, PolicyEngine(load_default_policy())),
        max_questions=max_questions,
    )
    provider = ScriptedProvider(responses, model="test-model")
    return run, service, provider, RunEventLog(store, run.id), store


def _complete_profile(run: Run) -> ActivityProfile:
    """Return a profile whose nine fixed interview facts are explicitly declared."""
    return default_activity_profile(run).model_copy(
        update={
            "purpose": "Profile credit applications.",
            "affected_population": "Credit applicants.",
            "decision_effect": "Changes review order but not credit approval.",
            "autonomy": "Recommendation only.",
            "human_oversight": "An analyst can override every recommendation.",
            "jurisdiction": "EU",
            "data_categories": ("credit_history",),
            "sensitive_attributes": (),
            "potential_consequences": ("An urgent application could be reviewed late.",),
        }
    )


def test_uncertain_execution_classification_asks_a_model_question_before_review(tmp_path):
    question = "¿El análisis se usará solo para investigación o para decidir sobre solicitantes?"
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.HIGH,
                activity_category="model_development",
                confidence=0.75,
                missing_information=("decision_effect",),
                needs_human_review=False,
                summary="The decision effect needs clarification.",
            ),
            RiskUncertaintyQuestionSelection(field="decision_effect", question=question),
        ],
    )
    profile = _complete_profile(run).model_copy(
        update={"sensitive_attributes": ("age", "sex"), "decision_effect": "undeclared"}
    )
    risk = service.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY)

    assert risk.needs_human_review is True
    pending = service.status(run, profile).pending_question
    assert pending is not None
    assert pending.question == question
    assert risk.needs_human_review is True
    assert RunController(store).current_state(run.id).wait_reason is WaitReason.INFORMATION
    restored = RiskInterviewService(
        store, MiraControlPlane(store, PolicyEngine(load_default_policy()))
    )
    assert (
        restored.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY) == risk
    )
    assert restored.status(run, profile).pending_question == pending
    assert sum(e.type is EventType.ACTIVITY_PROFILE_QUESTIONED for e in store.events(run.id)) == 1
    assert not any(e.type is EventType.TOOL_STARTED for e in store.events(run.id))
    assert verify_events(store.events(run.id)).valid


def test_complete_sensitive_profile_keeps_review_without_repeating_interview_question(
    tmp_path: Path,
) -> None:
    """Sensitivity can require review without making already-known facts incomplete."""
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.HIGH,
                activity_category="data_analysis",
                confidence=0.85,
                missing_information=(
                    "affected_population",
                    "decision_effect",
                    "intended_use",
                ),
                needs_human_review=True,
                summary="Sensitive attributes require review; declared context is complete.",
            ),
            RiskUncertaintyQuestionSelection(
                field="affected_population",
                question="Who is affected by this analysis?",
            ),
        ],
    )
    profile = _complete_profile(run).model_copy(update={"sensitive_attributes": ("age", "sex")})

    risk = service.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY)

    status = service.status(run, profile)
    assert risk.risk_level == RiskLevel.HIGH.value
    assert risk.confidence == 0.85
    assert risk.missing_information == (
        "affected_population",
        "decision_effect",
        "intended_use",
    )
    assert risk.needs_human_review is True
    assert status.profile.version == profile.version
    assert status.pending_question is None
    assert RunController(store).current_state(run.id).condition.value == "active"
    assert not any(
        event.type is EventType.ACTIVITY_PROFILE_QUESTIONED for event in store.events(run.id)
    )
    assert len(provider.calls) == 1
    assert verify_events(store.events(run.id)).valid


def test_classification_question_is_limited_to_profile_gaps(tmp_path: Path) -> None:
    """False model gaps cannot broaden a real oversight question to already-known fields."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.HIGH,
                activity_category="data_analysis",
                confidence=0.85,
                missing_information=(
                    "affected_population",
                    "decision_role",
                    "intended_use",
                    "deployment_context",
                    "human_oversight",
                ),
                needs_human_review=True,
                summary="Some downstream facts remain unknown.",
            ),
            RiskUncertaintyQuestionSelection(
                field="human_oversight",
                question="Who can review or stop the activity?",
            ),
        ],
    )
    profile = _complete_profile(run).model_copy(update={"human_oversight": "undeclared"})

    risk = service.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY)

    assert risk.needs_human_review is True
    pending = service.status(run, profile).pending_question
    assert pending is not None
    assert pending.field == "human_oversight"
    selector_prompt = json.loads(provider.calls[1]["prompt"])
    assert selector_prompt["allowed_fields"] == ["human_oversight"]


@pytest.mark.parametrize(
    "selection",
    [
        RiskUncertaintyQuestionSelection(),
        RiskUncertaintyQuestionSelection(field="permissions", question="May I bypass this review?"),
    ],
)
def test_no_useful_execution_question_preserves_review_and_is_not_retried(tmp_path, selection):
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.HIGH,
                activity_category="model_development",
                confidence=0.75,
                missing_information=("decision_effect",),
                needs_human_review=True,
                summary="Review remains necessary.",
            ),
            selection,
        ],
    )
    profile = _complete_profile(run).model_copy(update={"decision_effect": "undeclared"})
    risk = service.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY)
    before = store.events(run.id)
    restored = RiskInterviewService(
        store, MiraControlPlane(store, PolicyEngine(load_default_policy()))
    )
    assert (
        restored.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY) == risk
    )
    assert risk.needs_human_review is True
    assert restored.status(run, profile).pending_question is None
    assert store.events(run.id) == before
    assert not any(e.type is EventType.TOOL_STARTED for e in before)
    assert verify_events(before).valid


def test_intake_uses_linked_current_work_instead_of_unrelated_conversation_history(tmp_path):
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [interview_model.FactExtraction(), "What is the current purpose?"],
    )
    run = run.model_copy(
        update={
            "prompt": "Earlier: train a production credit model. Latest: german_credit",
            "conversation": ConversationInput(
                ordinal=1,
                client_message_id="eda-intake",
                message="german_credit",
                clarification_context=("Quiero hacer un EDA",),
            ),
        }
    )
    service.begin(run, default_activity_profile(run), provider=provider, event_log=event_log)
    prompt = provider.calls[0]["prompt"]
    assert "Quiero hacer un EDA" in prompt
    assert "german_credit" in prompt
    assert "production credit model" not in prompt


def test_execution_question_recovers_after_selection_is_saved_before_question(
    tmp_path, monkeypatch
):
    question = "What concrete decision can this analysis affect?"
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.HIGH,
                activity_category="model_development",
                confidence=0.75,
                missing_information=("decision_effect",),
                needs_human_review=True,
                summary="Clarify downstream decision effects.",
            ),
            RiskUncertaintyQuestionSelection(field="decision_effect", question=question),
        ],
    )
    original_append = store.append

    def interrupt_question(run_id, event_type, *args, **kwargs):
        if event_type is EventType.ACTIVITY_PROFILE_QUESTIONED:
            raise OSError("simulated interruption before question publication")
        return original_append(run_id, event_type, *args, **kwargs)

    profile = _complete_profile(run).model_copy(update={"decision_effect": "undeclared"})
    with monkeypatch.context() as patch:
        patch.setattr(store, "append", interrupt_question)
        with pytest.raises(OSError, match="before question publication"):
            service.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY)
    selected = [
        e
        for e in store.events(run.id)
        if e.payload.get("kind") == "risk.classification_clarification"
    ]
    assert len(selected) == 1
    restored_store = LocalRunStore(tmp_path / "runs")
    restored = RiskInterviewService(
        restored_store,
        MiraControlPlane(restored_store, PolicyEngine(load_default_policy())),
    )
    restored.classify(
        run, profile, RunEventLog(restored_store, run.id), provider, route_policy=TEST_ROUTE_POLICY
    )
    pending = restored.status(run, profile).pending_question
    assert pending is not None
    assert pending.question == question
    assert sum(e.type is EventType.RISK_CLASSIFIED for e in restored_store.events(run.id)) == 1
    assert verify_events(restored_store.events(run.id)).valid


def test_complete_profile_skips_model_context_extraction(tmp_path: Path) -> None:
    """Known interview facts do not spend a model call asking for an empty extraction."""
    run, service, provider, event_log, _store = _interview(tmp_path, [])
    profile = _complete_profile(run)

    status = service.begin(run, profile, provider=provider, event_log=event_log)

    assert status.profile == profile
    assert status.pending_question is None
    assert provider.calls == []
    assert not any(event.type is EventType.MODEL_SELECTED for event in event_log.events())
    assert verify_events(event_log.events()).valid


def test_complete_profile_autonomy_satisfies_deployment_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declared autonomy supplies the classifier's equivalent deployment-context fact."""
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.MEDIUM,
                activity_category="data_analysis",
                confidence=0.99,
                needs_human_review=False,
                summary="Bounded local analysis with indirect impact on represented applicants.",
            )
        ],
    )
    profile = _complete_profile(run)
    route_policy = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-test")

    risk = service.classify(
        run,
        profile,
        event_log,
        provider,
        route_policy=route_policy,
    )

    assert risk.missing_information == ()
    assert risk.needs_human_review is False
    assert '"deployment_context"' in provider.calls[0]["prompt"]
    assert f"\n{run.prompt}\n" in provider.calls[0]["prompt"]
    for value in (
        profile.affected_population,
        profile.jurisdiction,
        *profile.data_categories,
        *profile.potential_consequences,
    ):
        assert value in provider.calls[0]["prompt"]
    assert verify_events(event_log.events()).valid


def test_vague_prompt_still_asks_purpose_and_model_sees_answered_context(tmp_path: Path) -> None:
    """The prompt text cannot seed purpose, and generated phrasing remains field-specific."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [{"facts": []}, "State the concrete purpose of the activity."],
    )

    status = service.begin(
        run,
        default_activity_profile(run),
        provider=provider,
        event_log=event_log,
    )

    assert status.pending_question is not None
    assert status.pending_question.field == "purpose"
    assert status.pending_question.question == "State the concrete purpose of the activity."
    assert "Run request:\nanalyze" in provider.calls[0]["prompt"]
    question_event = next(
        event for event in event_log.events() if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
    )
    assert question_event.payload["question_source"] == "model"
    question_call = next(
        call
        for call in provider.calls
        if "Current activity-profile field: purpose" in call["prompt"]
    )
    assert "Already answered fields:" in question_call["prompt"]
    assert any(event.type is EventType.MODEL_SELECTED for event in event_log.events())
    assert verify_events(event_log.events()).valid


@pytest.mark.parametrize(
    ("route_policy", "reason"),
    [
        (None, "route_policy_unavailable"),
        (
            ModelRoutePolicy.from_routes(("other-model",), authority="code-owned-tests"),
            "route_not_allowed",
        ),
    ],
)
def test_interview_route_denial_precedes_gate_and_provider(
    route_policy: ModelRoutePolicy | None,
    reason: str,
) -> None:
    """A missing or denied route is durable before either Gate or provider can run."""
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Interview the activity.",
    )
    event_log = InMemoryEventLog(run.id)
    provider = ScriptedProvider(["This response must remain unused."])
    gate_calls: list[str] = []
    context = interview_model.RiskInterviewModelContext(
        provider=provider,
        event_log=event_log,
        before_model_call=lambda _choice: gate_calls.append("gate"),
        route_policy=route_policy,
    )

    with pytest.raises(ModelRouteDeniedError):
        interview_model.generate_question(
            "purpose",
            "What is the concrete purpose?",
            ("purpose",),
            default_activity_profile(run),
            context,
        )

    assert provider.calls == []
    assert gate_calls == []
    assert [event.type for event in event_log.events()] == [
        EventType.MODEL_SELECTED,
        EventType.MODEL_ROUTE_DENIED,
    ]
    assert event_log.events()[-1].payload["reason"] == reason


def test_question_model_failure_uses_the_static_fallback_without_blocking(tmp_path: Path) -> None:
    """An unavailable phrasing model leaves the original static question and continues."""
    run, service, provider, event_log, _store = _interview(tmp_path, [])

    status = service.begin(
        run,
        default_activity_profile(run),
        provider=provider,
        event_log=event_log,
    )

    assert status.pending_question is not None
    assert status.pending_question.field == "purpose"
    assert status.pending_question.question == "What is the concrete purpose of this activity?"
    question_event = next(
        event for event in event_log.events() if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
    )
    assert question_event.payload["question_source"] == "static_fallback"
    assert verify_events(event_log.events()).valid


def test_every_interview_model_channel_scrubs_credentials_before_provider_and_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four interview prompts and system messages are scrubbed at their source boundary."""
    secret = "sk-aB3dEf0gHi1jKl2mNo3pQr4sT5u"  # noqa: S105  # fixture credential
    marker = "[REDACTED:CREDENTIAL]"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    for name in (
        "_EXTRACTION_SYSTEM_PROMPT",
        "_QUESTION_SYSTEM_PROMPT",
        "_JUDGMENT_SYSTEM_PROMPT",
        "_FOLLOW_UP_SYSTEM_PROMPT",
    ):
        monkeypatch.setattr(interview_model, name, f"System instruction contains {secret}")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Interview this activity.",
    )
    profile = default_activity_profile(run).model_copy(
        update={"human_oversight": f"A reviewer holds {secret}"}
    )
    event_log = InMemoryEventLog(run.id)
    provider = ScriptedProvider(
        [
            {"facts": []},
            "What is the concrete purpose?",
            {"sufficient": False, "reason": "The concrete outcome is missing."},
            "Which concrete outcome should the activity achieve?",
        ]
    )
    context = interview_model.RiskInterviewModelContext(
        provider=provider,
        event_log=event_log,
        request_ledger=RequestLedger(event_log),
        route_policy=TEST_ROUTE_POLICY,
    )
    fields = ("purpose", "human_oversight")

    interview_model.extract_facts(
        ("purpose",),
        {"purpose": "What is the concrete purpose?"},
        f"Project context contains {secret}",
        fields,
        profile,
        context,
    )
    interview_model.generate_question(
        "purpose", "What is the concrete purpose?", fields, profile, context
    )
    interview_model.judge_sufficiency(
        "purpose",
        "What is the concrete purpose?",
        f"The candidate answer contains {secret}",
        fields,
        profile,
        context,
    )
    interview_model.generate_follow_up(
        "purpose",
        "What is the concrete purpose?",
        f"The candidate answer contains {secret}",
        f"The reason contains {secret}",
        fields,
        profile,
        context,
    )

    assert len(provider.calls) == 4
    assert all(secret not in str(call) for call in provider.calls)
    assert all(marker in call["prompt"] for call in provider.calls)
    assert all(call["system"] == f"System instruction contains {marker}" for call in provider.calls)
    events = event_log.events()
    assert all(secret not in event.model_dump_json() for event in events)
    ledger_inputs = [
        event
        for event in events
        if event.type
        in {
            EventType.MODEL_INPUT_HEADER_REVISED,
            EventType.MODEL_INPUT_SURFACE_UPDATED,
            EventType.MODEL_REQUEST_RECORDED,
        }
    ]
    assert ledger_inputs
    assert all(marker in event.model_dump_json() for event in ledger_inputs)


def test_invalid_context_extraction_is_charged_then_interview_continues(tmp_path: Path) -> None:
    """A malformed returned extraction is billed before the static interview continues."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [{"unexpected": []}, "Please state the concrete purpose."],
    )
    recorded: list[LLMResponse] = []
    request_ledger = RequestLedger(event_log)

    status = service.begin(
        run,
        default_activity_profile(run),
        provider=provider,
        event_log=event_log,
        record_model_usage=recorded.append,
        request_ledger=request_ledger,
    )

    replay = replay_request_ledger(tuple(event_log.events()))
    assert status.pending_question is not None
    assert status.pending_question.question == "Please state the concrete purpose."
    assert len(recorded) == 2
    assert recorded[0].metadata["schema"] == "FactExtraction"
    assert len(replay.requests) == 2
    assert [response.outcome for response in replay.responses] == [
        ResponseOutcome.PARSE_FAILURE,
        ResponseOutcome.SUCCESS,
    ]
    assert replay.in_flight == ()


def test_invalid_context_extraction_counts_towards_the_next_request_limit(
    tmp_path: Path,
) -> None:
    """The next interview call sees a returned invalid extraction as already spent usage."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [{"unexpected": []}, "This second response must remain unused."],
    )
    recorded: list[LLMResponse] = []

    def reject_after_one_request() -> None:
        if recorded:
            raise RuntimeError("model request limit reached")

    with pytest.raises(RuntimeError, match="model request limit reached"):
        service.begin(
            run,
            default_activity_profile(run),
            provider=provider,
            event_log=event_log,
            before_model_selection=reject_after_one_request,
            record_model_usage=recorded.append,
        )

    assert len(recorded) == 1
    assert len(provider.calls) == 1


def test_insufficient_answer_gets_one_directed_follow_up_and_keeps_profile_version(
    tmp_path: Path,
) -> None:
    """A weak answer closes one turn, records its judgment, and opens one follow-up."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [
            {"facts": []},
            "What is the concrete purpose?",
            {"sufficient": False, "reason": "It does not state the activity outcome."},
            "Which concrete outcome should the activity achieve?",
            {"sufficient": True, "reason": "The answer states the activity outcome."},
        ],
    )
    initial = default_activity_profile(run)
    service.begin(run, initial, provider=provider, event_log=event_log)
    human = Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=False)

    unchanged = service.answer(
        run,
        answer="It is about data.",
        actor=human,
        provider=provider,
        event_log=event_log,
    )

    assert unchanged.version == 1
    pending = service.status(run, initial).pending_question
    assert pending is not None
    assert pending.field == "purpose"
    assert pending.question == "Which concrete outcome should the activity achieve?"
    questions = [
        event for event in event_log.events() if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
    ]
    assert [event.payload["question_number"] for event in questions] == [1, 2]
    insufficient = next(
        event for event in event_log.events() if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
    )
    assert insufficient.payload["answer_source"] == "live_human"
    assert insufficient.payload["sufficiency"] == {
        "reason": "It does not state the activity outcome.",
        "sufficient": False,
    }
    follow_up_prompt = provider.calls[3]["prompt"]
    assert "Canonical activity-profile fact:" in follow_up_prompt
    assert "Previous human-facing question (context only):" in follow_up_prompt
    assert "Earlier question-answer turns for this field" in follow_up_prompt

    updated = service.answer(
        run,
        answer="It prioritises urgent applications for human review.",
        actor=human,
        provider=provider,
        event_log=event_log,
    )

    assert updated.version == 2
    assert updated.purpose == "It prioritises urgent applications for human review."
    assert verify_events(event_log.events()).valid


def test_generated_mitigation_question_does_not_expand_the_consequence_fact(
    tmp_path: Path,
) -> None:
    """MIRA-selected question wording cannot expand the persisted fact requirement."""
    mitigation_question = (
        "What specific measures will be put in place to mitigate the risk of misleading "
        "analysis due to the use of this historical dataset?"
    )
    answer = (
        "Este es un EDA de test, no va a tener efecto sobre ningun tipo de persona fisica, "
        "simplemente para explorar un poco el dataset."
    )
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.HIGH,
                activity_category="model_development",
                confidence=0.75,
                missing_information=("potential_consequences",),
                needs_human_review=True,
                summary="The inherent risk needs clarification.",
            ),
            {
                "field": "potential_consequences",
                "question": mitigation_question,
            },
            {"sufficient": True, "reason": "The answer describes the scoped consequences."},
        ],
    )
    run = run.model_copy(update={"prompt": "Run an exploratory data analysis of german_credit."})
    profile = _complete_profile(run).model_copy(update={"potential_consequences": ("undeclared",)})
    risk = service.classify(
        run,
        profile,
        event_log,
        provider,
        route_policy=TEST_ROUTE_POLICY,
    )
    pending = service.status(run, profile).pending_question
    assert pending is not None
    assert pending.field == "potential_consequences"
    assert risk.needs_human_review is True

    updated = service.answer(
        run,
        answer=answer,
        actor=Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        expected_question_id=pending.event_id,
        provider=provider,
        event_log=event_log,
    )

    assert updated.version == 2
    assert updated.potential_consequences == tuple(part.strip() for part in answer.split(","))
    judgment_system = " ".join(provider.calls[2]["system"].split())
    assert "An explicit scoped denial is a supplied fact" in judgment_system
    assert "even if it contradicts a project-derived value" in judgment_system
    assert "risk assessment and policy must evaluate it independently" in judgment_system
    selection_prompt = provider.calls[1]["prompt"]
    assert "allowed_field_definitions" in selection_prompt
    assert "inherent impact; it does not ask for controls" in selection_prompt
    assert "mitigation measures are assessed separately" in " ".join(
        provider.calls[1]["system"].split()
    )
    judgment_prompt = provider.calls[2]["prompt"]
    assert "Canonical activity-profile fact:" in judgment_prompt
    assert "Question shown to the human (context only):" in judgment_prompt
    assert "Required fact question:" not in judgment_prompt
    assert "controls, safeguards, or mitigations" in judgment_prompt
    assert mitigation_question in judgment_prompt
    assert run.prompt in judgment_prompt
    assert answer in judgment_prompt
    answer_event = next(
        event
        for event in store.events(run.id)
        if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
        and event.payload.get("question_event_id") == pending.event_id
    )
    assert answer_event.payload["answer"] == answer
    assert answer_event.payload["sufficiency"]["sufficient"] is True
    assert verify_events(event_log.events()).valid

    # Reopen the service from the event-backed store and inspect the next real classifier call.
    reopened_service = RiskInterviewService(
        store,
        MiraControlPlane(store, PolicyEngine(load_default_policy())),
    )
    persisted_profile = reopened_service.status(run, profile).profile
    assert persisted_profile.version == updated.version
    assert persisted_profile.potential_consequences == updated.potential_consequences
    classifier = ScriptedProvider(
        [
            RiskClassification(
                risk_level=RiskLevel.MEDIUM,
                activity_category="data_analysis",
                confidence=0.8,
                needs_human_review=False,
                summary="Local exploratory analysis with no declared person-level effect.",
            )
        ],
        model="test-model",
    )
    reopened_service.classify(
        run,
        persisted_profile,
        RunEventLog(store, run.id),
        classifier,
        route_policy=TEST_ROUTE_POLICY,
    )
    assert len(classifier.calls) == 1
    classification_prompt = classifier.calls[0]["prompt"]
    classification_system = classifier.calls[0]["system"]
    for consequence in persisted_profile.potential_consequences:
        assert consequence in classification_prompt
    assert "potential_consequences" in classification_prompt
    assert "Classifier prompt reference: risk-classifier-v3" in classification_prompt
    assert "Activity categories:" in classification_system
    assert "controls, safeguards, or mitigations" not in classification_prompt.casefold()
    assert "controls, safeguards, or mitigations" not in classification_system.casefold()


def test_invalid_sufficiency_judgment_is_charged_before_accepting_human_fallback(
    tmp_path: Path,
) -> None:
    """A malformed optional judgment is billed while the live human answer remains usable."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [
            {"facts": []},
            "What is the concrete purpose?",
            {"sufficient": True},
        ],
    )
    recorded: list[LLMResponse] = []
    service.begin(
        run,
        default_activity_profile(run),
        provider=provider,
        event_log=event_log,
        record_model_usage=recorded.append,
    )

    updated = service.answer(
        run,
        answer="Profile applications for a human analyst.",
        actor=Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        provider=provider,
        event_log=event_log,
        record_model_usage=recorded.append,
    )

    assert updated.purpose == "Profile applications for a human analyst."
    assert len(recorded) == 3
    assert recorded[-1].metadata["schema"] == "SufficiencyJudgment"


def test_no_live_decision_answer_is_judged_with_the_request_and_existing_profile(
    tmp_path: Path,
) -> None:
    """A scoped no-live-decision answer is evaluated beside the exact request and profile."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [
            {
                "sufficient": True,
                "reason": "The answer explicitly rules out live decision effects.",
            }
        ],
    )
    profile = _complete_profile(run).model_copy(
        update={
            "purpose": "An offline test of a credit-risk dataset.",
            "decision_effect": "No live decision effect; offline test only.",
            "potential_consequences": ("None; this test has no operational consequence.",),
        }
    )
    service.begin(run, profile)
    question = service.clarify_field(run, field="decision_effect")
    assert question is not None

    answer = "This is an offline test; no model result influences any live decision."
    updated = service.answer(
        run,
        answer=answer,
        actor=Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        expected_question_id=question.event_id,
        provider=provider,
        event_log=event_log,
    )

    assert updated.version == 2
    judgment_prompt = provider.calls[0]["prompt"]
    assert run.prompt in judgment_prompt
    assert profile.decision_effect in judgment_prompt
    assert "Full current activity profile" in judgment_prompt
    assert answer in judgment_prompt


def test_insufficient_answer_still_hits_the_existing_question_limit(tmp_path: Path) -> None:
    """A failed sufficiency judgment cannot create an unbounded follow-up loop."""
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            {"facts": []},
            "What is the purpose?",
            {"sufficient": False, "reason": "The answer is vague."},
        ],
        max_questions=1,
    )
    profile = default_activity_profile(run)
    service.begin(run, profile, provider=provider, event_log=event_log)

    unchanged = service.answer(
        run,
        answer="Something with data.",
        actor=Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=False),
        provider=provider,
        event_log=event_log,
    )
    controller = RunController(store)
    controller.advance(run.id, RunTransitionKind.RESUME, payload={"resume_from": "information"})

    status = service.begin(run, profile)

    assert unchanged.version == 1
    assert status.requires_human_review is True
    assert status.pending_question is None
    assert (
        sum(event.type is EventType.ACTIVITY_PROFILE_QUESTIONED for event in event_log.events())
        == 1
    )
    review = next(
        event
        for event in event_log.events()
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
        and isinstance(event.payload.get("request_payload"), dict)
        and event.payload["request_payload"].get("review_kind") == "missing_activity_profile"
    )
    request_payload = review.payload["request_payload"]
    assert request_payload["reason"] == "total_question_limit"
    assert request_payload["field"] == "purpose"
    assert request_payload["questions_asked"] == {
        "total": 1,
        "max_total": 1,
        "field_total": 1,
        "max_per_field": 3,
    }
    assert request_payload["last_question"]["question"] == "What is the purpose?"
    assert request_payload["last_answer"]["answer"] == "Something with data."
    assert request_payload["last_answer"]["sufficiency"] == {
        "sufficient": False,
        "reason": "The answer is vague.",
    }
    assert "purpose" in request_payload["remaining_fields"]
    assert set(request_payload["decision_effects"]) == {"approve", "reject"}
    limit_park = next(
        event
        for event in reversed(event_log.events())
        if event.type is EventType.RUN_TRANSITIONED
        and event.payload.get("command") == "wait_for_approval"
    )
    assert limit_park.payload["resume_from"] == "interview_limit"
    assert verify_events(event_log.events()).valid


def test_status_clears_question_limit_review_after_a_human_answer(tmp_path: Path) -> None:
    """A resolved limit review is history, not a still-pending status action."""
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            {"facts": []},
            "What is the purpose?",
            {"sufficient": False, "reason": "The answer is vague."},
        ],
        max_questions=1,
    )
    profile = default_activity_profile(run)
    service.begin(run, profile, provider=provider, event_log=event_log)
    service.answer(
        run,
        answer="Something with data.",
        actor=Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        provider=provider,
        event_log=event_log,
    )
    RunController(store).advance(
        run.id,
        RunTransitionKind.RESUME,
        payload={"resume_from": "information"},
    )
    service.begin(run, profile)
    request = next(
        event for event in event_log.events() if event.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    event_log.append(
        EventType.HUMAN_APPROVAL,
        Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        {
            "approved": True,
            "automatic": False,
            "decision_id": request.payload["decision_id"],
        },
    )

    status = service.status(run, profile)

    assert status.pending_question is None
    assert status.requires_human_review is False
    assert verify_events(event_log.events()).valid


def test_repeated_insufficient_answers_hit_a_per_field_limit(tmp_path: Path) -> None:
    """One unresolved fact cannot consume the entire global interview question budget."""
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            {"facts": []},
            "Which jurisdiction governs the activity?",
            {"sufficient": False, "reason": "No national jurisdiction is stated."},
            "Which member state governs the activity?",
            {"sufficient": False, "reason": "No member state is stated."},
            "Where is the organization established?",
            {"sufficient": False, "reason": "The establishment is not stated."},
        ],
    )
    profile = default_activity_profile(run).model_copy(
        update={
            "purpose": "Analyze credit data.",
            "affected_population": "Credit applicants.",
            "decision_effect": "Inform later human analysis.",
            "autonomy": "EDA only.",
            "human_oversight": "A data scientist reviews the report.",
        }
    )
    service.begin(run, profile, provider=provider, event_log=event_log)
    human = Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True)
    first_answer = "Only EU-level law is declared. " + ("x" * 2_000) + " OMITTED-TAIL"
    for answer in (
        first_answer,
        "No member state is declared.",
        "The organization's establishment is unknown.",
    ):
        service.answer(
            run,
            answer=answer,
            actor=human,
            provider=provider,
            event_log=event_log,
        )
    RunController(store).advance(
        run.id,
        RunTransitionKind.RESUME,
        payload={"resume_from": "information"},
    )

    status = service.begin(run, profile)

    questions = [
        event for event in event_log.events() if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED
    ]
    assert len(questions) == 3
    assert {event.payload["field"] for event in questions} == {"jurisdiction"}
    second_follow_up_prompt = provider.calls[5]["prompt"]
    assert "Which jurisdiction governs the activity?" in second_follow_up_prompt
    assert "Only EU-level law is declared." in second_follow_up_prompt
    history_text = second_follow_up_prompt.split(
        "Earlier question-answer turns for this field (bounded excerpts; omitted content is not "
        "evidence of absence):\n",
        maxsplit=1,
    )[1].split("\nCurrent Run request:\n", maxsplit=1)[0]
    history = json.loads(history_text)
    assert len(history) == 1
    assert history[0]["question"] == "Which jurisdiction governs the activity?"
    assert history[0]["answer"] == first_answer[:1_200]
    assert "OMITTED-TAIL" not in history_text
    assert history[0]["sufficiency"]["reason"] == "No national jurisdiction is stated."
    assert status.requires_human_review is True
    assert status.pending_question is None
    assert any(event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in event_log.events())
    assert verify_events(event_log.events()).valid


def test_context_document_records_six_facts_as_document_answers(tmp_path: Path) -> None:
    """Six facts stated from context leave exactly the final three fields for a human."""
    stated = {
        "purpose": "Benchmark a credit-risk model.",
        "affected_population": "Credit applicants.",
        "decision_effect": "Support human review.",
        "autonomy": "Recommendation only.",
        "human_oversight": "An analyst can stop the activity.",
        "jurisdiction": "Spain",
    }
    responses: list[Any] = [
        {
            "facts": [
                {"field": field, "value": value, "reason": f"Context states {field}."}
                for field, value in stated.items()
            ]
        },
        "Which data categories will be processed?",
    ]
    run, service, provider, event_log, _store = _interview(tmp_path, responses)

    status = service.begin(
        run,
        default_activity_profile(run),
        context_document=(
            "Purpose: benchmark a credit-risk model.\n"
            "Population: credit applicants.\n"
            "Effect: support human review.\n"
            "Autonomy: recommendation only.\n"
            "Oversight: an analyst can stop the activity.\n"
            "Jurisdiction: Spain."
        ),
        provider=provider,
        event_log=event_log,
    )

    document_answers = [
        event for event in event_log.events() if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
    ]
    assert status.profile.version == 7
    assert status.profile.purpose == "Benchmark a credit-risk model."
    assert status.profile.jurisdiction == "Spain"
    assert status.pending_question is not None
    assert status.pending_question.field == "data_categories"
    assert len(provider.calls) == 2
    assert "Jurisdiction: Spain." in provider.calls[0]["prompt"]
    assert [event.payload["answer"] for event in document_answers] == list(stated.values())
    assert all(event.payload["answer_source"] == "project_context" for event in document_answers)
    assert all(
        event.payload["source_ref"] == "run.prompt, .thymira/context.md"
        for event in document_answers
    )
    assert all(event.actor.kind is ActorKind.SYSTEM for event in document_answers)
    assert verify_events(event_log.events()).valid


def test_extraction_records_only_missing_fixed_fields_with_real_values(tmp_path: Path) -> None:
    """Code filters the model's facts: unknown fields, placeholders and repeats never land."""
    responses: list[Any] = [
        {
            "facts": [
                {"field": "budget", "value": "small", "reason": "Not an interview field."},
                {"field": "purpose", "value": "unknown", "reason": "A placeholder."},
                {"field": "jurisdiction", "value": "Spain", "reason": "Stated."},
                {"field": "jurisdiction", "value": "Portugal", "reason": "Repeated."},
                {"field": "sensitive_attributes", "value": "none", "reason": "Explicit."},
            ]
        },
        "What is the purpose?",
    ]
    run, service, provider, event_log, _store = _interview(tmp_path, responses)

    status = service.begin(
        run,
        default_activity_profile(run),
        provider=provider,
        event_log=event_log,
    )

    assert status.profile.version == 3
    assert status.profile.jurisdiction == "Spain"
    assert status.profile.sensitive_attributes == ()
    assert status.profile.purpose == "undeclared"
    assert status.pending_question is not None
    assert status.pending_question.field == "purpose"
    assert verify_events(event_log.events()).valid


def test_vague_context_document_leaves_the_interview_unchanged(tmp_path: Path) -> None:
    """A context the model cannot settle leaves every fixed field for the human."""
    responses: list[Any] = [{"facts": []}, "Please state the concrete purpose."]
    run, service, provider, event_log, _store = _interview(tmp_path, responses)

    status = service.begin(
        run,
        default_activity_profile(run),
        context_document="We might do something with data.",
        provider=provider,
        event_log=event_log,
    )

    assert status.profile.version == 1
    assert status.pending_question is not None
    assert status.pending_question.field == "purpose"
    assert not any(
        event.type is EventType.ACTIVITY_PROFILE_ANSWERED for event in event_log.events()
    )
    assert verify_events(event_log.events()).valid


@pytest.mark.integration
def test_core_loads_context_document_without_a_new_client_parameter(tmp_path: Path) -> None:
    """The production Core graph reads the project context before its first live question."""
    workspace = tmp_path / "project"
    config_dir = workspace / ".thymira"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "project:\n  name: credit-risk\n  domain: credit_risk\n",
        encoding="utf-8",
        newline="\n",
    )
    (config_dir / "context.md").write_text(
        "Purpose, affected population, decision effect, autonomy, oversight and jurisdiction.",
        encoding="utf-8",
        newline="\n",
    )
    stated = (
        "purpose",
        "affected_population",
        "decision_effect",
        "autonomy",
        "human_oversight",
        "jurisdiction",
    )
    provider = ScriptedProvider(
        [
            {
                "facts": [
                    {"field": field, "value": f"Declared {field}.", "reason": "Stated."}
                    for field in stated
                ]
            },
            "Which data categories will be processed?",
        ]
    )
    assembled: list[RuntimeDeps] = []
    factories: list[GraphFactory] = []

    def graph_factory(run: Run) -> CompiledStateGraph:
        """Build the real Core graph after the API composition exists."""
        deps = assembled[0]
        assert deps.project_resolution is not None
        if not factories:
            factories.append(
                build_runtime_graph_factory(
                    deps.run_store,
                    gate_factory=deps.gate_factory,
                    artifact_store_factory=deps.artifact_store_factory,
                    tool_manager=ToolManager(deps.tool_registry),
                    tool_registry=deps.tool_registry,
                    mira_tool_registry=deps.mira_tool_registry,
                    record_repository=deps.record_repository,
                    checkpoint_repository=deps.checkpoint_repository,
                    provider=provider,
                    project_dir=workspace,
                    project_config=deps.project_resolution.config,
                    catalog=full_agent_catalog(),
                    specs=(),
                    risk_interview=deps.risk_interview,
                    # The real Core graph gets the owner-composed durable board repositories,
                    # exactly like `build_default_deps` builds its own factory: this test asserts
                    # on the production composition, so it may not fall back to empty evidence.
                    plan_repository=deps.board_repository,
                    dag_repository=deps.dag_repository,
                )
            )
        return factories[0](run)

    assembled.append(
        build_default_deps(
            tmp_path / "runtime",
            workspace=workspace,
            graph_factory=graph_factory,
            provider=provider,
            principal_resolver=TEST_CREDENTIAL,
            # An absent THYMIRA_ALLOWED_MODEL_ROUTES is an intentional fail-closed empty
            # allowlist (ModelRoutePolicy.from_environment): every model.selected -- including
            # the interview's optional context-extraction call this test exercises -- would be
            # denied, not merely skipped, so the session needs an explicit operator allowlist
            # the same way production would carry one.
            model_route_policy=TEST_ROUTE_POLICY,
        )
    )
    deps = assembled[0]
    assert deps.project_resolution is not None
    session = deps.session_service.create(
        project_id=deps.project_resolution.project_id,
        client="test",
    )
    run = deps.run_service.create_run(
        session.id,
        "analyze",
        actor=Actor.system(),
        workspace=workspace,
    )

    events = deps.run_store.events(run.id)
    document_answers = [
        event for event in events if event.type is EventType.ACTIVITY_PROFILE_ANSWERED
    ]
    questions = [event for event in events if event.type is EventType.ACTIVITY_PROFILE_QUESTIONED]
    assert len(document_answers) == 6
    assert len(questions) == 1
    assert questions[0].payload["field"] == "data_categories"
    assert "Purpose, affected population" in provider.calls[0]["prompt"]
    assert verify_events(events).valid


def test_classification_drops_missing_information_no_interview_field_can_answer(
    tmp_path: Path,
) -> None:
    """The interview bounds the classifier to its own fields, so invented names cannot review."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.MEDIUM,
                activity_category="data_analysis",
                missing_information=("fairness_testing_plan", "consent_basis"),
                confidence=0.99,
                needs_human_review=False,
                summary="Bounded local analysis.",
            )
        ],
    )

    risk = service.classify(
        run,
        _complete_profile(run),
        event_log,
        provider,
        route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-test"),
    )

    assert risk.missing_information == ()
    assert risk.needs_human_review is False
    message = next(e for e in event_log.events() if e.type is EventType.AGENT_MESSAGE)
    assert "fairness_testing_plan" in message.payload["summary"]
    assert verify_events(event_log.events()).valid


def test_classification_keeps_missing_information_an_interview_field_can_answer(
    tmp_path: Path,
) -> None:
    """A gap the interview itself could close is kept, so the bound never hides a real question."""
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.MEDIUM,
                activity_category="data_analysis",
                missing_information=("human_oversight",),
                confidence=0.99,
                needs_human_review=False,
                summary="Bounded local analysis.",
            ),
            RiskUncertaintyQuestionSelection(
                field="human_oversight",
                question="Who can review or stop the activity?",
            ),
        ],
    )
    profile = _complete_profile(run).model_copy(update={"human_oversight": "undeclared"})

    risk = service.classify(
        run,
        profile,
        event_log,
        provider,
        route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-test"),
    )

    assert risk.missing_information == ("human_oversight",)
    assert risk.needs_human_review is True
    pending = service.status(run, profile).pending_question
    assert pending is not None
    assert pending.field == "human_oversight"
    assert RunController(store).current_state(run.id).wait_reason is WaitReason.INFORMATION


@pytest.mark.parametrize(
    ("risk_level", "activity_category"),
    [(RiskLevel.UNKNOWN, "data_analysis"), (RiskLevel.HIGH, "unknown")],
)
def test_unknown_classification_stays_fail_closed_and_keeps_clarification(
    tmp_path: Path,
    risk_level: RiskLevel,
    activity_category: str,
) -> None:
    """Unknown level or category still asks a question and retains the review requirement."""
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=risk_level,
                activity_category=activity_category,
                confidence=0.95,
                missing_information=(),
                needs_human_review=False,
                summary="The classification remains uncertain.",
            ),
            RiskUncertaintyQuestionSelection(
                field="purpose",
                question="What is the purpose of this activity?",
            ),
        ],
    )
    profile = _complete_profile(run)

    risk = service.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY)

    assert risk.needs_human_review is True
    pending = service.status(run, profile).pending_question
    assert pending is not None
    assert RunController(store).current_state(run.id).wait_reason is WaitReason.INFORMATION


def test_failed_classification_stays_fail_closed_and_keeps_clarification(tmp_path: Path) -> None:
    """A classifier fallback still carries its missing marker into human clarification."""
    run, service, provider, event_log, store = _interview(
        tmp_path,
        [
            {},
            RiskUncertaintyQuestionSelection(
                field="purpose",
                question="What is the purpose of this activity?",
            ),
        ],
    )
    profile = _complete_profile(run)

    risk = service.classify(run, profile, event_log, provider, route_policy=TEST_ROUTE_POLICY)

    assert CLASSIFICATION_FAILED_MARKER in risk.missing_information
    assert risk.needs_human_review is True
    assert service.status(run, profile).pending_question is not None
    assert RunController(store).current_state(run.id).wait_reason is WaitReason.INFORMATION


@pytest.mark.parametrize("clarified", [False, True])
def test_classification_prompt_carries_the_declared_decision_context(
    tmp_path: Path, clarified: bool
) -> None:
    """Core sends the profile's decision effect, autonomy and oversight, never a placeholder."""
    run, service, provider, event_log, _store = _interview(
        tmp_path,
        [
            RiskClassification(
                risk_level=RiskLevel.MEDIUM,
                activity_category="data_analysis",
                confidence=0.99,
                needs_human_review=False,
                summary="Bounded local analysis.",
            )
        ],
    )
    profile = _complete_profile(run).model_copy(update={"human_oversight": "undeclared"})
    if clarified:
        run = run.model_copy(
            update={
                "conversation": ConversationInput(
                    ordinal=1,
                    client_message_id="eda-clarified",
                    message="Si",
                    clarification_context=("Quiero realizar un EDA", "german_credit"),
                )
            }
        )

    service.classify(
        run,
        profile,
        event_log,
        provider,
        route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-test"),
    )

    prompt = provider.calls[0]["prompt"]
    assert "Changes review order but not credit approval." in prompt
    assert "Recommendation only." in prompt
    assert "profile-human_oversight" not in prompt
    assert "undeclared" not in prompt
    if clarified:
        assert "Quiero realizar un EDA" in prompt
        assert "german_credit" in prompt
