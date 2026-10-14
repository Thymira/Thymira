"""Behavioral tests for persistent, event-backed Session conversations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Never

import pytest

from thymira.core import (
    ConversationBusyError,
    ConversationConflictError,
    ConversationHistoryError,
    ConversationWorkspace,
    RunNotFoundError,
    RunService,
    SessionProjectMismatchError,
    SessionService,
)
from thymira.events import verify_events
from thymira.schemas import (
    Actor,
    ActorKind,
    Event,
    EventType,
    PreparedPublication,
    Run,
    RunCondition,
    RunStatus,
    Session,
    new_id,
)
from thymira.state import (
    LocalLifecycleRepository,
    LocalRunStore,
    LocalSessionRepository,
    conversation_lock,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class ConversationFixture:
    workspace: ConversationWorkspace
    run_service: RunService
    run_store: LocalRunStore
    session_service: SessionService
    session_repository: LocalSessionRepository
    lifecycle: LocalLifecycleRepository
    project_id: str
    actor: Actor
    root: Path


class RunStoreEventReader:
    """Adapt LocalRunStore's authoritative event reader to the EventStore protocol."""

    def __init__(self, store: LocalRunStore) -> None:
        self._store = store

    def open(self, run_id: str) -> Never:
        raise NotImplementedError

    def read(self, run_id: str) -> list[Event]:
        return self._store.events(run_id)


@pytest.fixture
def conversation(tmp_path: Path) -> ConversationFixture:
    root = tmp_path / "runtime"
    run_store = LocalRunStore(root / "runs")
    session_repository = LocalSessionRepository(root)
    session_service = SessionService(session_repository)
    lifecycle = LocalLifecycleRepository(
        root,
        run_store=run_store,
        session_repository=session_repository,
    )
    run_service = RunService(
        run_store,
        session_service,
        policy_sha256="0" * 64,
        graph_definition_hash="1" * 64,
        lifecycle_repository=lifecycle,
    )
    workspace = ConversationWorkspace(
        session_service,
        run_service,
        RunStoreEventReader(run_store),
        tmp_path / "conversation-locks",
    )
    return ConversationFixture(
        workspace=workspace,
        run_service=run_service,
        run_store=run_store,
        session_service=session_service,
        session_repository=session_repository,
        lifecycle=lifecycle,
        project_id=new_id("project"),
        actor=Actor(kind=ActorKind.HUMAN, id="analyst", authenticated=True),
        root=tmp_path / "conversation-locks",
    )


def _create_session(conversation: ConversationFixture) -> str:
    return conversation.workspace.create(
        project_id=conversation.project_id,
        title="",
        client="web",
    ).id


def _send(
    conversation: ConversationFixture,
    session_id: str,
    message: str,
    request_id: str,
    *,
    actor: Actor | None = None,
) -> tuple[Run, bool]:
    return conversation.workspace.send(
        session_id,
        message,
        request_id,
        actor=actor or conversation.actor,
        workspace=conversation.root,
        project_id=conversation.project_id,
    )


def _response_summary(
    conversation: ConversationFixture,
    run_id: str,
    summary: str,
) -> None:
    conversation.run_store.append(
        run_id,
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {"kind": "conversation.response", "content": summary},
        expected_version=conversation.run_store.version(run_id),
        subject_id=run_id,
        producer="thymira.core",
        producer_version="0.1",
    )


def _fail_turn(conversation: ConversationFixture, run_id: str) -> None:
    conversation.run_service.fail(run_id, actor=Actor.system(), error="scripted test failure")


def test_send_scrubs_secrets_persists_turn_and_retries_the_same_run(
    conversation: ConversationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THYMIRA_API_SECRET", "source-secret-value")
    session_id = _create_session(conversation)

    run, created = _send(conversation, session_id, "Use source-secret-value", "message-1")
    retry, retried = _send(conversation, session_id, "Use source-secret-value", "message-1")

    assert created is True
    assert retried is False
    assert retry.id == run.id
    assert run.conversation is not None
    assert run.conversation.message == "Use [REDACTED:CREDENTIAL]"
    assert "source-secret-value" not in run.prompt
    assert run.conversation.ordinal == 1
    session = conversation.session_service.get_session(session_id)
    assert session.title == "Use [REDACTED:CREDENTIAL]"
    assert session.run_ids == (run.id,)


def test_failed_turn_keeps_status_but_withholds_provisional_response_from_context(
    conversation: ConversationFixture,
) -> None:
    session_id = _create_session(conversation)
    first, _ = _send(conversation, session_id, "Summarize the sample", "message-1")
    _response_summary(conversation, first.id, "The sample has three columns.")
    _fail_turn(conversation, first.id)

    detail = conversation.workspace.detail(session_id, project_id=conversation.project_id)
    second, _ = _send(conversation, session_id, "Which column is numeric?", "message-2")

    assert detail.turns[0].assistant_message is None
    assert detail.turns[0].state.condition is RunCondition.TERMINAL
    assert detail.turns[0].state.outcome is not None
    assert detail.turns[0].state.outcome.value == "failed"
    assert detail.active_run_id is None
    assert second.conversation is not None
    assert second.conversation.context_run_ids == (first.id,)
    assert "The sample has three columns." not in second.prompt
    assert "No approved assistant response is available." in second.prompt
    assert "Current user request:\nWhich column is numeric?" in second.prompt
    assert "outcome: failed" in second.prompt
    assert verify_events(conversation.run_store.events(first.id)).valid


def test_followup_prompt_names_failed_run_and_separate_completed_correction(
    conversation: ConversationFixture,
) -> None:
    session_id = _create_session(conversation)
    original, _ = _send(conversation, session_id, "Original plot request", "message-1")
    _response_summary(conversation, original.id, "PRIVATE unapproved original result")
    _fail_turn(conversation, original.id)

    correction, _ = _send(conversation, session_id, "Correct the original plot", "message-2")
    _response_summary(conversation, correction.id, "PRIVATE unaudited correction result")
    conversation.run_service.advance(correction.id, RunStatus.PLANNING)
    conversation.run_service.advance(correction.id, RunStatus.RUNNING)
    conversation.run_service.advance(correction.id, RunStatus.AUDITING)
    conversation.run_service.complete(correction.id, actor=Actor.system())

    followup, _ = _send(conversation, session_id, "Compare the two plot attempts", "message-3")

    assert followup.conversation is not None
    assert followup.conversation.context_run_ids == (original.id, correction.id)
    assert original.id in followup.prompt
    assert correction.id in followup.prompt
    assert "outcome: failed" in followup.prompt
    assert "outcome: completed" in followup.prompt
    assert "PRIVATE unapproved original result" not in followup.prompt
    assert "PRIVATE unaudited correction result" not in followup.prompt


def test_send_rejects_a_new_turn_while_the_previous_run_is_active(
    conversation: ConversationFixture,
) -> None:
    session_id = _create_session(conversation)
    _send(conversation, session_id, "First request", "message-1")

    with pytest.raises(ConversationBusyError, match="previous conversation Run"):
        _send(conversation, session_id, "Second request", "message-2")


@pytest.mark.parametrize(
    ("message", "actor"),
    [
        ("Changed message", None),
        ("Original message", Actor(kind=ActorKind.HUMAN, id="another-analyst", authenticated=True)),
    ],
)
def test_request_id_reuse_with_changed_body_or_actor_conflicts(
    conversation: ConversationFixture,
    message: str,
    actor: Actor | None,
) -> None:
    session_id = _create_session(conversation)
    _send(conversation, session_id, "Original message", "message-1")

    with pytest.raises(ConversationConflictError, match="already bound"):
        _send(conversation, session_id, message, "message-1", actor=actor)


def test_detail_refuses_an_unverified_event_chain(conversation: ConversationFixture) -> None:
    session_id = _create_session(conversation)
    _run, _ = _send(conversation, session_id, "A request", "message-1")

    class CorruptEventReader(RunStoreEventReader):
        def read(self, run_id: str) -> list[Event]:
            events = conversation.run_store.events(run_id)
            return [events[0].model_copy(update={"payload": {"tampered": True}}), *events[1:]]

    workspace = ConversationWorkspace(
        conversation.session_service,
        conversation.run_service,
        CorruptEventReader(conversation.run_store),
        conversation.root,
    )

    with pytest.raises(ConversationHistoryError, match="failed verification"):
        workspace.detail(session_id, project_id=conversation.project_id)


def test_project_scope_and_cross_process_lock_are_enforced(
    conversation: ConversationFixture,
) -> None:
    session_id = _create_session(conversation)
    with pytest.raises(SessionProjectMismatchError):
        conversation.workspace.detail(session_id, project_id=new_id("project"))

    with (
        conversation_lock(conversation.root, session_id),
        pytest.raises(ConversationBusyError, match="being created"),
    ):
        _send(conversation, session_id, "Blocked request", "message-1")


def test_retry_recovers_a_publication_interrupted_after_session_link(
    conversation: ConversationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = _create_session(conversation)
    other_session = _create_session(conversation)
    original_link = conversation.lifecycle._link_session

    def link_then_fail(prepared: PreparedPublication) -> Session:
        original_link(prepared)
        raise RuntimeError("simulated interruption after Session link")

    monkeypatch.setattr(conversation.lifecycle, "_link_session", link_then_fail)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        _send(conversation, session_id, "Recover this request", "message-1")

    linked_session = conversation.session_service.get_session(session_id)
    assert len(linked_session.run_ids) == 1
    run_id = linked_session.run_ids[0]
    with pytest.raises(RunNotFoundError):
        conversation.run_service.get_run(run_id)

    assert conversation.run_service.recover_session_publications(other_session) == ()
    with pytest.raises(RunNotFoundError):
        conversation.run_service.get_run(run_id)

    monkeypatch.setattr(conversation.lifecycle, "_link_session", original_link)
    recovered, created = _send(conversation, session_id, "Recover this request", "message-1")

    assert recovered.id == run_id
    assert created is False
    assert conversation.run_service.get_run(run_id).conversation is not None
