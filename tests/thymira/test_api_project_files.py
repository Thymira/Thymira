"""Authenticated API tests for the project explorer's bounded file editing surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.thymira.api_support import api_credential, authenticated_client
from thymira.api import build_default_deps, create_app
from thymira.core.project_files import MAX_FILE_BYTES
from thymira.events import sha256_text

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from thymira.schemas import Id

pytestmark = pytest.mark.integration


class _Dispatcher:
    """Keep API composition valid without running a graph or model."""

    def submit(self, run_id: Id) -> None:
        """Accept a submission without dispatching it."""
        del run_id

    def resume(self, run_id: Id) -> None:
        """Accept a resume without dispatching it."""
        del run_id


def _client(tmp_path: Path, *, role: str = "admin") -> tuple[TestClient, Path]:
    """Build the authenticated application over a temporary configured project."""
    workspace = tmp_path / "workspace"
    config = workspace / ".thymira" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project:\n  name: files\n  domain: research\n", encoding="utf-8")
    token, credential = api_credential(role, f"{role}-actor")
    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        dispatcher=_Dispatcher(),
        principal_resolver=credential,
    )
    return authenticated_client(create_app(deps), token), workspace


def test_file_read_returns_revision_and_editability_metadata(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    source = workspace / "notes.md"
    source.write_text("Project notes\n", encoding="utf-8", newline="\n")

    response = client.get("/project/file", params={"path": "notes.md"})

    assert response.status_code == 200
    assert response.json() == {
        "path": "notes.md",
        "content": "Project notes\n",
        "truncated": False,
        "sha256": sha256_text("Project notes\n"),
        "editable": True,
        "read_only_reason": None,
    }


def test_file_put_persists_and_reads_back_an_existing_file(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    source = workspace / "notes.md"
    source.write_text("Before\n", encoding="utf-8", newline="\n")
    revision = client.get("/project/file", params={"path": "notes.md"}).json()["sha256"]

    response = client.put(
        "/project/file",
        json={
            "path": "notes.md",
            "content": "After\n",
            "expected_sha256": revision,
        },
    )

    assert response.status_code == 200
    assert source.read_bytes() == b"After\n"
    assert response.json()["content"] == "After\n"
    assert response.json()["sha256"] == sha256_text("After\n")
    assert response.json()["editable"] is True
    assert client.get("/project/file", params={"path": "notes.md"}).json() == response.json()


def test_file_put_reports_a_stale_revision_without_overwriting(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    source = workspace / "notes.md"
    source.write_text("Original\n", encoding="utf-8", newline="\n")
    stale = client.get("/project/file", params={"path": "notes.md"}).json()["sha256"]
    source.write_text("External edit\n", encoding="utf-8", newline="\n")

    response = client.put(
        "/project/file",
        json={"path": "notes.md", "content": "Draft\n", "expected_sha256": stale},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "project_file_conflict"
    assert source.read_text(encoding="utf-8") == "External edit\n"


def test_file_put_refuses_missing_traversal_and_symlink_targets(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("Outside stays private", encoding="utf-8")
    body = {"content": "Draft", "expected_sha256": sha256_text("anything")}

    missing = client.put("/project/file", json={**body, "path": "new.md"})
    traversal = client.put("/project/file", json={**body, "path": "../outside.md"})

    assert missing.status_code == 404
    assert traversal.status_code == 404
    assert not (workspace / "new.md").exists()
    assert outside.read_text(encoding="utf-8") == "Outside stays private"
    link = workspace / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this Windows account cannot create symbolic links")
    linked = client.put("/project/file", json={**body, "path": "linked.md"})
    assert linked.status_code == 404
    assert outside.read_text(encoding="utf-8") == "Outside stays private"


def test_redacted_and_truncated_files_are_preview_only(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path)
    private = workspace / "personal.md"
    private.write_text("Contact ana@example.com\n", encoding="utf-8", newline="\n")
    large = workspace / "large.md"
    large.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    private_revision = sha256_text("Contact ana@example.com\n")
    original_large = large.read_bytes()

    redacted = client.get("/project/file", params={"path": "personal.md"}).json()
    truncated = client.get("/project/file", params={"path": "large.md"}).json()
    redacted_save = client.put(
        "/project/file",
        json={
            "path": "personal.md",
            "content": "Safe replacement",
            "expected_sha256": private_revision,
        },
    )
    truncated_save = client.put(
        "/project/file",
        json={
            "path": "large.md",
            "content": "Safe replacement",
            "expected_sha256": sha256_text("unknown"),
        },
    )

    assert redacted["editable"] is False
    assert redacted["read_only_reason"] == "redacted"
    assert "ana@example.com" not in redacted["content"]
    assert truncated["editable"] is False
    assert truncated["truncated"] is True
    assert truncated["read_only_reason"] == "truncated"
    assert truncated["sha256"] is None
    assert redacted_save.status_code == truncated_save.status_code == 422
    assert redacted_save.json()["code"] == truncated_save.json()["code"] == "project_file_read_only"
    assert large.read_bytes() == original_large


def test_file_writes_require_write_permission(tmp_path: Path) -> None:
    client, workspace = _client(tmp_path, role="viewer")
    source = workspace / "notes.md"
    source.write_text("Visible", encoding="utf-8")
    revision = client.get("/project/file", params={"path": "notes.md"}).json()["sha256"]

    response = client.put(
        "/project/file",
        json={"path": "notes.md", "content": "Changed", "expected_sha256": revision},
    )

    assert response.status_code == 403
    assert source.read_text(encoding="utf-8") == "Visible"
