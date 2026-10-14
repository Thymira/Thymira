"""Render bounded source quotations from exact registered revisions, without executing code."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from thymira.core.model_continuity import _approved_trace, read_artifact, read_manifest
from thymira.events import scrub_credentials, verify_events
from thymira.schemas import ArtifactKind, EventType

if TYPE_CHECKING:
    from pathlib import Path

    from thymira.schemas import SessionConversation
    from thymira.schemas.session_chat import SessionChatProposal, SessionCitation
    from thymira.state import EventStore

_MAX_SOURCE_BYTES = 64 * 1024
_MAX_LINES = 48
_MAX_QUOTE_CHARS = 4400


def source_excerpt_reply(
    proposal: SessionChatProposal,
    detail: SessionConversation,
    citations: dict[str, SessionCitation],
    events: EventStore,
    root: Path,
) -> SessionChatProposal:
    """Replace model prose with verified source lines; an excerpt can never authorize work."""
    request = proposal.source_excerpt
    if request is None:
        return proposal
    if proposal.kind != "answer" or request.source not in citations:
        raise ValueError("source quotation must reference supplied read-only evidence")
    citation = citations[request.source]
    turn = next((turn for turn in detail.turns if turn.run.id == citation.run_id), None)
    if turn is None or not turn.assistant_message:
        raise ValueError("source quotation requires a completed approved result")
    if turn.run.session_id != detail.session.id or turn.run.project_id != detail.session.project_id:
        raise ValueError("source quotation is outside the Session scope")
    trace = tuple(events.read(turn.run.id))
    if not verify_events(trace).valid or any(event.run_id != turn.run.id for event in trace):
        raise ValueError("source quotation event chain is invalid")
    approved = _approved_trace(turn.run, trace)
    event = next((event for event in approved if event.seq == citation.seq), None)
    if event is None or event.type is not EventType.ARTIFACT_CREATED:
        raise ValueError("source quotation requires a registered artifact event")
    artifact = read_manifest(root, turn.run.id).get(event.payload.get("name", ""))
    if (
        artifact is None
        or artifact.kind is not ArtifactKind.CODE
        or artifact.run_id != turn.run.id
        or artifact.id != event.payload.get("artifact_id")
        or artifact.sha256 != event.payload.get("sha256")
        or artifact.size_bytes > _MAX_SOURCE_BYTES
        or not artifact.name
        or not artifact.name.endswith((".py", ".r", ".R", ".sql", ".js"))
    ):
        raise ValueError("source quotation revision is unavailable or unsupported")
    text = read_artifact(root, artifact, max_bytes=_MAX_SOURCE_BYTES).decode("utf-8")
    if scrub_credentials(text) != text:
        message = "This source cannot be quoted exactly because credential redaction is required."
    else:
        message = _numbered_excerpt(artifact.name, artifact.sha256, text, request.contains)
    return proposal.model_copy(
        update={
            "message": message,
            "sources": (request.source,),
            "clarification_scope": "evidence",
            "continues_clarification": False,
            "replaces_clarification": False,
        }
    )


def _numbered_excerpt(name: str, digest: str, text: str, contains: str) -> str:
    lines = text.splitlines()
    matches = [index for index, line in enumerate(lines) if contains.casefold() in line.casefold()]
    heading = f"{name}\nSHA-256: `{digest}`"
    if contains and not matches:
        return f"{heading}\n\nNo matching source text was found in this registered revision."
    start = max(0, matches[0] - 4) if contains else 0
    selected: list[str] = []
    for index in range(start, min(start + _MAX_LINES, len(lines))):
        line = f"{index + 1} | {lines[index]}"
        if sum(map(len, selected)) + len(selected) + len(line) > _MAX_QUOTE_CHARS:
            break
        selected.append(line)
    if not selected:
        return f"{heading}\n\nSource text is empty or exceeds the quotation limit."
    quoted = "\n".join(selected)
    fence = "`" * max(3, 1 + max((len(value) for value in re.findall(r"`+", quoted)), default=0))
    return (
        f"{heading}\nL{start + 1}-L{start + len(selected)} / {len(lines)}\n\n"
        f"{fence}text\n{quoted}\n{fence}"
    )
