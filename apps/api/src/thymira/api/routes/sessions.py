"""Persistent conversations over the existing Run execution and authorization boundaries."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import Field

from thymira.api.deps import RuntimeDeps, get_runtime_deps
from thymira.api.errors import problem
from thymira.api.permissions import Permission
from thymira.api.principal import require_permission, resolve_actor
from thymira.api.routes._scope import _configured_project
from thymira.api.routes.runs import _submit_run
from thymira.api.routes.session_dispatch import submit_session_run
from thymira.core import (
    ConversationBusyError,
    ConversationConflictError,
    SessionNotFoundError,
    SessionProjectMismatchError,
)
from thymira.core.conversation_inputs import (
    SessionInputsMissingError,
    SessionInputsUnavailableError,
)
from thymira.schemas import (
    ConversationTurn,
    Id,
    Session,
    SessionConversation,
    ThymiraModel,
    id_kind,
)
from thymira.schemas.session_chat import SessionChatRequest, SessionDiscussion
from thymira.state import OwnerBusyError

router = APIRouter(prefix="/sessions", tags=["sessions"])
_DEPS = Depends(get_runtime_deps)
_READ = Depends(require_permission(Permission.READ))
_WRITE = Depends(require_permission(Permission.WRITE))


class CreateSessionRequest(ThymiraModel):
    """Create an empty conversation in the configured project."""

    title: str = Field(default="", max_length=200)
    client: str = Field(default="web", min_length=1, max_length=64)


class SendMessageRequest(ThymiraModel):
    """One user turn with a stable retry identity, independent of the HTTP connection."""

    message: str = Field(min_length=1, max_length=16000)
    client_message_id: str = Field(min_length=1, max_length=128)


class SessionPage(ThymiraModel):
    """A page of continuing conversations, excluding implicit standalone Run sessions."""

    items: tuple[Session, ...]
    next_cursor: str | None = None


def _session(deps: RuntimeDeps, session_id: Id) -> Session:
    if id_kind(session_id) != "session":
        raise problem(422, "invalid_session_id", "A Session identifier is required.")
    project_id = _configured_project(deps)
    try:
        session = deps.session_service.get_session(session_id)
    except SessionNotFoundError as exc:
        raise problem(404, "session_not_found", "The requested Session was not found.") from exc
    if session.project_id != project_id or not session.conversation:
        raise problem(404, "session_not_found", "The requested Session was not found.")
    return session


@router.post("", response_model=Session, status_code=201, dependencies=[_WRITE])
def create_session(payload: CreateSessionRequest, deps: RuntimeDeps = _DEPS) -> Session:
    """Create a conversation without starting a Run."""
    return deps.conversation_workspace.create(
        project_id=_configured_project(deps), title=payload.title, client=payload.client
    )


@router.get("", response_model=SessionPage, dependencies=[_READ])
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    deps: RuntimeDeps = _DEPS,
) -> SessionPage:
    """List the project's conversations using the existing stable pagination contract."""
    try:
        page = deps.session_service.list_sessions(
            project_id=_configured_project(deps),
            conversation=True,
            limit=limit,
            cursor=cursor,
        )
    except (TypeError, ValueError) as exc:
        raise problem(422, "invalid_cursor", "The session cursor is invalid.") from exc
    return SessionPage(
        items=page.items,
        next_cursor=page.next_cursor,
    )


@router.get("/{session_id}", response_model=SessionConversation, dependencies=[_READ])
def get_session(session_id: Id, deps: RuntimeDeps = _DEPS) -> SessionConversation:
    """Reconstruct messages, responses and current turn states from durable Run evidence."""
    session = _session(deps, session_id)
    return deps.session_chat.detail(session.id, project_id=session.project_id)


@router.post(
    "/{session_id}/chat", response_model=SessionDiscussion, status_code=201, dependencies=[_WRITE]
)
def send_chat(
    session_id: Id,
    payload: SessionChatRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    deps: RuntimeDeps = _DEPS,
) -> SessionDiscussion:
    """Discuss existing evidence or publish genuine work through the existing Run boundary."""
    session = _session(deps, session_id)
    if not payload.message.strip() or not payload.client_message_id.strip():
        raise problem(422, "empty_message", "Message and retry identity must not be blank.")
    if deps.project_resolution is None:
        raise problem(503, "project_not_configured", "The project workspace is unavailable.")
    try:
        discussion, created, dispatch_id = deps.session_chat.send(
            session.id,
            payload,
            actor=resolve_actor(request),
            project_id=session.project_id,
            project_workspace=deps.project_resolution.workspace,
        )
    except (ConversationBusyError, OwnerBusyError) as exc:
        raise problem(
            409,
            "session_busy",
            "Another message or Run is active. You can ask about the session while a Run "
            "is active; new work must wait.",
        ) from exc
    except ConversationConflictError as exc:
        raise problem(
            409, "message_conflict", "This retry identity belongs to another message."
        ) from exc
    except (SessionInputsMissingError, SessionInputsUnavailableError) as exc:
        raise problem(
            409,
            "session_inputs_missing",
            "The requested work needs the declared datasets. "
            "Check Session datasets and retry this message.",
        ) from exc
    if dispatch_id is not None:
        background_tasks.add_task(submit_session_run, deps, dispatch_id)
    if not created:
        response.status_code = 200
    return discussion


@router.post(
    "/{session_id}/chat/{discussion_id}/retry",
    response_model=SessionDiscussion,
    dependencies=[_WRITE],
)
def retry_chat(
    session_id: Id,
    discussion_id: Id,
    request: Request,
    background_tasks: BackgroundTasks,
    deps: RuntimeDeps = _DEPS,
) -> SessionDiscussion:
    """Retry the same durable discussion after navigation or an interrupted connection."""
    session = _session(deps, session_id)
    if deps.project_resolution is None:
        raise problem(503, "project_not_configured", "The project workspace is unavailable.")
    try:
        discussion, _, dispatch_id = deps.session_chat.retry(
            session.id,
            discussion_id,
            actor=resolve_actor(request),
            project_id=session.project_id,
            project_workspace=deps.project_resolution.workspace,
        )
    except KeyError as exc:
        raise problem(404, "discussion_not_found", "The discussion was not found.") from exc
    except (ConversationBusyError, OwnerBusyError) as exc:
        raise problem(
            409, "session_busy", "Another message or Run is active. Try again later."
        ) from exc
    except ConversationConflictError as exc:
        raise problem(409, "message_conflict", "This discussion belongs to another actor.") from exc
    except (SessionInputsMissingError, SessionInputsUnavailableError) as exc:
        raise problem(
            409, "session_inputs_missing", "The requested work needs its declared datasets."
        ) from exc
    if dispatch_id is not None:
        background_tasks.add_task(submit_session_run, deps, dispatch_id)
    return discussion


@router.post(
    "/{session_id}/messages",
    response_model=ConversationTurn,
    status_code=201,
    dependencies=[_WRITE],
)
def send_message(
    session_id: Id,
    payload: SendMessageRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    deps: RuntimeDeps = _DEPS,
) -> ConversationTurn:
    """Persist one ordered turn, rejecting competing sends and replaying an identical retry."""
    session = _session(deps, session_id)
    if not payload.message.strip() or not payload.client_message_id.strip():
        raise problem(422, "empty_message", "Message and retry identity must not be blank.")
    resolution = deps.project_resolution
    if resolution is None:
        raise problem(503, "project_not_configured", "The project workspace is unavailable.")
    try:
        run, created = deps.conversation_workspace.send(
            session.id,
            payload.message,
            payload.client_message_id,
            actor=resolve_actor(request),
            workspace=resolution.workspace,
            project_id=session.project_id,
        )
    except (ConversationBusyError, OwnerBusyError) as exc:
        raise problem(
            409, "session_busy", "Finish or cancel the active turn before sending."
        ) from exc
    except ConversationConflictError as exc:
        raise problem(
            409, "message_conflict", "This retry identity belongs to another message."
        ) from exc
    except (SessionNotFoundError, SessionProjectMismatchError) as exc:
        raise problem(404, "session_not_found", "The requested Session was not found.") from exc
    except SessionInputsMissingError as exc:
        missing = tuple({"name": dataset.name, "path": dataset.path} for dataset in exc.datasets)
        labels = ", ".join(f"{dataset.name} ({dataset.path})" for dataset in exc.datasets)
        raise problem(
            409,
            "session_inputs_missing",
            f"Missing declared project datasets: {labels}. Upload or replace each dataset, or "
            "remove its declaration from .thymira/config.yaml before sending.",
            details={"missing_datasets": missing},
        ) from exc
    except SessionInputsUnavailableError as exc:
        raise problem(
            409,
            "session_inputs_unavailable",
            "Project inputs could not be checked. Repair .thymira/config.yaml and try again.",
        ) from exc
    if created:
        background_tasks.add_task(_submit_run, deps, run.id)
    else:
        response.status_code = 200
    detail = deps.conversation_workspace.detail(session.id, project_id=session.project_id)
    return next(turn for turn in detail.turns if turn.id == run.id)


__all__ = ["router"]
