"""Exact source excerpts remain read-only, verified and separate from new work intake."""

import json

import pytest

from tests.thymira.test_api_sessions import _client, _complete, _send
from tests.thymira.test_session_discussions import _chat
from thymira.agents.llm import ScriptedProvider
from thymira.schemas import Actor, ArtifactKind, EventType

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")


def _source(deps, run_id):
    store = deps.artifact_store_factory(run_id)
    artifact = store.save_text(
        "eda.py",
        "import seaborn as sns\n# numeric columns only\nsns.heatmap(df.corr(numeric_only=True))\n",
        produced_by=run_id,
        kind=ArtifactKind.CODE,
    )
    event = deps.event_store.open(run_id).append(
        EventType.ARTIFACT_CREATED,
        Actor.system(),
        {"artifact_id": artifact.id, "name": artifact.name, "sha256": artifact.sha256},
    )
    return artifact, event


def test_exact_heatmap_lines_are_rendered_from_verified_bytes_without_new_run(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session).json()["id"]
    artifact, event = _source(deps, run_id)
    _complete(deps, run_id)
    deps.session_chat.provider = ScriptedProvider(
        [
            {
                "kind": "answer",
                "message": "invented code must not be used",
                "sources": [],
                "source_excerpt": {"source": f"r0e{event.seq}", "contains": "heatmap"},
            },
        ],
        model="test-model",
    )
    answer = _chat(client, session, text="Devuelve las líneas exactas del heatmap").json()
    assert answer["status"] == "completed", answer
    assert "sns.heatmap(df.corr(numeric_only=True))" in answer["assistant_message"]
    assert "3 | sns.heatmap" in answer["assistant_message"]
    assert artifact.sha256 in answer["assistant_message"]
    assert "invented" not in answer["assistant_message"]
    assert answer["citations"][0]["seq"] == event.seq
    assert deps.session_service.get_session(session).run_ids == (run_id,)
    assert len(deps.session_chat.provider.calls) == 1


def test_read_only_clarification_does_not_contaminate_a_later_training_request(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    deps.session_chat.provider = ScriptedProvider(
        [
            {
                "kind": "clarification",
                "message": "Which source file?",
                "clarification_scope": "evidence",
            },
            {
                "kind": "clarification",
                "message": "Please name the file.",
                "clarification_scope": "evidence",
                "continues_clarification": True,
            },
            {"kind": "action", "message": "", "work_kind": "train_new"},
        ],
        model="test-model",
    )
    first = _chat(client, session, text="Show the heatmap source").json()
    assert first["status"] == "completed", first
    second = _chat(client, session, text="Yes", key="yes").json()
    assert second["status"] == "completed", second
    final = _chat(
        client, session, text="Now train a simple model on german_credit", key="train"
    ).json()
    assert final["status"] == "completed", final
    run = deps.run_service.get_run(final["run_id"])
    assert run.conversation.clarification_context == ()
    assert run.conversation.work_kind == "train_new"
    saved = json.loads((deps.session_chat.root / session / f"{first['id']}.json").read_text())
    assert saved["clarification_scope"] == "evidence"


@pytest.mark.parametrize("defect", ["tampered", "unapproved", "foreign_session", "invalidated"])
def test_untrusted_source_revision_never_becomes_a_quotation(tmp_path, defect):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    source_session = (
        client.post("/sessions", json={}).json()["id"] if defect == "foreign_session" else session
    )
    run_id = _send(client, source_session).json()["id"]
    artifact, event = _source(deps, run_id)
    if defect != "unapproved":
        _complete(deps, run_id)
    if defect == "tampered":
        path = tmp_path / "runtime" / "artifacts" / run_id / artifact.uri
        path.write_bytes(b"private replacement code")
    if defect == "invalidated":
        deps.artifact_store_factory(run_id).invalidate((artifact.name,), "test invalidation")
    deps.session_chat.provider = ScriptedProvider(
        [
            {
                "kind": "answer",
                "message": "untrusted replacement",
                "sources": [],
                "source_excerpt": {"source": f"r0e{event.seq}", "contains": "heatmap"},
            },
        ],
        model="test-model",
    )
    answer = _chat(client, session, text="Show the exact heatmap source").json()
    assert answer["status"] == "failed", answer
    assert answer["assistant_message"] is None
    assert len(deps.session_chat.provider.calls) == 1


def test_missing_source_match_is_honest_and_does_not_quote_another_function(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session).json()["id"]
    _, event = _source(deps, run_id)
    _complete(deps, run_id)
    deps.session_chat.provider = ScriptedProvider(
        [
            {
                "kind": "answer",
                "message": "invented",
                "source_excerpt": {
                    "source": f"r0e{event.seq}",
                    "contains": "not_in_the_source",
                },
            },
        ],
        model="test-model",
    )
    answer = _chat(client, session, text="Show this function's source").json()
    assert answer["status"] == "completed", answer
    assert "No matching source text" in answer["assistant_message"]
    assert "sns.heatmap" not in answer["assistant_message"]
