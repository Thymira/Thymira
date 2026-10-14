"""Conservative server-owned replies to explicit file-extension location questions."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from thymira.events import scrub_credentials
from thymira.schemas.session_chat import SessionChatProposal, SessionCitation

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from thymira.core.session_read_tools import SessionReadRecord

_EXTENSION = r"(?P<extension>\.?[a-z0-9]{1,12})"
_SPANISH_LOCATION = re.compile(
    rf"^\s*¿?\s*d[oó]nde\s+est[aá]n?\s+(?:(?:el|la|los|las)\s+)?"
    rf"(?:archivos?\s+)?{_EXTENSION}(?:\s+de\s+(?:(?:la|el)\s+)?run)?\s*\??\s*$",
    re.IGNORECASE,
)
_ENGLISH_LOCATION = re.compile(
    rf"^\s*where\s+(?:is|are)\s+(?:the\s+)?{_EXTENSION}\s+files?"
    rf"(?:\s+(?:of|from)\s+(?:the\s+)?run)?\s*\??\s*$",
    re.IGNORECASE,
)
_KNOWN_BARE_EXTENSIONS = frozenset(
    {
        "csv",
        "gif",
        "html",
        "jpeg",
        "jpg",
        "json",
        "md",
        "pdf",
        "png",
        "py",
        "svg",
        "txt",
        "xlsx",
        "zip",
    }
)
_MAX_LOCATION_MESSAGE_CHARS = 6000  # Keep the canonical SessionChatProposal.message bound.
_MAX_LOCATION_SOURCES = 12  # Keep the canonical SessionChatProposal.sources bound.
_MIN_RUN_PATH_PARTS = 3


def file_location_reply(
    question: str,
    records: Iterable[tuple[str, SessionReadRecord]],
    visible_citations: Mapping[str, SessionCitation],
    *,
    has_linked_clarification: bool = False,
) -> SessionChatProposal | None:
    """Answer only a full, simple location question from exact returned file records."""
    if has_linked_clarification:
        return None
    requested = _requested_extension(question)
    if requested is None:
        return None
    extension, language = requested

    paths: list[tuple[str, str]] = []
    page_only = False
    for source, record in records:
        citation = visible_citations.get(source)
        path = _verified_file_path(record, citation)
        if path is None or not _has_literal_extension(path, extension):
            continue
        paths.append((source, path))
        value = record.value
        offset = value.get("offset")
        page_only = (
            page_only
            or (isinstance(offset, int) and offset > 0)
            or value.get("truncated") is True
            or value.get("next_offset") is not None
        )

    unique: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for source, path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        unique.append((source, path))
    if not unique:
        return None

    page_only = page_only or len(unique) > _MAX_LOCATION_SOURCES
    visible, message = _bounded_location_message(unique, page_only, language)
    if not visible:
        return None
    return SessionChatProposal.model_validate(
        {
            "kind": "answer",
            "message": message,
            "sources": [source for source, _ in visible],
        }
    )


def _bounded_location_message(
    matches: list[tuple[str, str]], page_only: bool, language: str
) -> tuple[list[tuple[str, str]], str]:
    """Keep whole citation-bound paths inside the canonical proposal message bound."""
    limited = len(matches) > _MAX_LOCATION_SOURCES
    candidates = matches[:_MAX_LOCATION_SOURCES]
    show_page_note = page_only or limited
    while True:
        if language == "es":
            title = "Archivos ubicados en esta consulta"
            if show_page_note:
                title += " (solo la página devuelta; puede haber más coincidencias)"
        else:
            title = "Files located in this query"
            if show_page_note:
                title += " (returned page only; more matches may exist)"

        included: list[tuple[str, str]] = []
        lines = [f"{title}:"]
        for item in candidates:
            candidate_lines = [*lines, f"- {_display_path(item[1])}"]
            if len("\n".join(candidate_lines)) > _MAX_LOCATION_MESSAGE_CHARS:
                limited = True
                break
            included.append(item)
            lines.append(candidate_lines[-1])
        if limited and not show_page_note:
            show_page_note = True
            continue
        return included, "\n".join(lines)


def _requested_extension(question: str) -> tuple[str, str] | None:
    spanish_match = _SPANISH_LOCATION.fullmatch(question)
    match = spanish_match or _ENGLISH_LOCATION.fullmatch(question)
    if match is None:
        return None
    value = match.group("extension")
    if not value.isascii():
        return None
    value = value.lower()
    if value.startswith("."):
        extension = value
    elif value in _KNOWN_BARE_EXTENSIONS:
        extension = f".{value}"
    else:
        return None
    return extension, "es" if spanish_match is not None else "en"


def _has_literal_extension(path: str, extension: str) -> bool:
    filename = path.rsplit("/", 1)[-1]
    _, separator, suffix = filename.rpartition(".")
    return bool(separator and suffix.isascii() and f".{suffix.lower()}" == extension)


def _verified_file_path(record: SessionReadRecord, citation: SessionCitation | None) -> str | None:
    """Bind a positive file row to the exact server-created path citation."""
    value = record.value
    operation = value.get("operation")
    returned_count = value.get("returned_count")
    positive_file_row = (operation == "list_files" and value.get("kind") == "file") or (
        operation == "glob" and value.get("scan_complete") is True
    )
    positive_count = (
        isinstance(returned_count, int)
        and not isinstance(returned_count, bool)
        and returned_count > 0
    )
    if not positive_file_row or not positive_count:
        return None

    path = value.get("path")
    if not isinstance(path, str) or not path or citation is None or citation != record.citation:
        return None
    if scrub_credentials(path) != path:
        return None
    if citation.path != path:
        return None
    parts = path.split("/")
    expected_run_id = parts[1] if len(parts) >= _MIN_RUN_PATH_PARTS and parts[0] == "runs" else None
    if citation.run_id != expected_run_id:
        return None
    return path


def _display_path(path: str) -> str:
    """Render a literal path without allowing Markdown or HTML interpretation."""
    safe = path.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    delimiter = "`" * (max((len(run) for run in re.findall(r"`+", safe)), default=0) + 1)
    return f"{delimiter}{safe}{delimiter}"
