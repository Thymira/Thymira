"""Real API, lifecycle and disk persistence; execution is a bounded dispatcher double."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

from tests.thymira.api_support import TEST_CREDENTIAL, api_credential, authenticated_client
from thymira.api import build_default_deps, create_app
from thymira.core import ExecutionDispatchError, RunController, RunTransitionKind
from thymira.core.governance_binding import final_governance_binding
from thymira.core.session_chat_context import session_evidence
from thymira.events import sha256_text, verify_events
from thymira.mira.checks import AuditReport
from thymira.schemas import Actor, AuditFinding, EventType, Evidence, Framework, Severity, new_id
from thymira.schemas.run import SessionConversation

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


class _Dispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def submit(self, run_id: str) -> None:
        if self.fail:
            raise ExecutionDispatchError(f"dispatch failed for {run_id}")

    def resume(self, run_id: str) -> None:
        del run_id


def _client(tmp_path: Path, *, fail: bool = False):
    workspace = tmp_path / "workspace"
    config = workspace / ".thymira" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("project:\n  name: conversations\n  domain: research\n", encoding="utf-8")
    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        dispatcher=_Dispatcher(fail=fail),
        principal_resolver=TEST_CREDENTIAL,
    )
    return authenticated_client(create_app(deps)), deps


@pytest.fixture(autouse=True)
def _route_policy(monkeypatch):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")


def _send(client, session_id, message="Inspect the sample", request_id="request-one"):
    return client.post(
        f"/sessions/{session_id}/messages",
        json={"message": message, "client_message_id": request_id},
    )


def _complete(deps, run_id, response="The sample has 27 rows."):
    controller = RunController(deps.run_store)
    controller.advance(run_id, RunTransitionKind.START)
    controller.advance(run_id, RunTransitionKind.BEGIN_EXECUTION)
    deps.event_store.open(run_id).append(
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {"kind": "conversation.response", "content": response},
        subject_id=run_id,
        producer="thymira.core",
    )
    controller.advance(run_id, RunTransitionKind.BEGIN_AUDIT)
    log = deps.event_store.open(run_id)
    gate = deps.gate_factory(run_id)
    report = AuditReport(
        run_id=run_id,
        status="passed",
        controls=(),
        terminal_hash=log.events()[-1].hash,
        policy_sha256=gate.engine.policy_sha256,
    )
    log.append(
        EventType.AUDIT_COMPLETED,
        Actor.system(),
        {"audit_report": report.model_dump(mode="json"), "audit_revision": 1},
        subject_id=run_id,
    )
    decision = gate.review_findings(())
    binding = final_governance_binding(run_id, decision, log.events())
    controller.advance(run_id, RunTransitionKind.BEGIN_REPORTING)
    controller.advance(run_id, RunTransitionKind.COMPLETE, payload=binding)


def test_conversation_followup_and_outputs_survive_application_reconstruction(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={"title": "Sample inspection"}).json()
    sent = _send(client, session["id"])
    assert sent.status_code == 201, sent.text
    first = sent.json()
    _complete(deps, first["id"])
    artifact = deps.artifact_store_factory(first["id"]).save_text(
        "summary.md", "27 rows", produced_by=first["id"]
    )

    reopened, restored = _client(tmp_path)
    second = _send(reopened, session["id"], "Explain that row count", "request-two")
    assert second.status_code == 201, second.text
    run = restored.run_service.get_run(second.json()["id"])
    assert "The sample has 27 rows." in run.prompt
    assert "Inspect the sample" in run.prompt
    history = reopened.get(f"/sessions/{session['id']}").json()
    assert [turn["ordinal"] for turn in history["turns"]] == [1, 2]
    assert history["turns"][0]["assistant_message"] == "The sample has 27 rows."
    assert history["turns"][1]["assistant_message"] is None
    assert history["active_run_id"] == run.id
    assert reopened.get(f"/runs/{first['id']}/artifacts/{artifact.id}").status_code == 200
    assert verify_events(restored.event_store.read(first["id"])).valid


def test_identical_message_retry_after_restart_returns_same_turn(tmp_path):
    client, _ = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    first = _send(client, session["id"])
    reopened, _ = _client(tmp_path)
    retry = _send(reopened, session["id"])
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == first.json()["id"]
    assert len(reopened.get(f"/sessions/{session['id']}").json()["turns"]) == 1


@pytest.mark.parametrize("confinement_only", [False, True])
def test_blocked_turn_explains_bound_audit_and_tool_failure_after_restart(
    tmp_path, confinement_only
):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    run_id = _send(client, session["id"]).json()["id"]
    controller = RunController(deps.run_store)
    controller.advance(run_id, RunTransitionKind.START)
    controller.advance(run_id, RunTransitionKind.BEGIN_EXECUTION)
    log = deps.event_store.open(run_id)
    failure = log.append(
        EventType.TOOL_COMPLETED,
        Actor.system(),
        {
            "tool": "run_python",
            "error": "could not convert string to float"
            if confinement_only
            else "container runtime unavailable: docker was not found",
            "sandbox_enforcement": "partial" if confinement_only else "failed",
            "sandbox_spec": {"unenforced": ["rlimits", "workspace_quota"]},
        },
    )
    log.append(EventType.TOOL_COMPLETED, Actor.system(), {"error": "unrelated old error"})
    log.append(
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {"kind": "conversation.response", "content": "Unapproved conclusion"},
        subject_id=run_id,
        producer="thymira.core",
    )
    controller.advance(run_id, RunTransitionKind.BEGIN_AUDIT)
    finding = AuditFinding(
        id=new_id("finding"),
        run_id=run_id,
        control_id="A19",
        framework=Framework.INTERNAL,
        title="Confinement enforced",
        finding="run_python confinement was unusable",
        severity=Severity.CRITICAL,
        confidence=1.0,
        recommendation="Configure and verify the execution environment before starting new work.",
        evidence=(Evidence(kind="event", ref=f"seq:{failure.seq}", sha256=failure.hash),),
    )
    log.append(
        EventType.AUDIT_FINDING, Actor.system(), {"finding": finding.model_dump(mode="json")}
    )
    gate = deps.gate_factory(run_id)
    report = AuditReport(
        run_id=run_id,
        status="failed",
        controls=(),
        findings=(finding,),
        terminal_hash=log.events()[-1].hash,
        policy_sha256=gate.engine.policy_sha256,
    )
    log.append(
        EventType.AUDIT_COMPLETED,
        Actor.system(),
        {
            "audit_report": report.model_dump(mode="json"),
            "audit_revision": 1,
        },
    )
    decision = gate.review_findings((finding,))
    binding = final_governance_binding(run_id, decision, log.events())
    controller.advance(run_id, RunTransitionKind.BLOCK, payload=binding)
    before = client.get(f"/sessions/{session['id']}").json()["turns"][0]
    reopened, restored = _client(tmp_path)
    after = reopened.get(f"/sessions/{session['id']}").json()["turns"][0]
    diagnostic = after["terminal_diagnostic"]
    assert diagnostic == before["terminal_diagnostic"]
    assert diagnostic["policy_rule"] == decision.rule_id
    assert "critical" in diagnostic["summary"]
    assert any("A19" in detail for detail in diagnostic["details"])
    if confinement_only:
        assert "could not convert string" not in str(diagnostic)
        assert "workspace_quota" in str(diagnostic)
    else:
        assert any("docker was not found" in detail for detail in diagnostic["details"])
    assert diagnostic["next_steps"] == [finding.recommendation]
    evidence, _sources = session_evidence(
        SessionConversation.model_validate_json(reopened.get(f"/sessions/{session['id']}").text),
        restored.event_store,
    )
    assert finding.recommendation is not None
    assert finding.recommendation in evidence
    assert "unrelated old error" not in str(diagnostic)
    assert after["assistant_message"] is None
    assert "Unapproved conclusion" not in str(diagnostic)
    assert verify_events(restored.event_store.read(run_id)).valid


def test_conflicting_retry_and_concurrent_turn_are_refused(tmp_path):
    client, _ = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    assert _send(client, session["id"]).status_code == 201
    conflict = _send(client, session["id"], "A different message")
    busy = _send(client, session["id"], "Another turn", "request-two")
    assert (conflict.status_code, conflict.json()["code"]) == (409, "message_conflict")
    assert (busy.status_code, busy.json()["code"]) == (409, "session_busy")


def test_simultaneous_sends_publish_exactly_one_turn(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    barrier = Barrier(2)

    def send_at_once(request_id):
        with authenticated_client(create_app(deps)) as sender:
            barrier.wait(timeout=10)
            return _send(sender, session["id"], "Inspect concurrently", request_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(send_at_once, ("concurrent-one", "concurrent-two")))

    assert sorted(response.status_code for response in responses) == [201, 409]
    persisted = client.get(f"/sessions/{session['id']}").json()
    assert len(persisted["turns"]) == 1
    assert persisted["turns"][0]["ordinal"] == 1


def test_failed_turn_stays_visible_and_allows_followup(tmp_path):
    client, _ = _client(tmp_path, fail=True)
    session = client.post("/sessions", json={}).json()
    first = _send(client, session["id"])
    detail = client.get(f"/sessions/{session['id']}").json()
    assert detail["turns"][0]["state"]["outcome"] == "failed"
    assert detail["turns"][0]["assistant_message"] is None
    assert detail["active_run_id"] is None
    assert _send(client, session["id"], "Try another approach", "request-two").status_code == 201
    assert first.json()["id"] == detail["turns"][0]["id"]


def test_cancelled_turn_can_be_followed_up_without_losing_history(tmp_path):
    client, _ = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    first = _send(client, session["id"]).json()
    assert client.post(f"/runs/{first['id']}/cancel").status_code == 202
    history = client.get(f"/sessions/{session['id']}").json()
    assert history["turns"][0]["state"]["outcome"] == "cancelled"
    assert history["active_run_id"] is None
    assert _send(client, session["id"], "Use a smaller sample", "request-two").status_code == 201


def test_unverified_history_returns_a_safe_conflict_and_cannot_enter_next_prompt(
    tmp_path, monkeypatch
):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    first = _send(client, session["id"]).json()
    _complete(deps, first["id"])
    original_read = deps.event_store.read

    def corrupted_read(run_id):
        events = original_read(run_id)
        return [events[0].model_copy(update={"payload": {"tampered": True}}), *events[1:]]

    monkeypatch.setattr(deps.event_store, "read", corrupted_read)
    detail = client.get(f"/sessions/{session['id']}")
    followup = _send(client, session["id"], "Use earlier output", "request-two")
    for response in (detail, followup):
        assert response.status_code == 409
        assert response.json()["code"] == "conversation_history_unavailable"
        assert "tampered" not in response.text
    assert len(deps.session_service.get_session(session["id"]).run_ids) == 1


def test_standalone_run_contract_remains_available_and_cannot_bypass_conversation_order(tmp_path):
    client, _ = _client(tmp_path)
    standalone = client.post("/runs", json={"prompt": "Standalone analysis"})
    assert standalone.status_code == 201
    assert standalone.json()["prompt"] == "Standalone analysis"
    assert client.get("/sessions").json()["items"] == []
    session = client.post("/sessions", json={}).json()
    bypass = client.post("/runs", json={"prompt": "Bypass", "session_id": session["id"]})
    assert bypass.status_code == 409
    assert client.get(f"/sessions/{session['id']}").json()["turns"] == []


@pytest.mark.parametrize(
    "path", ["/sessions", "/sessions/session_00000000000000000000000000000000"]
)
def test_conversation_reads_require_existing_authentication(tmp_path, path):
    client, _ = _client(tmp_path)
    client.headers.clear()
    assert client.get(path).status_code == 401


def test_foreign_project_session_is_not_disclosed(tmp_path):
    client, deps = _client(tmp_path)
    foreign = deps.conversation_workspace.create(
        project_id=new_id("project"), title="Private project", client="web"
    )
    assert client.get(f"/sessions/{foreign.id}").status_code == 404
    assert _send(client, foreign.id).status_code == 404
    assert client.get("/sessions").json()["items"] == []


def test_viewer_can_read_project_conversations_but_cannot_send(tmp_path):
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={"title": "Shared project work"}).json()
    token, credential = api_credential("viewer", "reader")
    viewer = authenticated_client(create_app(replace(deps, principal_resolver=credential)), token)
    assert viewer.get(f"/sessions/{session['id']}").status_code == 200
    assert viewer.post("/sessions", json={}).status_code == 403
    assert _send(viewer, session["id"]).status_code == 403


def test_project_explorer_routes_return_bounded_text_and_refuse_private_paths(tmp_path):
    client, _ = _client(tmp_path)
    (tmp_path / "workspace" / "readme.md").write_text("# Project notes", encoding="utf-8")
    listing = client.get("/project/files").json()
    assert listing["entries"] == [{"path": "readme.md", "name": "readme.md", "kind": "file"}]
    preview = client.get("/project/file", params={"path": "readme.md"})
    assert preview.json() == {
        "path": "readme.md",
        "content": "# Project notes",
        "truncated": False,
        "sha256": sha256_text("# Project notes"),
        "editable": True,
        "read_only_reason": None,
    }
    assert client.get("/project/file", params={"path": ".thymira/config.yaml"}).status_code == 404
    assert client.get("/project/files", params={"path": "../"}).status_code == 404


@pytest.mark.parametrize("message", ["", "   ", "x" * 16001])
def test_invalid_messages_do_not_create_turns(tmp_path, message):
    client, _ = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    assert _send(client, session["id"], message).status_code == 422
    assert client.get(f"/sessions/{session['id']}").json()["turns"] == []
