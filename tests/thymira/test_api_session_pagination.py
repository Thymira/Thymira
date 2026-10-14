"""API regression coverage for filtering conversations before page boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tests.thymira.api_support import TEST_CREDENTIAL, authenticated_client
from thymira.api import build_default_deps, create_app
from thymira.schemas import Session, new_id

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_session_route_filters_conversations_before_applying_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standalone sessions on earlier pages cannot hide older conversations from the API."""
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    workspace = tmp_path / "workspace"
    config = workspace / ".thymira" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project:\n  name: pagination\n  domain: research\n", encoding="utf-8")
    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        principal_resolver=TEST_CREDENTIAL,
    )
    assert deps.project_resolution is not None
    project_id = deps.project_resolution.project_id
    start = datetime(2026, 9, 1, tzinfo=UTC)
    sessions = [
        Session(
            id=new_id("session"),
            project_id=project_id,
            client="web",
            conversation=False,
            created_at=start + timedelta(seconds=index),
        )
        for index in range(5)
    ]
    old_conversation = Session(
        id=new_id("session"),
        project_id=project_id,
        client="web",
        conversation=True,
        created_at=start + timedelta(seconds=5),
    )
    for session in (*sessions, old_conversation):
        deps.session_repository.save(session)

    with authenticated_client(create_app(deps)) as client:
        response = client.get("/sessions?limit=5")

    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["id"] for item in page["items"]] == [old_conversation.id]
    assert page["next_cursor"] is None
