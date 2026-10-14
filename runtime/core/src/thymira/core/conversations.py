"""Persistent conversation turns backed by existing Session, Run and event state."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from thymira.agents import frame_untrusted
from thymira.core.conversation_diagnostics import conversation_turn
from thymira.core.conversation_inputs import require_session_inputs_ready
from thymira.core.model_continuity import model_inputs_for_turns
from thymira.core.project_inputs import THYMIRA_DIRECTORY, ProjectInputError, ProjectInputs
from thymira.core.sessions import SessionProjectMismatchError
from thymira.events import canonical_json, scrub_credentials, sha256_text, verify_events
from thymira.schemas import (
    Actor,
    ConversationInput,
    ConversationTurn,
    Event,
    EventType,
    Id,
    ModelWorkIntent,
    Run,
    RunCondition,
    Session,
    SessionConversation,
    resolve_model_work_intent,
)
from thymira.state import IdempotencyConflictError, OwnerBusyError, conversation_lock

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.core.runs import RunService
    from thymira.core.sessions import SessionService
    from thymira.state import EventStore

MAX_HISTORY_CHARS = 16_000
MAX_HISTORY_CHARS_WITH_DIALOGUE = 8_000
MAX_HISTORY_TURNS = 12
_HISTORY_EXCERPT_MARKER = "…[earlier content omitted]"


class ConversationBusyError(RuntimeError):
    """Raised when another turn owns the Session or a prior turn is still active."""


class ConversationConflictError(ValueError):
    """Raised when a client request id is reused with a different conversation request."""


class ConversationHistoryError(RuntimeError):
    """Raised when a conversation cannot be reconstructed from verified Run evidence."""


class ConversationWorkspace:
    """Coordinate persistent turns without introducing a second transcript store."""

    def __init__(
        self,
        session_service: SessionService,
        run_service: RunService,
        event_store: EventStore,
        root: Path,
        discussion_history: Callable[[str], str] | None = None,
    ) -> None:
        self._sessions = session_service
        self._runs = run_service
        self._events = event_store
        self._root = Path(root)
        self._discussion_history = discussion_history

    def create(self, *, project_id: Id, title: str, client: str) -> Session:
        """Create a persistent Session that accepts conversation turns."""
        return self._sessions.create_session(
            project_id=project_id,
            title=scrub_credentials(title)[:200],
            client=client,
            conversation=True,
        )

    def detail(self, session_id: Id, *, project_id: Id) -> SessionConversation:
        """Reconstruct a conversation view from its Session, Runs and verified event chains."""
        session = self._get_session(session_id, project_id=project_id)
        turns = _load_turns(session, self._runs, self._verified_events)
        active = next(
            (
                turn.id
                for turn in reversed(turns)
                if turn.state.condition is not RunCondition.TERMINAL
            ),
            None,
        )
        return SessionConversation(session=session, turns=turns, active_run_id=active)

    def send(
        self,
        session_id: Id,
        message: str,
        client_message_id: str,
        *,
        actor: Actor,
        workspace: Path,
        project_id: Id,
        clarification_context: tuple[str, ...] = (),
        work_kind: Literal["general", "evaluate_existing", "train_new"] = "general",
    ) -> tuple[Run, bool]:
        """Publish one sequential turn and return its Run plus whether it was newly created."""
        # Validate source bounds before scrubbing or writing any local state.
        raw = ConversationInput(
            ordinal=1,
            client_message_id=client_message_id,
            message=message,
            clarification_context=clarification_context,
            work_kind=work_kind,
        )
        intent = resolve_model_work_intent(
            raw.message,
            raw.clarification_context,
            dataset_names=_project_dataset_names(Path(workspace)),
        )
        if intent is ModelWorkIntent.CLARIFY:
            raise ConversationConflictError(
                "model work is ambiguous; clarify whether to train a new model or evaluate an "
                "existing one before publishing a Run"
            )
        work_kind = intent.value
        safe_message = scrub_credentials(raw.message)
        safe_message_id = scrub_credentials(raw.client_message_id)
        try:
            with conversation_lock(self._root, session_id):
                session = self._get_session(session_id, project_id=project_id)
                if not session.conversation:
                    raise ConversationConflictError("Session is not a conversation")
                self._runs.recover_session_publications(session.id)
                session = self._get_session(session_id, project_id=project_id)
                return self._send_locked(
                    session,
                    safe_message,
                    safe_message_id,
                    actor=actor,
                    workspace=workspace,
                    project_id=project_id,
                    clarification_context=tuple(map(scrub_credentials, raw.clarification_context)),
                    work_kind=work_kind,
                )
        except OwnerBusyError as exc:
            raise ConversationBusyError("another conversation turn is being created") from exc

    def _send_locked(
        self,
        session: Session,
        message: str,
        client_message_id: str,
        *,
        actor: Actor,
        workspace: Path,
        project_id: Id,
        clarification_context: tuple[str, ...],
        work_kind: Literal["general", "evaluate_existing", "train_new"],
    ) -> tuple[Run, bool]:
        request = ConversationInput(
            ordinal=1,
            message=message,
            client_message_id=client_message_id,
            clarification_context=clarification_context,
            work_kind=work_kind,
        )
        existing = _find_duplicate(
            session, request, actor, self._runs.get_run, self._verified_events
        )
        if existing is not None:
            self._set_initial_title(session, message)
            return existing, False

        all_runs = tuple(self._runs.get_run(run_id) for run_id in session.run_ids)
        for prior in all_runs:
            if prior.session_id != session.id or prior.project_id != session.project_id:
                raise ConversationHistoryError("Session contains a Run from another scope")
            if self._runs.get_run_state(prior.id).condition is not RunCondition.TERMINAL:
                raise ConversationBusyError("a previous conversation Run is still active")

        turns = _load_turns(session, self._runs, self._verified_events)
        require_session_inputs_ready(workspace)
        ordinal = max((turn.ordinal for turn in turns), default=0) + 1
        recent = turns[-MAX_HISTORY_TURNS:]
        dialogue = self._discussion_history(session.id) if self._discussion_history else ""
        history_budget = MAX_HISTORY_CHARS_WITH_DIALOGUE if dialogue else MAX_HISTORY_CHARS
        selected, history = _bounded_history(recent, max_chars=history_budget)
        context_run_ids = tuple(turn.run.id for turn in selected)
        metadata = ConversationInput(
            ordinal=ordinal,
            client_message_id=client_message_id,
            message=message,
            context_run_ids=context_run_ids,
            model_inputs=model_inputs_for_turns(self._root.parent, selected, self._events),
            clarification_context=clarification_context,
            work_kind=work_kind,
        )
        prompt = _conversation_prompt((history + "\n" + dialogue).strip(), metadata.requested_work)
        idempotency_key = _idempotency_key(session, actor, client_message_id)
        try:
            run, _ = self._runs.create_run_published(
                session.id,
                prompt,
                actor=actor,
                workspace=Path(workspace),
                dispatch=False,
                idempotency_key=idempotency_key,
                conversation=metadata,
                project_id=project_id,
                session_client=session.client,
            )
        except IdempotencyConflictError as exc:
            raise ConversationConflictError(
                "client_message_id is already bound to a different request"
            ) from exc

        self._set_initial_title(session, message)
        return run, True

    def _get_session(self, session_id: Id, *, project_id: Id) -> Session:
        """Resolve the Session and enforce project scope before reading its Runs."""
        session = self._sessions.get_session(session_id)
        if session.project_id != project_id:
            raise SessionProjectMismatchError("Session does not belong to the requested project")
        return session

    def title_from_discussion(self, session_id: Id, message: str, *, project_id: Id) -> None:
        """Name a discussion-only session under the same lock used by Run publication."""
        with conversation_lock(self._root, session_id):
            session = self._get_session(session_id, project_id=project_id)
            self._set_initial_title(session, scrub_credentials(message))

    def _verified_events(self, run_id: Id) -> tuple[Event, ...]:
        """Read one verified event chain before text enters conversation context."""
        events = self._events.read(run_id)
        result = verify_events(events)
        if not result.valid:
            raise ConversationHistoryError(f"Run {run_id} event history failed verification")
        return tuple(events)

    def _set_initial_title(self, session: Session, message: str) -> None:
        """Set an empty title after publication using SessionService's latest persisted snapshot."""
        title = message.strip()[:200]
        if not session.title and title:
            self._sessions.set_title_if_empty(session.id, title)


def _load_turns(
    session: Session, runs: RunService, verified_events: Callable[[Id], tuple[Event, ...]]
) -> tuple[ConversationTurn, ...]:
    """Build ordered conversation turns, accepting only explicit response summary events."""
    turns: list[ConversationTurn] = []
    for run_id in session.run_ids:
        run = runs.get_run(run_id)
        if run.session_id != session.id or run.project_id != session.project_id:
            raise ConversationHistoryError("Session contains a Run from another scope")
        metadata = run.conversation
        if metadata is None:
            continue
        events = verified_events(run.id)
        state = runs.get_run_state(run.id)
        turns.append(conversation_turn(run, state, events))
    turns.sort(key=lambda turn: turn.ordinal)
    ordinals = [turn.ordinal for turn in turns]
    if len(ordinals) != len(set(ordinals)):
        raise ConversationHistoryError("Session contains duplicate conversation ordinals")
    return tuple(turns)


def _find_duplicate(
    session: Session,
    request: ConversationInput,
    actor: Actor,
    get_run: Callable[[Id], Run],
    verified_events: Callable[[Id], tuple[Event, ...]],
) -> Run | None:
    """Bind retries to the same scoped human text, clarification and creation actor."""
    for run_id in session.run_ids:
        run = get_run(run_id)
        metadata = run.conversation
        if metadata is None or metadata.client_message_id != request.client_message_id:
            continue
        if (
            run.session_id != session.id
            or run.project_id != session.project_id
            or metadata.message != request.message
            or metadata.clarification_context != request.clarification_context
            or metadata.work_kind != request.work_kind
            or _creation_actor(verified_events(run.id)) != actor
        ):
            raise ConversationConflictError(
                "client_message_id is already bound to a different request"
            )
        return run
    return None


def _creation_actor(events: tuple[Event, ...]) -> Actor:
    """Require a verified creation event before accepting the retry actor."""
    if not events or events[0].type is not EventType.RUN_STARTED:
        raise ConversationHistoryError("Run has no verified creation event")
    return events[0].actor


def _project_dataset_names(workspace: Path) -> tuple[str, ...]:
    """Resolve exact declared dataset names for the shared human-work interpretation."""
    project_dir = workspace.parent if workspace.name == THYMIRA_DIRECTORY else workspace
    try:
        return tuple(dataset.name for dataset in ProjectInputs(project_dir).datasets())
    except (OSError, UnicodeError, ProjectInputError):
        return ()


def _conversation_prompt(history: str, message: str) -> str:
    """Frame prior user and assistant summaries before appending the direct current request."""
    if not history:
        return message
    framed = frame_untrusted(history, label="conversation-history", max_chars=MAX_HISTORY_CHARS)
    return f"Previous conversation context:\n{framed}\n\nCurrent user request:\n{message}"


def _bounded_history(
    turns: tuple[ConversationTurn, ...],
    *,
    max_chars: int = MAX_HISTORY_CHARS,
) -> tuple[tuple[ConversationTurn, ...], str]:
    """Keep recent whole turns and clip one oversized newest turn without losing its header."""
    if not turns:
        return (), ""
    empty_frame = frame_untrusted("", label="conversation-history", max_chars=max_chars)
    remaining = max_chars - len(empty_frame)
    selected: list[ConversationTurn] = []
    rendered: list[str] = []
    for turn in reversed(turns):
        block = _render_turn(turn)
        separator = "\n\n" if rendered else ""
        cost = _escaped_length(separator + block)
        if cost <= remaining:
            selected.append(turn)
            rendered.append(block)
            remaining -= cost
            continue
        if not selected and remaining > 0:
            excerpt = _bounded_turn(turn, remaining)
            selected.append(turn)
            rendered.append(excerpt)
        break
    selected.reverse()
    rendered.reverse()
    return tuple(selected), "\n\n".join(rendered)


def _render_turn(turn: ConversationTurn) -> str:
    """Render user text, an approved summary and the exact Run identity and state."""
    outcome = turn.state.outcome.value if turn.state.outcome is not None else "none"
    summary = turn.assistant_message or "[No approved assistant response is available.]"
    return (
        f"Turn {turn.ordinal}\nRun ID: {turn.run.id}"
        f"\nRun condition: {turn.state.condition.value}; outcome: {outcome}"
        f"\nUser:\n{turn.user_message}\nAssistant summary:\n{summary}"
    )


def _bounded_turn(turn: ConversationTurn, max_chars: int) -> str:
    """Retain exact Run identity and state while bounding user text and approved summary."""
    outcome = turn.state.outcome.value if turn.state.outcome is not None else "none"
    header = (
        f"Turn {turn.ordinal}\nRun ID: {turn.run.id}"
        f"\nRun condition: {turn.state.condition.value}; outcome: {outcome}"
    )
    remaining = max(0, max_chars - _escaped_length(header))
    user = _fit_history_piece(f"\nUser:\n{turn.user_message}", remaining)
    remaining = max(0, remaining - _escaped_length(user))
    summary = turn.assistant_message or "[No approved assistant response is available.]"
    assistant = _fit_history_piece(f"\nAssistant summary:\n{summary}", remaining)
    return header + user + assistant


def _fit_history_piece(value: str, max_chars: int) -> str:
    """Keep a readable prefix of one variable field within the escaped character budget."""
    if max_chars <= 0:
        return ""
    if _escaped_length(value) <= max_chars:
        return value
    marker_cost = _escaped_length(_HISTORY_EXCERPT_MARKER)
    excerpt = _prefix_by_escaped_length(value, max(0, max_chars - marker_cost))
    return excerpt + _HISTORY_EXCERPT_MARKER if excerpt else ""


def _escaped_length(value: str) -> int:
    """Measure text after the angle delimiters are escaped by ``frame_untrusted``."""
    return len(value) + 5 * (value.count("<") + value.count(">"))


def _prefix_by_escaped_length(value: str, max_chars: int) -> str:
    """Keep the first complete code points whose escaped representation fits the budget."""
    if max_chars <= 0:
        return ""
    kept: list[str] = []
    used = 0
    for character in value:
        cost = 6 if character in "<>" else 1
        if used + cost > max_chars:
            break
        kept.append(character)
        used += cost
    return "".join(kept)


def _idempotency_key(session: Session, actor: Actor, client_message_id: str) -> str:
    """Scope one client message id to its project, Session and authenticated actor."""
    binding = canonical_json(
        {
            "project_id": session.project_id,
            "session_id": session.id,
            "actor": actor.to_json_dict(),
            "client_message_id": client_message_id,
        }
    )
    return f"conversation:{sha256_text(binding)}"


__all__ = [
    "ConversationBusyError",
    "ConversationConflictError",
    "ConversationHistoryError",
    "ConversationWorkspace",
]
