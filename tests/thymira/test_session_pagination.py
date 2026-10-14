"""Regression coverage for filtered Session repository pagination."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from thymira.core import SessionService
from thymira.schemas import Session, new_id
from thymira.state import InMemorySessionRepository, LocalSessionRepository, SessionRepository
from thymira.state.postgres import repositories as pg_repositories
from thymira.state.postgres.repositories import PgSessionRepository

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(params=("local", "memory"))
def session_repository(request: pytest.FixtureRequest, tmp_path: Path) -> SessionRepository:
    """Build each local Session backend behind the shared protocol."""
    if request.param == "local":
        return LocalSessionRepository(tmp_path)
    return InMemorySessionRepository()


def _ordered_sessions(project_id: str) -> tuple[Session, ...]:
    """Create interleaved standalone and conversation sessions with stable timestamps."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    return tuple(
        Session(
            id=new_id("session"),
            project_id=project_id,
            client="web",
            conversation=index % 2 == 1,
            created_at=start + timedelta(seconds=index),
        )
        for index in range(9)
    )


def test_session_filter_precedes_cursor_pagination_and_none_keeps_existing_order(
    session_repository: SessionRepository,
) -> None:
    """Filtered pages contain only conversations while unfiltered cursors keep their contract."""
    project_id = new_id("project")
    sessions = _ordered_sessions(project_id)
    for session in sessions:
        session_repository.save(session)

    all_sessions = session_repository.list(project_id=project_id, limit=3)
    assert all_sessions.items == sessions[:3]
    assert all_sessions.next_cursor is not None
    next_all = session_repository.list(
        project_id=project_id, limit=3, cursor=all_sessions.next_cursor
    )
    assert next_all.items == sessions[3:6]

    conversations = tuple(session for session in sessions if session.conversation)
    first = session_repository.list(project_id=project_id, conversation=True, limit=2)
    assert first.items == conversations[:2]
    assert first.next_cursor is not None
    second = session_repository.list(
        project_id=project_id,
        conversation=True,
        limit=2,
        cursor=first.next_cursor,
    )
    assert second.items == conversations[2:]
    assert second.next_cursor is None
    standalone = session_repository.list(project_id=project_id, conversation=False, limit=3)
    standalone_sessions = tuple(session for session in sessions if not session.conversation)
    assert standalone.items == standalone_sessions[:3]


def test_session_service_passes_optional_conversation_filter(tmp_path: Path) -> None:
    """The core Session listing boundary exposes the repository's conversation filter."""
    repository = LocalSessionRepository(tmp_path)
    sessions = SessionService(repository)
    project_id = new_id("project")
    standalone = Session(id=new_id("session"), project_id=project_id, client="web")
    conversation = Session(
        id=new_id("session"), project_id=project_id, client="web", conversation=True
    )
    repository.save(standalone)
    repository.save(conversation)

    page = sessions.list_sessions(project_id=project_id, conversation=True, limit=5)

    assert page.items == (conversation,)


@pytest.mark.parametrize(("conversation", "expected_value"), [(True, "true"), (False, "false")])
def test_postgres_session_filter_is_sent_to_database_pagination(
    monkeypatch: pytest.MonkeyPatch,
    conversation: bool,
    expected_value: str,
) -> None:
    """PostgreSQL filters the stored JSON field before limiting the cursor page."""
    captured: list[str] = []

    def capture_page(
        engine: sa.Engine,
        table: sa.Table,
        *,
        conditions: list[sa.ColumnElement[bool]],
        limit: int,
        cursor: str | None,
    ) -> tuple[list[str], str | None]:
        del engine, table, limit, cursor
        captured.extend(
            str(
                condition.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            for condition in conditions
        )
        return [], None

    monkeypatch.setattr(pg_repositories, "_paginate", capture_page)
    engine = sa.create_engine("sqlite://")
    try:
        PgSessionRepository(engine).list(conversation=conversation, limit=2)
    finally:
        engine.dispose()

    assert any("CAST(sessions.document AS JSONB)" in condition for condition in captured)
    assert any("->> 'conversation'" in condition for condition in captured)
    assert any(f"= '{expected_value}'" in condition for condition in captured)
    if not conversation:
        assert any("IS NULL" in condition for condition in captured)
