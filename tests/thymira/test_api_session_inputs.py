"""Session sends refuse to publish turns while declared project datasets are missing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.thymira.api_support import TEST_CREDENTIAL, authenticated_client
from thymira.api import build_default_deps, create_app
from thymira.core import RunController, RunTransitionKind
from thymira.core.governance_binding import final_governance_binding
from thymira.mira.checks import AuditReport
from thymira.schemas import Actor, EventType, Id, new_id

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient
    from httpx2 import Response

    from thymira.api.deps import RuntimeDeps

pytestmark = pytest.mark.integration


class _Dispatcher:
    """Keep the API's dispatcher seam active without executing a graph."""

    def submit(self, run_id: Id) -> None:
        """Accept a newly published Run without starting background work."""
        del run_id

    def resume(self, run_id: Id) -> None:
        """Satisfy the dispatcher protocol without resuming background work."""
        del run_id


def _client(
    tmp_path: Path,
    *,
    config_text: str = (
        "project:\n  name: input-check\n  domain: research\n"
        "datasets:\n  - name: german_credit\n    path: data/applications.csv\n"
    ),
) -> tuple[TestClient, RuntimeDeps, Path]:
    project_dir = tmp_path / "workspace"
    config_dir = project_dir / ".thymira"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(config_text, encoding="utf-8", newline="\n")
    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=project_dir,
        dispatcher=_Dispatcher(),
        principal_resolver=TEST_CREDENTIAL,
    )
    return authenticated_client(create_app(deps)), deps, project_dir


def _send(client: TestClient, session_id: str, message: str, request_id: str) -> Response:
    return client.post(
        f"/sessions/{session_id}/messages",
        json={"message": message, "client_message_id": request_id},
    )


def _complete(deps: RuntimeDeps, run_id: Id, response: str) -> None:
    controller = RunController(deps.run_store)
    controller.advance(run_id, RunTransitionKind.START)
    controller.advance(run_id, RunTransitionKind.BEGIN_EXECUTION)
    log = deps.event_store.open(run_id)
    log.append(
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {"kind": "conversation.response", "content": response},
        subject_id=run_id,
        producer="thymira.core",
    )
    controller.advance(run_id, RunTransitionKind.BEGIN_AUDIT)
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


def test_missing_declared_dataset_blocks_new_session_turn_without_allocating_a_run(
    tmp_path: Path,
) -> None:
    client, deps, project_dir = _client(tmp_path)
    session = client.post("/sessions", json={}).json()

    response = _send(client, session["id"], "Inspect the applications", "request-one")

    assert response.status_code == 409
    assert response.json()["code"] == "session_inputs_missing"
    assert "german_credit" in response.json()["message"]
    assert "data/applications.csv" in response.json()["message"]
    assert "upload" in response.json()["message"].lower()
    assert response.json()["details"]["missing_datasets"] == [
        {"name": "german_credit", "path": "data/applications.csv"}
    ]
    assert str(project_dir) not in response.text
    assert deps.session_service.get_session(session["id"]).run_ids == ()
    assert client.get(f"/sessions/{session['id']}").json()["turns"] == []


def test_session_rejects_declared_dataset_symlink_outside_project(tmp_path: Path) -> None:
    client, deps, project_dir = _client(tmp_path)
    external = tmp_path / "private.csv"
    external.write_text("secret\nnot-a-project-input\n", encoding="utf-8")
    source = project_dir / "data" / "applications.csv"
    source.parent.mkdir()
    try:
        source.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
    session = client.post("/sessions", json={}).json()

    response = _send(client, session["id"], "Profile the data", "external-input")

    assert response.status_code == 409
    assert response.json()["code"] == "session_inputs_unavailable"
    assert str(external) not in response.text
    assert "not-a-project-input" not in response.text
    assert deps.session_service.get_session(session["id"]).run_ids == ()


def test_session_refuses_resolved_source_outside_root_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the containment boundary even on hosts that cannot create symlinks."""
    client, deps, project_dir = _client(tmp_path)
    source = project_dir / "data" / "applications.csv"
    source.parent.mkdir()
    source.write_text("value\n1\n", encoding="utf-8")
    session = client.post("/sessions", json={}).json()
    original_resolve = type(source).resolve
    external = tmp_path / "outside.csv"

    def resolve_source(path: Path, *, strict: bool = False) -> Path:
        if path == source:
            return external
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(type(source), "resolve", resolve_source)

    response = _send(client, session["id"], "Profile the data", "escaped-source")

    assert response.status_code == 409
    assert response.json()["code"] == "session_inputs_unavailable"
    assert str(external) not in response.text
    assert deps.session_service.get_session(session["id"]).run_ids == ()


def test_upload_then_send_preserves_retry_and_followup_history(tmp_path: Path) -> None:
    client, deps, project_dir = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    first_message = "Inspect the applications"
    first_id = "request-one"

    blocked = _send(client, session["id"], first_message, first_id)
    assert blocked.status_code == 409

    upload = client.put(
        "/project/datasets/german_credit",
        params={"filename": "applications.csv"},
        content=b"age,income\n30,1000\n45,2000\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload.status_code == 200, upload.text
    first = _send(client, session["id"], first_message, first_id)
    assert first.status_code == 201, first.text
    first_run = first.json()
    _complete(deps, first_run["id"], "The file contains two applications.")

    (project_dir / "data" / "applications.csv").unlink()
    retry = _send(client, session["id"], first_message, first_id)
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == first_run["id"]

    blocked_followup = _send(client, session["id"], "Explain the row count", "request-two")
    assert blocked_followup.status_code == 409
    assert blocked_followup.json()["code"] == "session_inputs_missing"
    assert deps.session_service.get_session(session["id"]).run_ids == (first_run["id"],)

    replacement = client.put(
        "/project/datasets/german_credit",
        params={"filename": "replacement.csv"},
        content=b"age,income\n30,1000\n45,2000\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert replacement.status_code == 200, replacement.text
    second = _send(client, session["id"], "Explain the row count", "request-two")
    assert second.status_code == 201, second.text
    assert "Inspect the applications" in second.json()["run"]["prompt"]
    assert "The file contains two applications." in second.json()["run"]["prompt"]

    detail = client.get(f"/sessions/{session['id']}")
    assert detail.status_code == 200
    turns = detail.json()["turns"]
    assert [turn["ordinal"] for turn in turns] == [1, 2]
    assert turns[0]["assistant_message"] == "The file contains two applications."
    assert turns[1]["user_message"] == "Explain the row count"


def test_malformed_project_config_returns_safe_conflict_without_a_run(tmp_path: Path) -> None:
    client, deps, project_dir = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    sensitive_config = 'project: "credential-like-value"\nprivate: "secret-fragment"\n'
    (project_dir / ".thymira" / "config.yaml").write_text(
        sensitive_config, encoding="utf-8", newline="\n"
    )

    response = _send(client, session["id"], "Inspect the applications", "request-one")

    assert response.status_code == 409
    assert response.json()["code"] == "session_inputs_unavailable"
    assert "secret-fragment" not in response.text
    assert str(project_dir) not in response.text
    assert deps.session_service.get_session(session["id"]).run_ids == ()


def test_empty_valid_project_and_foreign_session_scope_remain_safe(tmp_path: Path) -> None:
    client, _, _project_dir = _client(
        tmp_path / "empty-project",
        config_text="project:\n  name: input-check\n  domain: research\n",
    )
    local_session = client.post("/sessions", json={}).json()
    accepted = _send(client, local_session["id"], "Analyze the project", "request-one")
    assert accepted.status_code == 201, accepted.text

    scoped_client, scoped_deps, _ = _client(tmp_path / "missing-project")
    foreign = scoped_deps.conversation_workspace.create(
        project_id=new_id("project"), title="Private project", client="web"
    )
    response = _send(scoped_client, foreign.id, "Inspect the applications", "foreign-request")
    assert response.status_code == 404
    assert response.json()["code"] == "session_not_found"
    assert "german_credit" not in response.text
