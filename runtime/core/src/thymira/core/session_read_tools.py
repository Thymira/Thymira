"""Bounded read-only queries over a conversation's project and verified Run evidence."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from thymira.core.conversation_response import approved_conversation_response
from thymira.core.model_continuity import _approved_trace, read_artifact, read_manifest
from thymira.core.project_files import (
    ProjectFileError,
    list_project_files,
    read_project_file_details,
)
from thymira.core.project_files import (
    _visible_name as _project_visible_name,
)
from thymira.events import canonical_json, redact_export, sha256_text, verify_events
from thymira.schemas import Artifact, Event, EventType
from thymira.schemas.session_chat import (
    SessionCitation,
    SessionReadRequest,
)
from thymira.tools.models import ToolExecutionError
from thymira.tools.read_queries import execute_session_read_query

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.policies import Policy
    from thymira.schemas import ConversationTurn, SessionConversation
    from thymira.state import EventStore
    from thymira.state.session_discussions import SessionProviderLog

_MAX_PROJECT_FILES = 1000
_MAX_PROJECT_DIRECTORIES = 1000
_MAX_ARTIFACT_BYTES = 64 * 1024
_MAX_ERROR_MESSAGE_CHARS = 500
_MAX_GLOB_PATH_PARTS = 64
_MAX_READ_PAGE_CHARS = 8000
_MAX_SEARCH_PATTERN_CHARS = 500
_MAX_RUN_EVENTS = 4096
_MAX_SESSION_RUNS = 200
_MAX_SESSION_VIRTUAL_FILES = 4096
_MAX_TRACE_BYTES = 256 * 1024
_MAX_GREP_MATCHES = 500
_MAX_VIRTUAL_FILE_CHARS = 256 * 1024
_RUN_ARTIFACT_PATH_PARTS = 4
_RUN_FILE_PATH_PARTS = 3
_RUN_ROOT_PATH_PARTS = 2
_TRACE_FIELDS: dict[EventType, tuple[str, ...]] = {
    EventType.HUMAN_APPROVAL_REQUESTED: ("decision_id", "policy_decision_id"),
    EventType.HUMAN_APPROVAL: ("approved", "automatic", "decision_id", "policy_decision_id"),
    EventType.ACTIVITY_PROFILE_QUESTIONED: ("field", "question_source"),
    EventType.ACTIVITY_PROFILE_ANSWERED: ("field", "sufficiency"),
    EventType.RISK_ASSESSMENT_RECORDED: (),
    EventType.TOOL_STARTED: ("tool",),
    EventType.TOOL_COMPLETED: ("tool", "status", "result_code"),
    EventType.ARTIFACT_CREATED: ("artifact_id", "name", "kind", "sha256"),
    EventType.ARTIFACT_INVALIDATED: ("artifact_id", "name"),
    EventType.RUN_FAILED: (),
    EventType.RUN_TRANSITIONED: ("command",),
    EventType.POLICY_DECISION: ("decision", "rule_id"),
}
_WINDOWS_DEVICES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


class SessionReadError(ValueError):
    """A requested virtual path or bounded read is unavailable."""


@dataclass(frozen=True, slots=True)
class SessionReadRecord:
    """One complete evidence row and its server-bound citation."""

    value: dict[str, object]
    citation: SessionCitation


@dataclass(frozen=True, slots=True)
class SessionReadOutput:
    """The bounded result rows from one authorized operation or a safe tool error."""

    records: tuple[SessionReadRecord, ...] = ()
    error: str | None = None


def execute_session_read(
    request: SessionReadRequest,
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
    policy: Policy,
    log: SessionProviderLog,
) -> SessionReadOutput:
    """Apply the tools read-query policy boundary, then execute a fixed Session read."""
    request_digest = sha256_text(canonical_json(request.model_dump(mode="json")))
    try:
        records = execute_session_read_query(
            request.operation,
            policy=policy,
            log=log,
            request_sha256=request_digest,
            callback=lambda: _dispatch(
                request,
                detail=detail,
                project_root=project_root,
                runtime_root=runtime_root,
                events=events,
            ),
        )
    except (SessionReadError, ToolExecutionError) as exc:
        return SessionReadOutput(error=_safe_tool_error(exc))
    return SessionReadOutput(records=records)


def _safe_tool_error(error: Exception) -> str:
    """Keep model-visible tool errors bounded, credential-free and free of local paths."""
    if isinstance(error, ToolExecutionError):
        return "The Session read query was denied by project policy."
    if not isinstance(error, SessionReadError):
        return "The requested evidence is unavailable within the read safety boundary."
    message = str(error).strip()
    if not message or len(message) > _MAX_ERROR_MESSAGE_CHARS:
        return "The read operation failed within its safety boundary. Narrow the path or query."
    safe = redact_export(message)
    return safe if isinstance(safe, str) else "The read operation failed safely."


def _dispatch(
    request: SessionReadRequest,
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> tuple[SessionReadRecord, ...]:
    """Dispatch one of the four schema-closed read operations."""
    try:
        if request.operation == "list_files":
            return _list_files(
                request,
                detail=detail,
                project_root=project_root,
                runtime_root=runtime_root,
                events=events,
            )
        if request.operation == "read_file":
            return (
                _read_file(
                    request,
                    detail=detail,
                    project_root=project_root,
                    runtime_root=runtime_root,
                    events=events,
                ),
            )
        if request.operation == "glob":
            return _glob(
                request,
                detail=detail,
                project_root=project_root,
                runtime_root=runtime_root,
                events=events,
            )
        if request.operation == "grep":
            return _grep(
                request,
                detail=detail,
                project_root=project_root,
                runtime_root=runtime_root,
                events=events,
            )
    except SessionReadError:
        raise
    except ProjectFileError as exc:
        raise SessionReadError(str(exc)) from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise SessionReadError(
            "The requested evidence is unavailable or is not bounded text."
        ) from exc
    raise SessionReadError("Unknown Session read operation.")


def _list_files(
    request: SessionReadRequest,
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> tuple[SessionReadRecord, ...]:
    parts = _virtual_parts(request.path)
    entries = _directory_entries(
        parts,
        detail=detail,
        project_root=project_root,
        runtime_root=runtime_root,
        events=events,
    )
    page, next_offset = _page(entries, request.offset, request.limit)
    if not page:
        scope = _virtual_path(parts)
        return (
            SessionReadRecord(
                value={
                    "operation": "list_files",
                    "path": scope,
                    "entry_count": len(entries),
                    "offset": request.offset,
                    "returned_count": 0,
                    "next_offset": None,
                    "truncated": False,
                },
                citation=_citation_for_path(detail, scope, f"Directory: {scope or 'project'}"),
            ),
        )
    return tuple(
        SessionReadRecord(
            value={
                "operation": "list_files",
                "path": entry.path,
                "kind": "directory" if entry.directory else "file",
                "entry_count": len(entries),
                "offset": request.offset,
                "returned_count": len(page),
                "next_offset": next_offset,
                "truncated": next_offset is not None,
            },
            citation=entry.citation,
        )
        for entry in page
    )


def _read_file(
    request: SessionReadRequest,
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> SessionReadRecord:
    parts = _virtual_parts(request.path)
    if not parts:
        raise SessionReadError("A file path is required.")
    content, source_truncated, origin_digest, citation = _read_virtual_file(
        parts,
        detail=detail,
        project_root=project_root,
        runtime_root=runtime_root,
        events=events,
    )
    if len(content.encode("utf-8")) > _MAX_VIRTUAL_FILE_CHARS:
        raise SessionReadError("The requested virtual file exceeds its read budget.")
    lines = content.splitlines()
    if not lines and content == "":
        lines = []
    if request.offset > len(lines):
        raise SessionReadError("The requested line offset is beyond the available preview.")
    selected: list[str] = []
    used_chars = 0
    for line in lines[request.offset : request.offset + request.limit]:
        if len(line) > _MAX_READ_PAGE_CHARS:
            raise SessionReadError(
                "A source line exceeds the bounded read size; no text was returned."
            )
        if used_chars + len(line) + (1 if selected else 0) > _MAX_READ_PAGE_CHARS:
            break
        selected.append(line)
        used_chars += len(line) + (1 if selected else 0)
    end_offset = request.offset + len(selected)
    next_offset = end_offset if end_offset < len(lines) else None
    if not selected and request.offset < len(lines):
        raise SessionReadError("The next source line exceeds the bounded read size.")
    return SessionReadRecord(
        value={
            "operation": "read_file",
            "path": _virtual_path(parts),
            "line_range": ({"start": request.offset + 1, "end": end_offset} if selected else None),
            "offset": request.offset,
            "next_offset": next_offset,
            "total_lines": None if source_truncated else len(lines),
            "available_lines": len(lines),
            "truncated": source_truncated or next_offset is not None,
            "source_truncated": source_truncated,
            "origin_sha256": origin_digest,
            "content": "\n".join(selected),
        },
        citation=citation,
    )


def _glob(
    request: SessionReadRequest,
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> tuple[SessionReadRecord, ...]:
    if request.pattern is None:
        raise SessionReadError("A literal filename glob is required.")
    parts = _virtual_parts(request.path)
    files = _files_under(
        parts,
        detail=detail,
        project_root=project_root,
        runtime_root=runtime_root,
        events=events,
    )
    scope = _virtual_path(parts)
    matches = tuple(
        path for path in files if _glob_matches(request.pattern, _relative_to_scope(path, scope))
    )
    page, next_offset = _page(matches, request.offset, request.limit)
    if not page:
        scope = _virtual_path(parts)
        return (
            SessionReadRecord(
                value={
                    "operation": "glob",
                    "path": scope,
                    "pattern": request.pattern,
                    "total_count": len(matches),
                    "offset": request.offset,
                    "returned_count": 0,
                    "next_offset": None,
                    "truncated": False,
                    "scan_complete": True,
                },
                citation=_citation_for_path(detail, scope, f"Directory: {scope or 'project'}"),
            ),
        )
    return tuple(
        SessionReadRecord(
            value={
                "operation": "glob",
                "path": path,
                "pattern": request.pattern,
                "total_count": len(matches),
                "offset": request.offset,
                "returned_count": len(page),
                "next_offset": next_offset,
                "truncated": next_offset is not None,
                "scan_complete": True,
            },
            citation=_citation_for_path(detail, path, "Glob match"),
        )
        for path in page
    )


def _grep(
    request: SessionReadRequest,
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> tuple[SessionReadRecord, ...]:
    if request.pattern is None:
        raise SessionReadError("A literal search string is required.")
    if len(request.pattern) > _MAX_SEARCH_PATTERN_CHARS:
        raise SessionReadError("The literal search string exceeds its bound.")
    parts = _virtual_parts(request.path)
    files = _files_under(
        parts,
        detail=detail,
        project_root=project_root,
        runtime_root=runtime_root,
        events=events,
    )
    matches: list[tuple[str, int, str, bool, SessionCitation]] = []
    for path in files:
        content, truncated, _, citation = _read_virtual_file(
            _virtual_parts(path),
            detail=detail,
            project_root=project_root,
            runtime_root=runtime_root,
            events=events,
        )
        if truncated:
            raise SessionReadError(
                "Search stopped because a source exceeded the bounded reader; narrow the path."
            )
        for line_number, line in enumerate(content.splitlines(), start=1):
            match_at = line.find(request.pattern)
            if match_at < 0:
                continue
            start = max(0, match_at - 160)
            end = min(len(line), match_at + len(request.pattern) + 160)
            matches.append(
                (path, line_number, line[start:end], start > 0 or end < len(line), citation)
            )
            if len(matches) > _MAX_GREP_MATCHES:
                raise SessionReadError(
                    "Search found more than the bounded result limit; narrow the path or string."
                )
    page, next_offset = _page(matches, request.offset, request.limit)
    if not page:
        scope = _virtual_path(parts)
        return (
            SessionReadRecord(
                value={
                    "operation": "grep",
                    "path": scope,
                    "pattern": request.pattern,
                    "total_count": len(matches),
                    "offset": request.offset,
                    "returned_count": 0,
                    "next_offset": None,
                    "truncated": False,
                    "scan_complete": True,
                },
                citation=_citation_for_path(detail, scope, f"Directory: {scope or 'project'}"),
            ),
        )
    return tuple(
        SessionReadRecord(
            value={
                "operation": "grep",
                "path": path,
                "line_range": {"start": line_number, "end": line_number},
                "pattern": request.pattern,
                "match_excerpt": excerpt,
                "excerpt_truncated": excerpt_truncated,
                "total_count": len(matches),
                "offset": request.offset,
                "returned_count": len(page),
                "next_offset": next_offset,
                "truncated": next_offset is not None,
                "scan_complete": True,
            },
            citation=citation,
        )
        for path, line_number, excerpt, excerpt_truncated, citation in page
    )


def _page[T](values: Sequence[T], offset: int, limit: int) -> tuple[tuple[T, ...], int | None]:
    """Return a deterministic page and an explicit next offset."""
    page = tuple(values[offset : offset + limit])
    next_offset = offset + len(page) if offset + len(page) < len(values) else None
    return page, next_offset


def _virtual_parts(value: str) -> tuple[str, ...]:
    """Parse a virtual path without accepting traversal or Windows path aliases."""
    if value in {"", "."}:
        return ()
    if (
        "\\" in value
        or ":" in value
        or "\0" in value
        or value.startswith("/")
        or PurePosixPath(value).is_absolute()
    ):
        raise SessionReadError("Use a relative virtual path with forward slashes.")
    parts = tuple(value.split("/"))
    if any(
        not part or part in {".", ".."} or part.rstrip(". ") != part or _windows_device(part)
        for part in parts
    ):
        raise SessionReadError("The requested virtual path is unavailable.")
    return parts


def _windows_device(part: str) -> bool:
    """Recognize Win32 device names even when an extension follows them."""
    return part.split(".", 1)[0].upper() in _WINDOWS_DEVICES


def _visible_name(name: str) -> bool:
    """Reuse project visibility rules and add Windows device-name protection."""
    return _project_visible_name(name) and not _windows_device(name)


def _virtual_path(parts: Sequence[str]) -> str:
    """Render a validated virtual path."""
    return "/".join(parts)


def _relative_to_scope(path: str, scope: str) -> str:
    """Return a virtual path relative to the explicit listing/search scope."""
    if not scope:
        return path
    if path == scope:
        return path.rsplit("/", 1)[-1]
    if _path_is_under(path, scope):
        return path[len(scope) + 1 :]
    raise SessionReadError("A search result was outside the requested scope.")


def _citation_for_path(
    detail: SessionConversation, path: str, label: str, *, seq: int | None = None
) -> SessionCitation:
    """Bind a citation to the exact project path or Session-owned Run represented by it."""
    parts = _virtual_parts(path)
    run_id = parts[1] if len(parts) > 1 and parts[0] == "runs" else None
    if run_id is not None and not any(turn.run.id == run_id for turn in _session_turns(detail)):
        raise SessionReadError("The requested Run is not part of this Session.")
    return SessionCitation(run_id=run_id, seq=seq, path=path, label=label[:200])


def _session_turns(detail: SessionConversation) -> tuple[ConversationTurn, ...]:
    """Return only real Runs whose Session and project identities match this conversation."""
    session = detail.session
    turns = tuple(
        turn
        for turn in detail.turns
        if turn.run.session_id == session.id and turn.run.project_id == session.project_id
    )
    if len(turns) > _MAX_SESSION_RUNS:
        raise SessionReadError("The Session Run count exceeds the read bound.")
    if len(turns) != len(detail.turns):
        raise SessionReadError("The Session contains a Run outside its project scope.")
    return turns


@dataclass(frozen=True, slots=True)
class _VirtualEntry:
    """One child in the Session's virtual tree."""

    path: str
    name: str
    directory: bool
    citation: SessionCitation


def _directory_entries(
    parts: tuple[str, ...],
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> tuple[_VirtualEntry, ...]:
    if not parts:
        return (
            _VirtualEntry(
                "project", "project", True, _citation_for_path(detail, "project", "Project")
            ),
            _VirtualEntry("runs", "runs", True, _citation_for_path(detail, "runs", "Session Runs")),
        )
    if parts[0] == "project":
        relative = _canonical_project_relative(project_root, parts[1:])
        entries, truncated = list_project_files(project_root, relative)
        if parts[1:] and (project_root / relative).is_file():
            raise SessionReadError("The requested path is a file, not a directory.")
        if truncated:
            raise SessionReadError("The directory exceeds the safe listing bound; narrow the path.")
        return tuple(
            _VirtualEntry(
                f"project/{entry.path}",
                entry.name,
                entry.directory,
                _citation_for_path(
                    detail,
                    f"project/{entry.path}",
                    f"Directory: {entry.name}" if entry.directory else entry.name,
                ),
            )
            for entry in entries
        )
    if parts == ("runs",):
        return tuple(
            _VirtualEntry(
                f"runs/{turn.run.id}",
                turn.run.id,
                True,
                _citation_for_path(detail, f"runs/{turn.run.id}", f"Run {turn.run.id}"),
            )
            for turn in _session_turns(detail)
        )
    if parts[0] == "runs":
        turn = _turn_for_path(detail, parts)
        return _run_directory_entries(
            turn,
            parts,
            detail=detail,
            runtime_root=runtime_root,
            events=events,
        )
    raise SessionReadError("The requested virtual directory is unavailable.")


def _canonical_project_relative(root: Path, parts: Sequence[str]) -> str:
    """Require the exact visible spelling of every existing project path component."""
    current = ""
    for part in parts:
        if not _visible_name(part):
            raise SessionReadError("Hidden and credential paths are not available.")
        entries, truncated = list_project_files(root, current)
        exact = next((entry for entry in entries if entry.name == part), None)
        if exact is None:
            if truncated:
                raise SessionReadError("The directory exceeds the safe path resolution bound.")
            if any(entry.name.casefold() == part.casefold() for entry in entries):
                raise SessionReadError("Windows case aliases are not available.")
            raise SessionReadError("The requested project path is not available.")
        if not exact.directory and part != parts[-1]:
            raise SessionReadError("A project path component is not a directory.")
        current = exact.path
    return current


def _turn_for_path(detail: SessionConversation, parts: tuple[str, ...]) -> ConversationTurn:
    """Resolve only an actual Run in this Session, including turns older than recent context."""
    if len(parts) < _RUN_ROOT_PATH_PARTS or parts[0] != "runs":
        raise SessionReadError("A Run path is incomplete.")
    turn = next((turn for turn in _session_turns(detail) if turn.run.id == parts[1]), None)
    if turn is None:
        raise SessionReadError("The requested Run is not part of this Session.")
    return turn


def _verified_trace(events: EventStore, run_id: str) -> tuple[Event, ...]:
    """Verify the Run chain and ownership before projecting operational trace facts."""
    trace = tuple(events.read(run_id))
    if (
        len(trace) > _MAX_RUN_EVENTS
        or not verify_events(trace).valid
        or any(event.run_id != run_id for event in trace)
    ):
        raise SessionReadError("Run trace failed verification or exceeds its read bound.")
    return trace


def _approved_result(turn: ConversationTurn, trace: tuple[Event, ...]) -> str | None:
    """Return only the response already bound by the exact audited Run completion."""
    return approved_conversation_response(turn.run, turn.state, trace)


def _artifact_records(
    turn: ConversationTurn, trace: tuple[Event, ...], runtime_root: Path
) -> tuple[tuple[str, Artifact, int], ...]:
    """Join registered metadata to its hash-chained creation event without opening a store."""
    manifest = read_manifest(runtime_root, turn.run.id)
    created = {
        event.payload.get("name"): event
        for event in trace
        if event.type is EventType.ARTIFACT_CREATED and isinstance(event.payload.get("name"), str)
    }
    records: list[tuple[str, Artifact, int]] = []
    for name, artifact in sorted(manifest.items()):
        if not _safe_artifact_name(name):
            continue
        event = created.get(name)
        if (
            event is None
            or artifact.run_id != turn.run.id
            or event.payload.get("artifact_id") != artifact.id
            or event.payload.get("sha256") != artifact.sha256
        ):
            continue
        records.append((name, artifact, event.seq))
    return tuple(records)


def _safe_artifact_name(name: str) -> bool:
    """Accept only canonical, visible artifact names for the virtual namespace."""
    if not name or "\\" in name or ":" in name or "\0" in name:
        return False
    parts = name.split("/")
    return all(
        part and part not in {".", ".."} and part.rstrip(". ") == part and _visible_name(part)
        for part in parts
    )


def _run_directory_entries(
    turn: ConversationTurn,
    parts: tuple[str, ...],
    *,
    detail: SessionConversation,
    runtime_root: Path,
    events: EventStore,
) -> tuple[_VirtualEntry, ...]:
    """List generated Run evidence files and registered artifact names."""
    run = turn.run
    trace = _verified_trace(events, run.id)
    root = f"runs/{run.id}"
    artifacts = _artifact_records(turn, trace, runtime_root)
    if len(parts) == _RUN_ROOT_PATH_PARTS:
        result = _approved_result(turn, trace)
        names = ["status.json", "trace.jsonl", "artifacts.json"]
        if result is not None:
            names.append("result.txt")
        entries = [
            _VirtualEntry(
                f"{root}/{name}",
                name,
                False,
                _citation_for_path(detail, f"{root}/{name}", name),
            )
            for name in names
        ]
        entries.append(
            _VirtualEntry(
                f"{root}/artifacts",
                "artifacts",
                True,
                SessionCitation(run_id=run.id, path=f"{root}/artifacts", label="Artifacts"),
            )
        )
        return tuple(entries)
    if parts[2] != "artifacts":
        raise SessionReadError("The requested Run directory is unavailable.")
    artifact_paths = tuple(f"{root}/artifacts/{name}" for name, _, _ in artifacts)
    directory_path = _virtual_path(parts)
    prefix = f"{directory_path}/" if directory_path else ""
    children: dict[str, _VirtualEntry] = {}
    for artifact_path in artifact_paths:
        if not artifact_path.startswith(prefix):
            continue
        remainder = artifact_path[len(prefix) :]
        if not remainder:
            continue
        name, separator, _ = remainder.partition("/")
        child_path = f"{directory_path}/{name}" if directory_path else name
        directory = bool(separator)
        children[name] = _VirtualEntry(
            child_path,
            name,
            directory,
            _citation_for_path(detail, child_path, name),
        )
    return tuple(
        sorted(children.values(), key=lambda entry: (not entry.directory, entry.name.casefold()))
    )


def _files_under(  # noqa: PLR0911, PLR0912, PLR0915  # fixed virtual-tree traversal and bounds
    parts: tuple[str, ...],
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> tuple[str, ...]:
    """Enumerate the complete bounded set of visible file paths under one virtual directory."""
    if not parts:
        project = _files_under(
            ("project",),
            detail=detail,
            project_root=project_root,
            runtime_root=runtime_root,
            events=events,
        )
        runs = _files_under(
            ("runs",),
            detail=detail,
            project_root=project_root,
            runtime_root=runtime_root,
            events=events,
        )
        return (*project, *runs)
    if parts[0] == "project":
        relative = _canonical_project_relative(project_root, parts[1:])
        root_dir = _virtual_path(parts)
        start = project_root if not relative else project_root / relative
        if not start.is_dir():
            if start.is_file():
                return (root_dir,)
            raise SessionReadError("The requested project path is unavailable.")
        pending = [(start, root_dir)]
        files: list[str] = []
        visited_directories = 0
        while pending:
            directory, _ = pending.pop()
            visited_directories += 1
            if visited_directories > _MAX_PROJECT_DIRECTORIES:
                raise SessionReadError("Search stopped at the project directory-count bound.")
            relative_dir = directory.relative_to(project_root).as_posix()
            entries, truncated = list_project_files(
                project_root, "" if relative_dir == "." else relative_dir
            )
            if truncated:
                raise SessionReadError(
                    "Search stopped because a directory exceeds its listing bound."
                )
            for entry in entries:
                child_virtual = f"project/{entry.path}"
                if entry.directory:
                    pending.append((project_root / entry.path, child_virtual))
                else:
                    files.append(child_virtual)
                if len(files) > _MAX_PROJECT_FILES:
                    raise SessionReadError("Search stopped at the project file-count bound.")
        return tuple(sorted(files))
    if parts[0] == "runs":
        turns = _session_turns(detail)
        selected = turns if len(parts) == 1 else (_turn_for_path(detail, parts),)
        files: list[str] = []
        for turn in selected:
            trace = _verified_trace(events, turn.run.id)
            root = f"runs/{turn.run.id}"
            files.extend((f"{root}/status.json", f"{root}/trace.jsonl", f"{root}/artifacts.json"))
            if _approved_result(turn, trace) is not None:
                files.append(f"{root}/result.txt")
            files.extend(
                f"{root}/artifacts/{name}"
                for name, _, _ in _artifact_records(turn, trace, runtime_root)
            )
            if len(files) > _MAX_SESSION_VIRTUAL_FILES:
                raise SessionReadError("Search stopped at the Session Run file-count bound.")
        scope = _virtual_path(parts)
        if len(parts) == 1:
            return tuple(sorted(set(files)))
        if len(parts) == _RUN_ROOT_PATH_PARTS:
            return tuple(sorted(set(files)))
        if parts[2] == "artifacts":
            candidates = tuple(sorted(set(files)))
            if scope in candidates:
                return (scope,)
            descendants = tuple(
                path for path in candidates if path != scope and _path_is_under(path, scope)
            )
            if descendants:
                return descendants
            if len(parts) == _RUN_FILE_PATH_PARTS:
                return ()
            raise SessionReadError("The requested Run artifact scope is unavailable.")
        exact = f"{scope}"
        if exact in files:
            return (exact,)
        raise SessionReadError("The requested Run search path is unavailable.")
    raise SessionReadError("The requested virtual search scope is unavailable.")


def _glob_matches(pattern: str, path: str) -> bool:
    """Match literal filename glob segments without compiling regular expressions."""
    if not pattern or "\\" in pattern or ":" in pattern or "\0" in pattern:
        raise SessionReadError("The filename glob is invalid.")
    pattern_parts = pattern.split("/")
    path_parts = path.split("/")
    if (
        len(pattern_parts) > _MAX_GLOB_PATH_PARTS
        or len(path_parts) > _MAX_GLOB_PATH_PARTS
        or any(not part for part in pattern_parts)
    ):
        raise SessionReadError("The filename glob exceeds its path bound.")
    if "/" not in pattern:
        return fnmatch.fnmatchcase(path_parts[-1], pattern)

    @cache
    def match(pattern_index: int, path_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        if pattern_parts[pattern_index] == "**":
            return match(pattern_index + 1, path_index) or (
                path_index < len(path_parts) and match(pattern_index, path_index + 1)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], pattern_parts[pattern_index])
            and match(pattern_index + 1, path_index + 1)
        )

    return match(0, 0)


def _read_virtual_file(  # noqa: PLR0912, PLR0915  # fixed Run-file mapping with approval gates
    parts: tuple[str, ...],
    *,
    detail: SessionConversation,
    project_root: Path,
    runtime_root: Path,
    events: EventStore,
) -> tuple[str, bool, str | None, SessionCitation]:
    """Read one safe project preview or Session-bound Run virtual file as bounded text."""
    path = _virtual_path(parts)
    if not parts:
        raise SessionReadError("A file path is required.")
    if parts[0] == "project":
        relative = _canonical_project_relative(project_root, parts[1:])
        preview = read_project_file_details(project_root, relative)
        return (
            preview.content,
            preview.truncated,
            preview.sha256,
            _citation_for_path(detail, path, path),
        )
    if parts[0] != "runs" or len(parts) < _RUN_FILE_PATH_PARTS:
        raise SessionReadError("The requested virtual file is unavailable.")
    turn = _turn_for_path(detail, parts)
    run = turn.run
    trace = _verified_trace(events, run.id)
    content: str
    origin_digest: str | None = None
    citation_seq: int | None = None
    if parts[2] == "status.json" and len(parts) == _RUN_FILE_PATH_PARTS:
        approved = _approved_result(turn, trace)
        state = turn.state
        content = canonical_json(
            {
                "run_id": run.id,
                "status": run.status.value,
                "condition": state.condition.value,
                "outcome": state.outcome.value if state.outcome is not None else None,
                "approved_result_available": approved is not None,
            }
        )
    elif parts[2] == "trace.jsonl" and len(parts) == _RUN_FILE_PATH_PARTS:
        content = _safe_trace_text(run.id, trace)
        origin_digest = trace[-1].hash if trace else None
    elif parts[2] == "result.txt" and len(parts) == _RUN_FILE_PATH_PARTS:
        result = _approved_result(turn, trace)
        if result is None:
            raise SessionReadError("No approved terminal Run result is available.")
        content = result
        terminal = next(
            (event for event in reversed(trace) if event.type is EventType.RUN_TRANSITIONED), None
        )
        citation_seq = terminal.seq if terminal is not None else None
        origin_digest = sha256_text(result)
    elif parts[2] == "artifacts.json" and len(parts) == _RUN_FILE_PATH_PARTS:
        records = _artifact_records(turn, trace, runtime_root)
        content = canonical_json(
            [
                {
                    "name": name,
                    "kind": artifact.kind.value,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "valid": artifact.valid,
                    "created_at": artifact.created_at.isoformat(),
                }
                for name, artifact, _ in records
            ]
        )
        origin_digest = sha256_text(content)
    elif parts[2] == "artifacts" and len(parts) >= _RUN_ARTIFACT_PATH_PARTS:
        name = "/".join(parts[3:])
        if not _safe_artifact_name(name):
            raise SessionReadError("The requested registered artifact path is unavailable.")
        try:
            approved_trace = _approved_trace(run, trace)
        except ValueError as exc:
            raise SessionReadError("No approved Run artifact snapshot is available.") from exc
        records = _artifact_records(turn, approved_trace, runtime_root)
        selected = next((item for item in records if item[0] == name), None)
        if selected is None:
            raise SessionReadError("The requested artifact is not registered for this Run.")
        _, artifact, citation_seq = selected
        if not artifact.valid:
            raise SessionReadError("The requested artifact revision is invalidated.")
        if _artifact_was_invalidated(artifact, trace):
            raise SessionReadError(
                "The requested artifact revision was invalidated after approval."
            )
        if _approved_result(turn, trace) is None:
            raise SessionReadError("Artifact contents require an approved terminal Run result.")
        try:
            raw = read_artifact(runtime_root, artifact, max_bytes=_MAX_ARTIFACT_BYTES)
        except (OSError, ValueError) as exc:
            raise SessionReadError(
                "The registered artifact is invalid, changed or exceeds the text read bound."
            ) from exc
        if b"\0" in raw:
            raise SessionReadError("Binary registered artifacts cannot be read as text.")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SessionReadError("The registered artifact is not valid UTF-8 text.") from exc
        safe = redact_export(content)
        if not isinstance(safe, str):
            raise SessionReadError("The registered artifact could not be safely redacted.")
        content = safe
        origin_digest = artifact.sha256
    else:
        raise SessionReadError("The requested virtual file is unavailable.")
    if len(content.encode("utf-8")) > _MAX_VIRTUAL_FILE_CHARS:
        raise SessionReadError("The requested virtual file exceeds its read budget.")
    citation = _citation_for_path(detail, path, path, seq=citation_seq)
    return content, False, origin_digest, citation


def _artifact_was_invalidated(artifact: Artifact, trace: tuple[Event, ...]) -> bool:
    """Match the current authoritative invalidation event to this exact artifact revision."""
    return any(
        event.type is EventType.ARTIFACT_INVALIDATED
        and event.payload.get("artifact_id") == artifact.id
        for event in trace
    )


def _safe_trace_text(run_id: str, trace: tuple[Event, ...]) -> str:
    """Render an allowlisted, redacted operational trace without prompts or file contents."""
    rows: list[str] = []
    for event in trace:
        fields = _TRACE_FIELDS.get(event.type)
        if fields is None:
            continue
        payload = {key: event.payload[key] for key in fields if key in event.payload}
        artifact_name = payload.get("name")
        if isinstance(artifact_name, str) and not _safe_artifact_name(artifact_name):
            payload.pop("name", None)
        projection = redact_export(
            {"run_id": run_id, "seq": event.seq, "type": event.type.value, **payload}
        )
        rows.append(canonical_json(projection))
    text = "\n".join(rows)
    if len(text.encode("utf-8")) > _MAX_TRACE_BYTES:
        raise SessionReadError("The safe operational trace exceeds its read bound.")
    return text


def _path_is_under(path: str, scope: str) -> bool:
    """Return whether one virtual path belongs to a directory scope."""
    return not scope or path == scope or path.startswith(f"{scope}/")
