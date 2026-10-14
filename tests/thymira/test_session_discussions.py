"""Persistent read-only session answers never create or execute analytical Runs."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event as ThreadEvent

import pytest

from tests.thymira.api_support import api_credential, authenticated_client
from tests.thymira.test_api_sessions import _client, _complete, _send
from thymira.agents.llm import ScriptedProvider
from thymira.api import create_app
from thymira.core import RunController, RunTransitionKind, session_chat_model
from thymira.core.session_chat_context import MAX_EVIDENCE_CHARS, session_evidence
from thymira.events import sha256_text
from thymira.schemas import Actor, ActorKind, EventType, new_id
from thymira.schemas.session_chat import (
    SessionChatProposal,
    SessionDiscussion,
    SessionReadRequest,
    StoredDiscussion,
)
from thymira.state.session_discussions import LocalDiscussionRepository, SessionProviderLog

pytestmark = pytest.mark.integration


def _chat(client, session_id, text="What does that mean?", key="question-one", mode="auto"):
    return client.post(
        f"/sessions/{session_id}/chat",
        json={"message": text, "client_message_id": key, "mode": mode},
    )


def _provider(deps, *, kind="answer", text="The sample contains 27 observations.", sources=("r0",)):
    provider = ScriptedProvider(
        [json.dumps({"kind": kind, "message": text, "sources": list(sources)})], model="test-model"
    )
    deps.session_chat.provider = provider
    return provider


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")


def test_question_persists_without_another_run_and_retries_without_model_call(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run = _send(client, session).json()["id"]
    _complete(deps, run)
    provider = _provider(deps)
    answer = _chat(client, session)
    assert answer.status_code == 201, answer.text
    value = answer.json()
    assert value["kind"] == "answer"
    assert value["run_id"] is None
    assert value["citations"][0]["run_id"] == run
    assert len(deps.session_service.get_session(session).run_ids) == 1
    assert _chat(client, session).json() == value
    assert len(provider.calls) == 1
    rebuilt, _ = _client(tmp_path)
    assert rebuilt.get(f"/sessions/{session}").json()["discussions"] == [value]
    assert _chat(rebuilt, session).json() == value


def test_explicit_question_while_run_active_needs_no_dataset_or_approval(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _send(client, session)
    _provider(deps, text="The Run has not finished.")
    answer = _chat(client, session, mode="ask")
    assert answer.status_code == 201, answer.text
    assert answer.json()["kind"] == "answer"
    assert len(deps.session_service.get_session(session).run_ids) == 1


def test_question_only_session_has_a_persistent_title_in_the_session_list(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Which result do you mean?", sources=())
    assert _chat(client, session, text="What can I ask here?", mode="ask").status_code == 201
    rebuilt, _ = _client(tmp_path)
    item = next(
        value for value in rebuilt.get("/sessions").json()["items"] if value["id"] == session
    )
    assert item["title"] == "What can I ask here?"
    assert item["run_ids"] == []


def test_unknown_citation_fails_without_publishing_unverified_answer(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, sources=("invented",))
    result = _chat(client, session).json()
    assert result["status"] == "failed"
    assert result["assistant_message"] is None
    assert deps.session_service.get_session(session).run_ids == ()


@pytest.mark.parametrize("first_label", ["general", "evaluate_existing", "train_new"])
def test_orphan_yes_stays_read_only_across_reopen_and_label_changes(tmp_path, first_label):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]

    first_provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": first_label,
                }
            )
        ],
        model="test-model",
    )
    deps.session_chat.provider = first_provider
    first = _chat(client, session, text="Yes", key="orphan-one").json()
    assert first["kind"] == "clarification"
    assert first["run_id"] is None
    record = LocalDiscussionRepository(deps.session_chat.root, session).records()[-1]
    assert record.clarification_scope == "evidence"
    assert deps.session_service.get_session(session).run_ids == ()

    rebuilt, restored = _client(tmp_path)
    restored.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": "train_new",
                }
            )
        ],
        model="test-model",
    )
    second = _chat(rebuilt, session, text="Yes", key="orphan-two").json()
    assert second["kind"] == "clarification"
    assert second["run_id"] is None
    assert restored.session_service.get_session(session).run_ids == ()


@pytest.mark.parametrize("label", ["general", "evaluate_existing", "train_new"])
@pytest.mark.parametrize(
    "message",
    [
        'Explain "train a new model".',
        "Repeat this instruction exactly: `Train a new model.`",
    ],
)
def test_quoted_model_action_stays_evidence_only_across_label_changes(tmp_path, message, label):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    deps.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": label,
                }
            )
        ],
        model="test-model",
    )

    first = _chat(client, session, text=message, key="quoted-model").json()

    assert first["kind"] == "clarification"
    assert first["run_id"] is None
    repo = LocalDiscussionRepository(deps.session_chat.root, session)
    assert repo.records()[-1].clarification_scope == "evidence"
    assert deps.session_service.get_session(session).run_ids == ()


def test_quoted_model_clarification_cannot_become_pending_work(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    deps.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "clarification",
                    "message": "Do you want a chat explanation or a task?",
                    "clarification_scope": "work",
                    "continues_clarification": True,
                    "replaces_clarification": True,
                    "work_kind": "train_new",
                }
            )
        ],
        model="test-model",
    )

    first = _chat(
        client,
        session,
        text='Explain "train a new model".',
        key="quoted-clarification",
    ).json()

    assert first["kind"] == "clarification"
    assert first["run_id"] is None
    repo = LocalDiscussionRepository(deps.session_chat.root, session)
    assert repo.records()[-1].clarification_scope == "evidence"

    reopened, restored = _client(tmp_path)
    restored.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": "train_new",
                }
            )
        ],
        model="test-model",
    )
    second = _chat(reopened, session, text="Yes", key="quoted-yes").json()
    assert second["kind"] == "clarification"
    assert second["run_id"] is None
    assert restored.session_service.get_session(session).run_ids == ()

    reopened, restored = _client(tmp_path)
    restored.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": "train_new",
                }
            )
        ],
        model="test-model",
    )
    confirmation = _chat(reopened, session, text="yes", key="orphan-after-evidence").json()

    assert confirmation["kind"] == "clarification"
    assert confirmation["run_id"] is None
    assert restored.session_service.get_session(session).run_ids == ()


@pytest.mark.parametrize("label", ["general", "evaluate_existing", "train_new"])
@pytest.mark.parametrize(
    "message",
    [
        "Never train a new model; only do an EDA.",
        "Nunca entrenes un modelo; solo haz un EDA.",
    ],
)
def test_explicit_training_prohibition_keeps_eda_general_across_labels(tmp_path, message, label):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    deps.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": label,
                }
            )
        ],
        model="test-model",
    )

    response = _chat(client, session, text=message, key="negative-model-action")

    assert response.status_code == 201, response.text
    value = response.json()
    assert value["run_id"] is not None
    record = LocalDiscussionRepository(deps.session_chat.root, session).records()[-1]
    run = deps.run_service.get_run(value["run_id"])
    assert record.work_kind == "general"
    assert run.conversation.work_kind == "general"
    assert deps.session_service.get_session(session).run_ids == (run.id,)


def test_ambiguous_model_yes_stays_unclear_and_request_work_replaces_it(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    deps.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": "train_new",
                }
            )
        ],
        model="test-model",
    )

    ambiguous = _chat(client, session, text="Use a model for this dataset", key="ambiguous")
    assert ambiguous.status_code == 201, ambiguous.text
    first = ambiguous.json()
    assert first["kind"] == "clarification"
    assert first["run_id"] is None
    assert "train a new model" in first["assistant_message"].lower()
    assert deps.session_service.get_session(session).run_ids == ()

    reopened, restored = _client(tmp_path)
    restored.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": "train_new",
                }
            )
        ],
        model="test-model",
    )
    yes = _chat(reopened, session, text="Yes", key="ambiguous-yes").json()
    assert yes["kind"] == "clarification"
    assert yes["run_id"] is None
    assert restored.session_service.get_session(session).run_ids == ()

    replacement_text = "Train a new classifier on this dataset."
    replacement = _chat(reopened, session, text=replacement_text, key="request-work", mode="action")
    assert replacement.status_code == 201, replacement.text
    run = restored.run_service.get_run(replacement.json()["run_id"])
    assert run.conversation is not None
    assert run.conversation.work_kind == "train_new"
    assert run.conversation.clarification_context == ()
    assert run.conversation.requested_work == replacement_text


def test_explicit_action_preserves_run_contract_and_duplicate_identity(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    first = _chat(client, session, text="Run an EDA", mode="action")
    assert first.status_code == 201, first.text
    run_id = first.json()["run_id"]
    assert run_id
    assert _chat(client, session, text="Run an EDA", mode="action").json() == first.json()
    assert _chat(client, session, text="Different", mode="action").status_code == 409
    assert deps.session_service.get_session(session).run_ids == (run_id,)


def test_ambiguous_message_asks_a_question_and_never_starts_work(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Which result do you mean?", sources=())
    value = _chat(client, session, text="that").json()
    assert value["kind"] == "clarification"
    assert value["assistant_message"] == "Which result do you mean?"
    assert deps.session_service.get_session(session).run_ids == ()


def test_auto_action_uses_original_message_and_governed_run_publication(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    provider = _provider(deps, kind="action", text="", sources=())
    value = _chat(client, session, text="Compute a new EDA").json()
    run = deps.run_service.get_run(value["run_id"])
    assert run.conversation.message == "Compute a new EDA"
    assert provider.calls[0]["tools"] == []
    assert len(deps.session_service.get_session(session).run_ids) == 1


def test_explicit_question_cannot_be_escalated_to_action_by_model(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="action", text="", sources=())
    value = _chat(client, session, mode="ask").json()
    assert value["status"] == "failed"
    assert deps.session_service.get_session(session).run_ids == ()


def test_discussion_context_is_available_to_followup_question_and_new_work(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Do you mean the missing-value table?", sources=())
    _chat(client, session, text="What is that?")
    second = _provider(
        deps, kind="clarification", text="Use EDA to calculate that table.", sources=()
    )
    _chat(client, session, text="yes", key="two")
    assert "missing-value table" in second.calls[0]["prompt"]
    action = _chat(client, session, text="Calculate that table", key="three", mode="action").json()
    run = deps.run_service.get_run(action["run_id"])
    assert "missing-value table" in run.prompt
    assert "not_audited_result" in run.prompt


def test_active_run_blocks_only_new_work_and_pending_publication_can_be_retried(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run = _send(client, session).json()["id"]
    pending = _chat(client, session, text="New calculation", mode="action")
    assert pending.status_code == 409
    discussion = client.get(f"/sessions/{session}").json()["discussions"][0]
    assert discussion["kind"] == "action"
    assert discussion["status"] == "pending"
    _complete(deps, run)
    rebuilt, rebuilt_deps = _client(tmp_path)
    retry = rebuilt.post(f"/sessions/{session}/chat/{discussion['id']}/retry")
    assert retry.status_code == 200, retry.text
    assert retry.json()["run_id"] != run
    assert len(rebuilt_deps.session_service.get_session(session).run_ids) == 2


def test_cross_project_session_and_foreign_discussion_cannot_be_read_or_retried(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    foreign = deps.session_service.create_session(
        project_id=new_id("project"), client="test", conversation=True
    )
    assert _chat(client, foreign.id).status_code == 404
    _provider(deps, kind="clarification", sources=())
    value = _chat(client, session).json()
    other = client.post("/sessions", json={}).json()["id"]
    assert client.post(f"/sessions/{other}/chat/{value['id']}/retry").status_code == 404
    assert deps.session_service.get_session(other).run_ids == ()


def test_retry_is_actor_bound_and_write_permission_is_required(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", sources=())
    value = _chat(client, session).json()
    token, credential = api_credential(actor_id="different")
    other = authenticated_client(create_app(replace(deps, principal_resolver=credential)), token)
    assert _chat(other, session).status_code == 409
    assert other.post(f"/sessions/{session}/chat/{value['id']}/retry").status_code == 409
    token, credential = api_credential(role="viewer")
    reader = authenticated_client(create_app(replace(deps, principal_resolver=credential)), token)
    assert _chat(reader, session).status_code == 403


def test_provider_call_is_serialized_across_reconstructed_runtime_instances(tmp_path, monkeypatch):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    provider = _provider(deps, kind="clarification", sources=())
    entered, release = ThreadEvent(), ThreadEvent()
    complete = provider.complete_structured

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return complete(*args, **kwargs)

    monkeypatch.setattr(provider, "complete_structured", delayed)
    other, _ = _client(tmp_path)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(_chat, client, session)
        assert entered.wait(5)
        try:
            assert _chat(other, session).status_code == 409
            assert other.get(f"/sessions/{session}").json()["discussions"][0]["status"] == "pending"
        finally:
            release.set()
        assert pending.result().status_code == 201
    assert len(provider.calls) == 1
    assert _chat(other, session).status_code == 200


def test_interrupted_provider_request_is_not_reissued_on_restart(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    record = StoredDiscussion(
        session_id=session,
        project_id=deps.session_service.get_session(session).project_id,
        actor=Actor(kind=ActorKind.HUMAN, id="tester", role="admin", authenticated=True),
        client_message_id=sha256_text("question-one"),
        discussion=SessionDiscussion(
            id=new_id("turn"), ordinal=1, user_message="What does that mean?", mode="auto"
        ),
    )
    LocalDiscussionRepository(deps.session_chat.root, session).save(record)
    SessionProviderLog(deps.session_chat.root, session, record.discussion.id).append(
        EventType.MODEL_REQUEST_RECORDED,
        Actor.system(),
        {"interrupted": True},
    )
    rebuilt, rebuilt_deps = _client(tmp_path)
    provider = _provider(rebuilt_deps)
    value = _chat(rebuilt, session).json()
    assert value["status"] == "failed"
    assert provider.calls == []
    assert rebuilt_deps.session_service.get_session(session).run_ids == ()


def test_blocked_or_failed_run_never_releases_provisional_analytical_text(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run = _send(client, session).json()["id"]
    RunController(deps.run_store).advance(run, RunTransitionKind.START)
    deps.event_store.open(run).append(
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {
            "kind": "conversation.response",
            "content": "UNAPPROVED SECRET ANALYSIS",
        },
    )
    deps.run_service.fail(run, actor=Actor.system(), error="Python execution failed")
    provider = _provider(deps, text="The Run failed during Python execution.")
    assert _chat(client, session).json()["status"] == "completed"
    assert "UNAPPROVED SECRET ANALYSIS" not in provider.calls[0]["prompt"]
    assert "Python execution failed" in provider.calls[0]["prompt"]


def test_credentials_are_scrubbed_from_request_reply_and_private_evidence(tmp_path, monkeypatch):
    secret = "sk-session-test-secret-12345678901234567890"  # noqa: S105
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    provider = _provider(deps, kind="clarification", text=f"Did you mean {secret}?", sources=())
    value = _chat(client, session, text=f"Explain {secret}", key=secret).json()
    assert secret not in json.dumps(value)
    assert secret not in provider.calls[0]["prompt"]
    for path in deps.session_chat.root.rglob("*.json"):
        assert secret not in path.read_text(encoding="utf-8")


def test_session_model_allowlist_denial_makes_no_provider_call(tmp_path, monkeypatch):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    provider = _provider(deps)
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "unapproved-model")
    value = _chat(client, session).json()
    assert value["status"] == "failed"
    assert provider.calls == []


def test_created_action_recovers_after_response_loss_without_duplicate_execution(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    value = _chat(client, session, text="Compute a table", mode="action").json()
    run_id = value["run_id"]
    submitted = []

    class AdvancingDispatcher:
        def submit(self, submitted_id):
            submitted.append(submitted_id)
            RunController(deps.run_store).advance(submitted_id, RunTransitionKind.START)

        def resume(self, _run_id):
            raise AssertionError("resuming is not a chat publication operation")

    recovery = authenticated_client(create_app(replace(deps, dispatcher=AdvancingDispatcher())))
    assert recovery.post(f"/sessions/{session}/chat/{value['id']}/retry").status_code == 200
    assert recovery.post(f"/sessions/{session}/chat/{value['id']}/retry").status_code == 200
    assert submitted == [run_id]
    assert deps.session_service.get_session(session).run_ids == (run_id,)


def test_injected_provider_cannot_bypass_selected_model_route(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    provider = _provider(deps, kind="clarification", text="What result?", sources=())
    provider.model = "unapproved-model"
    value = _chat(client, session).json()
    assert value["status"] == "failed"
    assert provider.calls == []
    events = SessionProviderLog(deps.session_chat.root, session, value["id"]).events()
    assert events[-1].type is EventType.MODEL_ROUTE_DENIED
    assert not any(event.type is EventType.MODEL_REQUEST_RECORDED for event in events)


def test_provider_construction_pins_recorded_choice_across_settings_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model,other-model")
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    provider = _provider(deps, kind="clarification", text="What result?", sources=())
    deps.session_chat.provider = None
    factory = session_chat_model.provider_for

    def changing_settings(*args, **kwargs):
        monkeypatch.setenv("THYMIRA_MODEL_FAST", "other-model")
        return factory(*args, **kwargs)

    monkeypatch.setattr("thymira.agents.llm.routing.LiteLLMProvider", lambda **kwargs: provider)
    monkeypatch.setattr(session_chat_model, "provider_for", changing_settings)
    value = _chat(client, session).json()
    assert value["status"] == "completed"
    events = SessionProviderLog(deps.session_chat.root, session, value["id"]).events()
    selected = next(event for event in events if event.type is EventType.MODEL_SELECTED)
    header = next(event for event in events if event.type is EventType.MODEL_INPUT_HEADER_REVISED)
    assert selected.payload["model"] == header.payload["model"] == "test-model"


def test_question_budget_is_charged_and_refuses_over_limit_response(tmp_path, monkeypatch):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    provider = _provider(deps, kind="clarification", text="What result?", sources=())
    complete = provider.complete_structured

    def oversized(*args, **kwargs):
        proposal, response = complete(*args, **kwargs)
        return proposal, response.model_copy(update={"input_tokens": 17_000})

    monkeypatch.setattr(provider, "complete_structured", oversized)
    value = _chat(client, session).json()
    assert value["status"] == "failed"
    assert "budget" in value["error"]
    events = SessionProviderLog(deps.session_chat.root, session, value["id"]).events()
    assert events[-1].type is EventType.SESSION_MODEL_USAGE
    assert events[-1].payload["input_tokens"] == 17_000
    assert deps.session_service.get_session(session).run_ids == ()


def test_model_context_omits_other_session_and_raw_tool_arguments(tmp_path):
    client, deps = _client(tmp_path)
    other = client.post("/sessions", json={}).json()["id"]
    other_run = _send(client, other).json()["id"]
    _complete(deps, other_run, response="UNRELATED WORKSPACE RESULT")
    session = client.post("/sessions", json={}).json()["id"]
    run = _send(client, session).json()["id"]
    deps.event_store.open(run).append(
        EventType.HUMAN_APPROVAL_REQUESTED,
        Actor.system(),
        {
            "summary": "Review the Python operation",
            "request_payload": {
                "arguments": {"code": "RAW DATASET CONTENT"},
                "reason": "tool_request",
            },
        },
    )
    provider = _provider(deps, text="A Python operation awaits review.")
    assert _chat(client, session).json()["status"] == "completed"
    prompt = provider.calls[0]["prompt"]
    assert "UNRELATED WORKSPACE RESULT" not in prompt
    assert "RAW DATASET CONTENT" not in prompt
    assert "Review the Python operation" in prompt


def test_context_uses_real_interview_events_and_only_cites_complete_selected_records(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    for number in range(4):
        run = _send(client, session, request_id=f"work-{number}").json()["id"]
        deps.event_store.open(run).append(
            EventType.ACTIVITY_PROFILE_QUESTIONED,
            Actor.system(),
            {
                "field": "decision_effect",
                "question": "Will these results affect any live decision?",
            },
        )
        deps.event_store.open(run).append(
            EventType.ACTIVITY_PROFILE_ANSWERED,
            Actor.system(),
            {
                "field": "decision_effect",
                "answer": "No live decisions; offline research only.",
            },
        )
        _complete(deps, run, response="Approved descriptive result. " * 400)
    detail = deps.session_chat.detail(
        session, project_id=deps.session_service.get_session(session).project_id
    )
    text, citations = session_evidence(detail, deps.event_store)
    assert len(text) <= MAX_EVIDENCE_CHARS
    selected = [json.loads(line) for line in text.splitlines()]
    assert {item["source"] for item in selected} == set(citations)
    assert "No live decisions; offline research only." in text
    assert "activity_profile.questioned" in text


def test_context_cites_created_artifact_name_alongside_terminal_missing_artifact_error(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run = _send(client, session).json()["id"]
    RunController(deps.run_store).advance(run, RunTransitionKind.START)
    artifact_id = new_id("artifact")
    deps.event_store.open(run).append(
        EventType.ARTIFACT_CREATED,
        Actor.system(),
        {"artifact_id": artifact_id, "name": "data/eda_plots.png"},
    )
    deps.run_service.fail(
        run,
        actor=Actor.system(),
        error="THY halted before completion: required artifacts missing: eda_plots.png",
    )

    detail = deps.session_chat.detail(
        session, project_id=deps.session_service.get_session(session).project_id
    )
    text, citations = session_evidence(detail, deps.event_store)

    artifact_event = next(
        event for event in deps.event_store.read(run) if event.type is EventType.ARTIFACT_CREATED
    )
    artifact_record = next(
        item
        for item in map(json.loads, text.splitlines())
        if item.get("type") == "artifact.created"
    )
    assert artifact_record["recorded_fact_excerpt"] == (
        f'{{"artifact_id":"{artifact_id}","name":"data/eda_plots.png"}}'
    )
    assert citations[artifact_record["source"]].run_id == run
    assert citations[artifact_record["source"]].seq == artifact_event.seq

    provider = _provider(
        deps,
        text="The event records data/eda_plots.png, while the terminal error names eda_plots.png.",
        sources=(artifact_record["source"],),
    )
    value = _chat(client, session, text="Explain the terminal artifact outcome.").json()
    assert value["status"] == "completed"
    assert value["citations"][0]["seq"] == artifact_event.seq
    assert "data/eda_plots.png" in provider.calls[0]["prompt"]
    assert "artifact.created" in provider.calls[0]["prompt"]
    assert "required artifacts missing: eda_plots.png" in provider.calls[0]["prompt"]


def test_citationless_answer_is_repaired_once_and_persisted_without_creating_a_run(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run = _send(client, session).json()["id"]
    _complete(deps, run)
    secret = "sk-session-test-secret-12345678901234567890"  # noqa: S105
    marker = "FINAL-DRAFT-MARKER-MUST-SURVIVE"
    monkeypatch.setenv("THYMIRA_TEST_API_KEY", secret)
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "answer",
                    "message": (
                        "x" * (session_chat_model.MAX_REPAIR_RESPONSE_CHARS + 128) + secret + marker
                    ),
                    "sources": [],
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The approved summary is available.",
                    "sources": ["r0"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    response = _chat(client, session, text="Explain the approved result.")

    assert response.status_code == 201, response.text
    value = response.json()
    assert value["status"] == "completed"
    assert value["citations"][0]["run_id"] == run
    assert value["run_id"] is None
    assert len(provider.calls) == 2
    assert "unverified-citation-repair-response" in provider.calls[1]["prompt"]
    assert "r0" in provider.calls[1]["prompt"]
    assert secret not in provider.calls[1]["prompt"]
    assert secret[:8] not in provider.calls[1]["prompt"]
    assert marker in provider.calls[1]["prompt"]
    events = SessionProviderLog(deps.session_chat.root, session, value["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 2
    assert not any(
        event.type in {EventType.TOOL_STARTED, EventType.TOOL_DENIED} for event in events
    )
    assert sum(event.type is EventType.SESSION_MODEL_USAGE for event in events) == 2
    assert [
        event.payload["model"] for event in events if event.type is EventType.MODEL_SELECTED
    ] == [
        "test-model",
        "test-model",
    ]
    usage_records = [event for event in events if event.type is EventType.SESSION_MODEL_USAGE]
    assert all(
        event.payload["limits"]
        == {"max_requests": 6, "max_tokens": 16_000, "max_tool_calls": 0, "max_cost_usd": None}
        for event in usage_records
    )
    assert all(call["tools"] == [] for call in provider.calls)
    assert deps.session_service.get_session(session).run_ids == (run,)

    rebuilt, _ = _client(tmp_path)
    assert rebuilt.get(f"/sessions/{session}").json()["discussions"] == [value]
    assert _chat(rebuilt, session, text="Explain the approved result.").json() == value
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    ("repair_kind", "repair_message", "repair_sources", "repair_lookup"),
    [
        ("answer", "This cites an unknown event.", ["invented-source"], None),
        ("action", "", [], None),
        ("lookup", "", [], {"operation": "list_files", "path": "project"}),
    ],
    ids=("unknown-citation", "repair-cannot-dispatch-work", "repair-cannot-query"),
)
def test_invalid_citation_repair_fails_closed_without_creating_a_run(
    tmp_path, repair_kind, repair_message, repair_sources, repair_lookup
):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run = _send(client, session).json()["id"]
    _complete(deps, run)
    repair_proposal = {
        "kind": repair_kind,
        "message": repair_message,
        "sources": repair_sources,
    }
    if repair_lookup is not None:
        repair_proposal["lookup"] = repair_lookup
        parsed_lookup = SessionChatProposal.model_validate(repair_proposal).lookup
        assert parsed_lookup == SessionReadRequest.model_validate(repair_lookup)
    provider = ScriptedProvider(
        [
            json.dumps({"kind": "answer", "message": "Unsupported answer.", "sources": []}),
            json.dumps(repair_proposal),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    response = _chat(client, session, text="Explain the approved result.")

    assert response.status_code == 201, response.text
    value = response.json()
    assert value["status"] == "failed"
    assert value["assistant_message"] is None
    assert value["run_id"] is None
    assert deps.session_service.get_session(session).run_ids == (run,)
    assert len(provider.calls) == 2
    events = SessionProviderLog(deps.session_chat.root, session, value["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 2
    assert not any(
        event.type in {EventType.TOOL_STARTED, EventType.TOOL_DENIED} for event in events
    )

    assert _chat(client, session, text="Explain the approved result.").json() == value
    assert len(provider.calls) == 2
