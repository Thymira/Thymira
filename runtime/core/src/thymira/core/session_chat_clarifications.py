"""Bounded inputs for interpreting and publishing a clarified human work request."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from thymira.core.project_inputs import (
    CONFIG_FILE,
    THYMIRA_DIRECTORY,
    ProjectInputError,
    ProjectInputs,
)
from thymira.core.session_chat_context import MAX_DISCUSSION_CHARS, MAX_DISCUSSION_TURNS
from thymira.events import canonical_json, scrub_credentials
from thymira.schemas.session_chat import SessionChatProposal

if TYPE_CHECKING:
    from pathlib import Path

    from thymira.schemas import SessionConversation
    from thymira.schemas.session_chat import StoredDiscussion
    from thymira.state.session_discussions import LocalDiscussionRepository

MAX_DATASET_INVENTORY_CHARS = 1800
_BARE_CONFIRMATION_RE = re.compile(
    r"^(?:yes|yeah|yep|yup|ok|okay|sure|exactly|correct|right|si|sí|claro|exacto|"
    r"de acuerdo|vale|por supuesto|confirmo)"
    r"(?:\s+(?:please|por favor|te lo confirmo(?: otra vez)?))?$",
    re.IGNORECASE,
)


class ClarificationContextError(ValueError):
    """A complete, attributable clarification chain is unavailable for work publication."""


class ClarificationReplacementError(ClarificationContextError):
    """A model cannot authorize discarding an outstanding human work request."""


def _prior_records(
    current: StoredDiscussion, repository: LocalDiscussionRepository
) -> list[StoredDiscussion]:
    """Ignore only failed same-actor Auto interpretations, never conversation boundaries."""
    return [
        item
        for item in repository.records()
        if item.discussion.ordinal < current.discussion.ordinal
        and not (
            item.actor == current.actor
            and item.project_id == current.project_id
            and item.discussion.mode == "auto"
            and item.discussion.status == "failed"
            and not item.action_selected
            and item.discussion.run_id is None
        )
    ]


def normalize_initial_continuation(
    proposal: SessionChatProposal,
    current: StoredDiscussion,
    detail: SessionConversation,
    repository: LocalDiscussionRepository,
) -> SessionChatProposal:
    """Ignore an impossible parent claim only when no prior conversation was established.

    A failed interpretation has no usable reply or work to continue. Existing completed/pending
    discussions and Run history still require the full parent checks, even if omitted from the
    model's bounded context. The proposed reply and work intent themselves remain unchanged.
    """
    if not proposal.continues_clarification or detail.turns:
        return proposal
    prior = [
        item
        for item in repository.records()
        if item.discussion.ordinal < current.discussion.ordinal
    ]
    if any(
        item.discussion.status != "failed"
        or item.action_selected
        or item.discussion.run_id is not None
        for item in prior
    ):
        return proposal
    return proposal.model_copy(update={"continues_clarification": False})


def link_pending_work_reply(
    proposal: SessionChatProposal,
    current: StoredDiscussion,
    detail: SessionConversation,
    repository: LocalDiscussionRepository,
    datasets: str,
) -> tuple[SessionChatProposal, tuple[tuple[str, ...], str] | None]:
    """Bind replies to an unresolved request unless the user explicitly replaces it.

    A missing/false continuation flag must not silently discard an active request. Binding
    remains limited to a complete same-actor chain; assistant suggestions never enter
    the published request. Replacement requires the explicit human Request work mode.
    """
    if proposal.kind != "action" and proposal.clarification_scope == "evidence":
        return proposal.model_copy(
            update={"continues_clarification": False, "replaces_clarification": False}
        ), None
    if proposal.replaces_clarification and (
        proposal.continues_clarification or proposal.kind == "answer"
    ):
        raise ClarificationContextError("conflicting clarification relation")
    if (
        proposal.continues_clarification
        or proposal.kind == "answer"
        or current.discussion.mode != "auto"
    ):
        return proposal, None

    prior = _prior_records(current, repository)
    previous = prior[-1] if prior else None
    if previous is None or not _pending_work_question(previous, current):
        if is_bare_confirmation(current.discussion.user_message):
            spanish = bool(
                re.search(
                    r"\b(?:sí|si|claro|exacto|vale|confirmo|acuerdo)\b",
                    current.discussion.user_message,
                    re.IGNORECASE,
                )
            )
            return SessionChatProposal(
                kind="clarification",
                message=(
                    "Esta confirmación no está vinculada a una petición pendiente. Indica la tarea "
                    "completa que quieres realizar."
                    if spanish
                    else "This confirmation is not linked to pending work. Please state the full "
                    "task you want performed."
                ),
                clarification_scope="evidence",
            ), None
        if _is_declared_dataset_reply(current.discussion.user_message, datasets):
            raise ClarificationContextError(
                "no unresolved work clarification belongs to this actor"
            )
        return proposal, None

    if proposal.replaces_clarification:
        raise ClarificationReplacementError("replacement requires explicit Request work mode")
    context, parent_id = clarification_context(current, detail, repository)
    return (
        proposal.model_copy(update={"continues_clarification": True}),
        (context, parent_id),
    )


def _pending_work_question(previous: StoredDiscussion, current: StoredDiscussion) -> bool:
    """Identify the adjacent intake without interpreting the language of the reply."""
    turn = previous.discussion
    return (
        previous.actor == current.actor
        and previous.project_id == current.project_id
        and previous.clarification_scope == "work"
        and turn.mode == "auto"
        and turn.kind == "clarification"
        and turn.status == "completed"
        and bool(turn.assistant_message and turn.assistant_message.strip())
        and turn.run_id is None
        and not previous.action_selected
    )


def is_bare_confirmation(message: str) -> bool:
    """Recognize short confirmations that need an attributable pending request."""
    normalized = re.sub(r"[^\w\s]", " ", message.casefold())
    normalized = " ".join(normalized.split())
    return bool(_BARE_CONFIRMATION_RE.fullmatch(normalized))


def _is_declared_dataset_reply(message: str, inventory: str) -> bool:
    """Match only an exact declared dataset name from the bounded project inventory."""
    reply = message.strip()
    if not reply:
        return False
    return reply in declared_dataset_names(inventory)


def declared_dataset_names(inventory: str) -> tuple[str, ...]:
    """Return exact names from the already bounded inventory frame."""
    names = []
    for line in inventory.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return tuple(names)


def dataset_inventory(workspace: Path) -> str:
    """Describe bounded dataset names, availability and configured targets without reading data."""
    project = workspace.parent if workspace.name == THYMIRA_DIRECTORY else workspace
    if not (project / THYMIRA_DIRECTORY / CONFIG_FILE).is_file():
        return "No declared dataset inventory is available."
    try:
        datasets = ProjectInputs(project).datasets()
    except (OSError, UnicodeError, ProjectInputError):
        return "Dataset inventory is unavailable; do not infer names or availability."
    lines = []
    for dataset in datasets:
        line = scrub_credentials(
            canonical_json(
                {
                    "name": dataset.name,
                    "present": dataset.present,
                    "target": dataset.target,
                }
            )
        )
        if sum(map(len, lines)) + len(line) + len(lines) > MAX_DATASET_INVENTORY_CHARS:
            lines.append("Additional dataset declarations omitted by the context limit.")
            break
        lines.append(line)
    return "\n".join(lines) or "No datasets are declared in this project."


def clarification_context(
    current: StoredDiscussion,
    detail: SessionConversation,
    repository: LocalDiscussionRepository,
) -> tuple[tuple[str, ...], str]:
    """Freeze complete same-actor user messages from the unresolved clarification chain.

    Neither assistant suggestions nor unrelated questions or previous actions authorize work.
    Oversized chains fail instead of dropping an original request or a later restriction.
    """
    messages: list[str] = []
    last_run = max((turn.run.created_at for turn in detail.turns), default=None)
    records = _prior_records(current, repository)
    if not records:
        raise ClarificationContextError("no unresolved work clarification belongs to this actor")
    parent_id = records[-1].discussion.id
    while records:
        record = records.pop()
        turn = record.discussion
        if "clarification_parent_id" not in record.model_fields_set:
            raise ClarificationContextError("legacy clarification has no explicit parent binding")
        if (
            record.actor != current.actor
            or record.clarification_scope != "work"
            or record.project_id != current.project_id
            or turn.mode != "auto"
            or turn.kind != "clarification"
            or turn.status != "completed"
            or (last_run is not None and turn.created_at <= last_run)
        ):
            raise ClarificationContextError(
                "no unresolved work clarification belongs to this actor"
            )
        messages.append(turn.user_message)
        if (
            len(messages) > MAX_DISCUSSION_TURNS
            or sum(map(len, messages)) + 2 * (len(messages) - 1) > MAX_DISCUSSION_CHARS
        ):
            raise ClarificationContextError("clarification chain exceeds the work context limit")
        if record.clarification_parent_id is None:
            break
        if not records or records[-1].discussion.id != record.clarification_parent_id:
            raise ClarificationContextError("clarification parent is not the preceding discussion")
    return tuple(scrub_credentials(message) for message in reversed(messages)), parent_id


def interpretation_clarification_context(
    current: StoredDiscussion,
    detail: SessionConversation,
    repository: LocalDiscussionRepository,
) -> tuple[str, ...]:
    """Supply a complete pending human work chain to interpretation, without truncation."""
    if current.clarification_context:
        return current.clarification_context
    if current.discussion.mode != "auto":
        return ()
    previous = next(
        (
            item
            for item in reversed(repository.records())
            if item.discussion.ordinal < current.discussion.ordinal
        ),
        None,
    )
    if (
        previous is None
        or previous.actor != current.actor
        or previous.project_id != current.project_id
        or previous.clarification_scope != "work"
        or previous.discussion.mode != "auto"
        or previous.discussion.kind != "clarification"
        or previous.discussion.status != "completed"
    ):
        return ()
    context, _ = clarification_context(current, detail, repository)
    return context
