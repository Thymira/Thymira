"""Clarified work keeps its human request and dataset across durable Run publication."""

from __future__ import annotations

import json

import pytest

from tests.thymira.test_api_sessions import _client, _complete, _send
from tests.thymira.test_session_discussions import _chat, _provider
from thymira.agents import RiskClassification
from thymira.agents.llm import ScriptedProvider
from thymira.core import default_activity_profile
from thymira.schemas import RiskLevel
from thymira.state.session_discussions import LocalDiscussionRepository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")


def _continue_work(deps, *, kind="action", text="", continues=True, replaces=False):
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": kind,
                    "message": text,
                    "continues_clarification": continues,
                    "replaces_clarification": replaces,
                }
            )
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider
    return provider


def _declare_german_credit(tmp_path, *, target=None):
    workspace = tmp_path / "workspace"
    (workspace / "data").mkdir()
    (workspace / "data/german_credit.csv").write_text("id\n1\n", encoding="utf-8")
    target_line = f"    target: {target}\n" if target else ""
    (workspace / ".thymira/config.yaml").write_text(
        "project:\n  name: conversations\n  domain: research\n"
        "datasets:\n  - name: german_credit\n    path: data/german_credit.csv\n" + target_line,
        encoding="utf-8",
    )


def _classification_prompt(deps, run):
    """Observe the clarified published Run at the actual risk-classifier boundary."""
    profile = default_activity_profile(run).model_copy(
        update={
            "purpose": "Bounded exploratory data analysis.",
            "affected_population": "No individual decision is made.",
            "decision_effect": "Descriptive only; no eligibility decision changes.",
            "autonomy": "No automated decision is made.",
            "human_oversight": "A researcher reviews the report.",
            "jurisdiction": "EU",
            "data_categories": ("application records",),
            "sensitive_attributes": (),
            "potential_consequences": ("No person-level operational effect.",),
            "created_at": run.created_at,
        }
    )
    provider = ScriptedProvider(
        [
            RiskClassification(
                risk_level=RiskLevel.LOW,
                activity_category="data_analysis",
                confidence=0.8,
                needs_human_review=False,
                summary="Bounded exploratory analysis.",
            )
        ],
        model="test-model",
    )
    deps.risk_interview.classify(
        run,
        profile,
        deps.event_store.open(run.id),
        provider,
        route_policy=run.model_route_policy,
    )
    assert provider.calls
    return provider.calls[0]["prompt"]


@pytest.mark.parametrize("failed_before", [False, True])
def test_first_question_does_not_require_a_parent_when_model_sets_continuation(
    tmp_path, failed_before
):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    if failed_before:
        _provider(deps, sources=("unknown",))
        failed = _chat(client, session, text="Train a model", key="old-failure").json()
        assert failed["status"] == "failed"
    question = "¿Sobre qué dataset deseas realizar el EDA?"
    provider = _continue_work(deps, kind="clarification", text=question)
    request = "Quiero realizar un EDA"
    response = _chat(client, session, text=request)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["status"] == "completed", value
    assert value["assistant_message"] == question
    assert value["run_id"] is None
    assert len(provider.calls) == 1
    rebuilt, restored = _client(tmp_path)
    assert _chat(rebuilt, session, text=request).json() == value
    assert restored.session_service.get_session(session).run_ids == ()
    _continue_work(restored)
    answer = _chat(rebuilt, session, text="german_credit", key="dataset").json()
    run = restored.run_service.get_run(answer["run_id"])
    assert run.conversation.clarification_context == (request,)
    assert run.conversation.message == "german_credit"
    assert "Train a model" not in run.conversation.requested_work


def test_dataset_only_action_with_false_continuation_keeps_adjacent_eda_scope(tmp_path):
    client, deps = _client(tmp_path)
    _declare_german_credit(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    question = "¿Sobre qué dataset quieres realizar el EDA?"
    _continue_work(deps, kind="clarification", text=question, continues=False)
    original = "Quiero hacer un EDA"
    clarification = _chat(client, session, text=original).json()
    assert clarification["status"] == "completed", clarification
    assert clarification["assistant_message"] == question

    provider = _continue_work(deps, continues=False)
    response = _chat(client, session, text="german_credit", key="dataset")
    assert response.status_code == 201, response.text
    value = response.json()
    run = deps.run_service.get_run(value["run_id"])
    stored = json.loads(
        (deps.session_chat.root / session / f"{value['id']}.json").read_text(encoding="utf-8")
    )

    assert run.conversation.clarification_context == (original,)
    assert stored["clarification_parent_id"] == clarification["id"]
    assert run.conversation.message == "german_credit"
    assert original in run.conversation.requested_work
    assert question not in run.conversation.requested_work
    assert "german_credit" in run.conversation.requested_work
    assert len(provider.calls) == 1


def test_dataset_only_action_without_adjacent_clarification_starts_no_run(tmp_path):
    client, deps = _client(tmp_path)
    _declare_german_credit(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _continue_work(deps, continues=False)

    response = _chat(client, session, text="german_credit")

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert "restate the complete task and dataset" in response.json()["error"]
    assert deps.session_service.get_session(session).run_ids == ()


@pytest.mark.parametrize("reply", ["El de german_credit", "Si", "Si, te lo confirmo otra vez"])
def test_natural_reply_keeps_pending_request_when_model_omits_continuation(tmp_path, reply):
    client, deps = _client(tmp_path)
    _declare_german_credit(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    original = "Quiero hacer un EDA, muestrame graficas sin entrenar modelos"
    _continue_work(deps, kind="clarification", text="Which dataset?", continues=False)
    _chat(client, session, text=original)
    _continue_work(deps, kind="clarification", text="Confirm the dataset?", continues=True)
    _chat(client, session, text="german_credit", key="first-answer")
    rebuilt, restored = _client(tmp_path)
    _continue_work(restored, continues=False)

    value = _chat(rebuilt, session, text=reply, key="reply").json()

    assert value["status"] == "completed", value
    run = restored.run_service.get_run(value["run_id"])
    assert run.conversation.clarification_context == (original, "german_credit")
    assert run.conversation.requested_work.endswith(reply)
    assert "sin entrenar modelos" in run.conversation.requested_work
    classification_prompt = _classification_prompt(restored, run)
    assert run.conversation.requested_work in classification_prompt
    assert reply in classification_prompt
    assert "sin entrenar modelos" in classification_prompt
    assert len(restored.session_service.get_session(session).run_ids) == 1
    assert _chat(rebuilt, session, text=reply, key="reply").json() == value


def test_model_replacement_claim_cannot_discard_a_pending_human_request(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    original = "Run only an EDA with plots; do not train models"
    _continue_work(deps, kind="clarification", text="Which dataset?", continues=False)
    _chat(client, session, text=original)
    _continue_work(deps, continues=False, replaces=True)

    rejected = _chat(client, session, text="Yes", key="false-replacement").json()

    assert rejected["status"] == "failed"
    assert rejected["run_id"] is None
    assert "Request work" in rejected["error"]
    rebuilt, restored = _client(tmp_path)
    _continue_work(restored, continues=False)
    accepted = _chat(rebuilt, session, text="The sample dataset", key="real-answer").json()
    run = restored.run_service.get_run(accepted["run_id"])
    assert run.conversation.clarification_context == (original,)
    assert len(restored.session_service.get_session(session).run_ids) == 1


def test_false_continuation_scope_survives_restart_and_pending_retry(tmp_path):
    client, deps = _client(tmp_path)
    _declare_german_credit(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    active_run = _send(client, session).json()["id"]
    _continue_work(deps, kind="clarification", text="Which dataset?", continues=False)
    original = "Run an EDA"
    clarification = _chat(client, session, text=original).json()

    provider = _continue_work(deps, continues=False)
    blocked = _chat(client, session, text="german_credit", key="dataset")
    assert blocked.status_code == 409
    pending = client.get(f"/sessions/{session}").json()["discussions"][-1]
    assert pending["status"] == "pending"
    stored = json.loads(
        (deps.session_chat.root / session / f"{pending['id']}.json").read_text(encoding="utf-8")
    )
    assert stored["clarification_context"] == [original]
    assert stored["clarification_parent_id"] == clarification["id"]

    _complete(deps, active_run)
    rebuilt, restored = _client(tmp_path)
    retry = rebuilt.post(f"/sessions/{session}/chat/{pending['id']}/retry")
    assert retry.status_code == 200, retry.text
    run = restored.run_service.get_run(retry.json()["run_id"])

    assert run.conversation.clarification_context == (original,)
    assert original in run.conversation.requested_work
    assert "german_credit" in run.conversation.requested_work
    assert provider.calls
    assert len(provider.calls) == 1
    assert _chat(rebuilt, session, text="german_credit", key="dataset").json() == retry.json()


@pytest.mark.parametrize("mode", ["auto", "ask"])
def test_standalone_action_ignores_impossible_first_message_continuation(tmp_path, mode):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _continue_work(deps)
    value = _chat(client, session, text="Haz un EDA de german_credit", mode=mode).json()
    if mode == "ask":
        assert value["status"] == "failed"
        assert deps.session_service.get_session(session).run_ids == ()
        return
    assert value["status"] == "completed", value
    run = deps.run_service.get_run(value["run_id"])
    assert run.conversation.clarification_context == ()
    assert run.conversation.requested_work == "Haz un EDA de german_credit"


@pytest.mark.parametrize("confirm", [False, True])
def test_dataset_answer_preserves_work_scope_after_restart_and_retry(tmp_path, confirm):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Which dataset?", sources=())
    _chat(client, session, text="Quiero realizar un EDA del dataset")
    if confirm:
        _continue_work(deps, kind="clarification", text="EDA on german_credit?")
        _chat(client, session, text="german_credit", key="dataset")
    rebuilt, restored = _client(tmp_path)
    provider = _continue_work(restored)
    current = "Si" if confirm else "german_credit"
    response = _chat(rebuilt, session, text=current, key="work")
    assert response.status_code == 201, response.text
    run = restored.run_service.get_run(response.json()["run_id"])
    assert run.conversation.message == current
    assert "Quiero realizar un EDA del dataset" in run.conversation.requested_work
    assert "german_credit" in run.conversation.requested_work
    assert "Which dataset?" not in run.conversation.requested_work
    assert run.prompt.endswith(run.conversation.requested_work)
    assert _chat(rebuilt, session, text=current, key="work").json() == response.json()
    assert len(provider.calls) == 1
    reopened, recovered = _client(tmp_path)
    assert recovered.run_service.get_run(run.id).conversation == run.conversation
    assert len(reopened.get(f"/sessions/{session}").json()["turns"]) == 1


def test_linked_yes_completes_explicit_training_after_reopen(tmp_path):
    client, deps = _client(tmp_path)
    _declare_german_credit(tmp_path, target="id")
    session = client.post("/sessions", json={}).json()["id"]
    request = "Train a new model on german_credit with target id."
    _continue_work(deps, kind="clarification", text="Continue with this training request?")

    pending = _chat(client, session, text=request, key="train-request").json()

    assert pending["kind"] == "clarification"
    assert pending["run_id"] is None
    assert deps.session_service.get_session(session).run_ids == ()

    reopened, restored = _client(tmp_path)
    _continue_work(restored, kind="action", continues=False)

    response = _chat(reopened, session, text="Yes", key="confirm-train")

    assert response.status_code == 201, response.text
    run = restored.run_service.get_run(response.json()["run_id"])
    assert run.conversation is not None
    assert run.conversation.work_kind == "train_new"
    assert run.conversation.message == "Yes"
    assert run.conversation.clarification_context == (request,)


@pytest.mark.parametrize(
    ("missing", "question", "reply"),
    [
        ("dataset", "Which registered dataset should the model use?", "german_credit"),
        ("target", "Which column should the model predict?", "id"),
    ],
)
def test_missing_training_input_is_persisted_and_linked_across_restart(
    tmp_path, missing, question, reply
):
    client, deps = _client(tmp_path)
    if missing == "target":
        _declare_german_credit(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    request = "Train a new model"
    first_provider = _continue_work(deps, kind="clarification", text=question)

    pending = _chat(client, session, text=request, key=f"missing-{missing}").json()

    assert pending["kind"] == "clarification"
    assert pending["assistant_message"] == question
    assert pending["run_id"] is None
    stored = LocalDiscussionRepository(deps.session_chat.root, session).records()[-1]
    assert stored.clarification_scope == "work"
    assert stored.discussion.user_message == request
    assert deps.session_service.get_session(session).run_ids == ()
    if missing == "target":
        assert '"target":null' in first_provider.calls[0]["prompt"]
    else:
        assert "No datasets are declared" in first_provider.calls[0]["prompt"]

    reopened, restored = _client(tmp_path)
    if missing == "dataset":
        _declare_german_credit(tmp_path, target="id")
    _continue_work(restored, kind="action", continues=False)

    response = _chat(reopened, session, text=reply, key=f"reply-{missing}")

    assert response.status_code == 201, response.text
    value = response.json()
    run = restored.run_service.get_run(value["run_id"])
    assert run.conversation is not None
    assert run.conversation.work_kind == "train_new"
    assert run.conversation.message == reply
    assert run.conversation.clarification_context == (request,)
    assert request in run.conversation.requested_work
    assert question not in run.conversation.requested_work


def test_old_work_and_explicit_questions_do_not_become_new_work_scope(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    first = _chat(client, session, text="Train a classifier", mode="action").json()
    _complete(deps, first["run_id"])
    _provider(deps, kind="clarification", text="Which training metric?", sources=())
    _chat(client, session, text="Explain model training", key="ask", mode="ask")
    _provider(deps, kind="action", text="", sources=())
    response = _chat(client, session, text="Run an EDA", key="eda")
    run = deps.run_service.get_run(response.json()["run_id"])
    assert run.conversation.requested_work == "Run an EDA"


def test_chat_receives_only_declared_dataset_metadata_and_typed_dialogue(tmp_path):
    client, deps = _client(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "data").mkdir()
    (workspace / "data/sample.csv").write_text("private,row\nDO_NOT_SEND,1\n", encoding="utf-8")
    (workspace / ".thymira/config.yaml").write_text(
        "project:\n  name: conversations\n  domain: research\n"
        "datasets:\n  - name: german_credit\n    path: data/sample.csv\n",
        encoding="utf-8",
    )
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Which dataset?", sources=())
    _chat(client, session, text="Quiero realizar un EDA del dataset")
    provider = _continue_work(deps)
    response = _chat(client, session, text="german_credit", key="dataset")
    assert response.status_code == 201, response.text
    prompt = provider.calls[0]["prompt"]
    assert '"name":"german_credit","present":true' in prompt
    assert '"kind":"clarification"' in prompt
    assert '"mode":"auto"' in prompt
    assert "DO_NOT_SEND" not in prompt


@pytest.mark.parametrize("needs_dataset", [False, True])
def test_new_action_does_not_inherit_an_abandoned_clarification(tmp_path, needs_dataset):
    client, deps = _client(tmp_path)
    if needs_dataset:
        _declare_german_credit(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Which model?", sources=())
    _chat(client, session, text="Train a model")
    _continue_work(
        deps,
        kind="clarification" if needs_dataset else "action",
        text="Which dataset?",
        continues=False,
        replaces=True,
    )
    value = _chat(client, session, text="Instead, just do an EDA", key="new").json()
    assert value["status"] == "failed"
    assert value["run_id"] is None
    assert "Request work" in value["error"]
    value = _chat(
        client,
        session,
        text="Instead, just do an EDA of german_credit",
        key="explicit-replacement",
        mode="action",
    ).json()
    _rebuilt, restored = _client(tmp_path)
    run = restored.run_service.get_run(value["run_id"])
    assert "Instead, just do an EDA" in run.conversation.requested_work
    assert "Train a model" not in run.conversation.requested_work
    classification_prompt = _classification_prompt(restored, run)
    assert run.conversation.requested_work in classification_prompt
    assert "Instead, just do an EDA" in classification_prompt
    assert "Train a model" not in classification_prompt


def test_explicit_dataset_replacement_reaches_classifier_after_reopen(tmp_path):
    client, deps = _client(tmp_path)
    _declare_german_credit(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "data/second_dataset.csv").write_text("score\n4\n7\n", encoding="utf-8")
    config = workspace / ".thymira/config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + "  - name: second_dataset\n    path: data/second_dataset.csv\n",
        encoding="utf-8",
    )
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Which profile facts should I report?", sources=())
    pending = _chat(client, session, text="Run an EDA of german_credit").json()
    assert pending["kind"] == "clarification"
    assert pending["run_id"] is None

    replacement_text = "Instead, run an EDA of second_dataset"
    replacement = _chat(
        client,
        session,
        text=replacement_text,
        key="replace-dataset",
        mode="action",
    ).json()
    assert replacement["kind"] == "action"
    assert replacement["run_id"] is not None

    rebuilt, restored = _client(tmp_path)
    run = restored.run_service.get_run(replacement["run_id"])
    assert run.conversation.message == replacement_text
    assert run.conversation.requested_work == replacement_text
    assert "second_dataset" in run.conversation.requested_work
    assert "german_credit" not in run.conversation.requested_work
    classifier_prompt = _classification_prompt(restored, run)
    assert replacement_text in classifier_prompt
    assert "second_dataset" in classifier_prompt
    assert "german_credit" not in classifier_prompt

    turns = rebuilt.get(f"/sessions/{session}").json()["discussions"]
    assert turns[0]["id"] == pending["id"]
    assert turns[0]["kind"] == "clarification"
    assert turns[0]["run_id"] is None
    assert turns[1]["id"] == replacement["id"]
    assert turns[1]["run_id"] == replacement["run_id"]


@pytest.mark.parametrize("boundary", ["ask", "legacy_run", "oversized"])
def test_ineligible_clarification_cannot_be_promoted_into_work(tmp_path, boundary):
    client, deps = _client(tmp_path)
    _declare_german_credit(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Which dataset?", sources=())
    _chat(
        client,
        session,
        text="Run an EDA" if boundary != "oversized" else "x" * 8001,
        mode="ask" if boundary == "ask" else "auto",
    )
    if boundary == "legacy_run":
        run_id = _send(client, session).json()["id"]
        _complete(deps, run_id)
    _continue_work(deps, continues=False)
    response = _chat(client, session, text="german_credit", key="dataset").json()
    assert response["status"] == "failed"
    assert response["run_id"] is None
    assert len(deps.session_service.get_session(session).run_ids) == (boundary == "legacy_run")


def test_pending_action_freezes_clarification_before_later_questions_and_retry(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    active = _send(client, session).json()["id"]
    _provider(deps, kind="clarification", text="Which dataset?", sources=())
    _chat(client, session, text="Next, run an EDA")
    provider = _continue_work(deps)
    assert _chat(client, session, text="german_credit", key="dataset").status_code == 409
    pending = client.get(f"/sessions/{session}").json()["discussions"][-1]
    _provider(deps, text="Work is still active.", sources=("r0",))
    _chat(client, session, text="Why is it waiting?", key="question", mode="ask")
    _complete(deps, active)
    rebuilt, restored = _client(tmp_path)
    result = rebuilt.post(f"/sessions/{session}/chat/{pending['id']}/retry").json()
    run = restored.run_service.get_run(result["run_id"])
    assert run.conversation.clarification_context == ("Next, run an EDA",)
    assert run.conversation.message == "german_credit"
    assert "Why is it waiting?" not in run.conversation.requested_work
    assert len(provider.calls) == 1


def test_legacy_clarification_requires_complete_request_instead_of_guessing_links(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    _provider(deps, kind="clarification", text="Confirm the EDA?", sources=())
    value = _chat(client, session, text="german_credit").json()
    path = deps.session_chat.root / session / f"{value['id']}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored.pop("clarification_parent_id")
    path.write_text(json.dumps(stored), encoding="utf-8")
    _continue_work(deps)
    result = _chat(client, session, text="Si", key="legacy-reply").json()
    assert result["status"] == "failed"
    assert "restate the complete task and dataset" in result["error"]
    assert deps.session_service.get_session(session).run_ids == ()
