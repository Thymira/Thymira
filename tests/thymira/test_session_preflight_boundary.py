"""A chat-displayed MIRA intake remains part of its original Run across API restart."""

import pytest

from tests.thymira.api_support import TEST_CREDENTIAL, authenticated_client
from tests.thymira.test_api_risk_clarification import _Dispatcher
from thymira.agents.llm import ScriptedProvider
from thymira.api import build_default_deps, create_app
from thymira.core import RunController, RunTransitionKind, default_activity_profile
from thymira.events import verify_events
from thymira.schemas import EventType

pytestmark = pytest.mark.integration


def test_interview_answer_is_not_a_new_chat_request_or_run(tmp_path, monkeypatch):
    for name in ("THYMIRA_ALLOWED_MODEL_ROUTES", "THYMIRA_MODEL_FAST"):
        monkeypatch.setenv(name, "test-model")
    workspace = tmp_path / "workspace"
    config = workspace / ".thymira/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project:\n  name: interview\n  domain: research\n", encoding="utf-8")
    provider = ScriptedProvider(
        [
            "What consequences could an incorrect result have in this test?",
            {"sufficient": True, "reason": "The user supplies a scoped declaration."},
        ],
        model="test-model",
    )
    dispatcher = _Dispatcher()
    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        dispatcher=dispatcher,
        principal_resolver=TEST_CREDENTIAL,
        provider=provider,
    )
    client = authenticated_client(create_app(deps))
    session = client.post("/sessions", json={}).json()["id"]
    sent = client.post(
        f"/sessions/{session}/chat",
        json={
            "message": "Run an EDA with plots of the test dataset",
            "mode": "action",
            "client_message_id": "eda-request",
        },
    ).json()
    run = deps.run_service.get_run(sent["run_id"])
    RunController(deps.run_store).advance(run.id, RunTransitionKind.START)
    profile = default_activity_profile(run).model_copy(
        update={
            "version": 2,
            "purpose": "EDA with plots",
            "affected_population": "Test records",
            "decision_effect": "Offline exploration",
            "autonomy": "Local analysis",
            "human_oversight": "Researcher review",
            "jurisdiction": "EU",
            "data_categories": ("test records",),
            "sensitive_attributes": (),
            "potential_consequences": ("undeclared",),
        }
    )
    question = deps.risk_interview.begin(
        run,
        profile,
        provider=provider,
        event_log=deps.event_store.open(run.id),
    ).pending_question
    assert question is not None

    # The same HTTP endpoint the chat's MIRA form calls; only the provider is simulated.
    answer = "It has zero impact in this test."
    accepted = client.post(
        f"/runs/{run.id}/risk-interview",
        json={
            "answer": answer,
            "expected_question_id": question.event_id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    fresh = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        dispatcher=_Dispatcher(),
        principal_resolver=TEST_CREDENTIAL,
        provider=ScriptedProvider([], model="test-model"),
    )
    reopened = authenticated_client(create_app(fresh))
    detail = reopened.get(f"/sessions/{session}").json()
    assert [turn["id"] for turn in detail["turns"]] == [run.id]
    assert [item["id"] for item in detail["discussions"]] == [sent["id"]]
    assert answer not in fresh.run_service.get_run(run.id).prompt
    status = reopened.get(f"/runs/{run.id}/risk-interview").json()
    assert status["profile"]["potential_consequences"] == [answer]
    assert status["pending_question"] is None
    events = fresh.event_store.read(run.id)
    assert verify_events(events).valid
    assert sum(event.type is EventType.RUN_STARTED for event in events) == 1
    assert any(event.type is EventType.ACTIVITY_PROFILE_ANSWERED for event in events)
    assert not any(event.type is EventType.HUMAN_APPROVAL for event in events)
