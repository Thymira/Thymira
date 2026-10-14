"""Bounded project browsing and optimistic edits for the workspace inspector."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from thymira.events import redact_export
from thymira.state.lifecycle_errors import OwnerBusyError
from thymira.state.project_file_lock import ProjectFileLockError, project_file_lock

MAX_FILE_BYTES = 64 * 1024
MAX_DIRECTORY_ENTRIES = 200
_HIDDEN_DIRECTORIES = frozenset({"node_modules", "__pycache__", "runs"})
_PRIVATE_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore"})
_REDACTION_MARKER = "[REDACTED:"
_FILE_LOCKS: dict[Path, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


class ProjectFileError(ValueError):
    """A requested path is outside the public project browsing surface."""


class ProjectFileConflictError(ProjectFileError):
    """The file changed after the caller read the revision it is trying to save."""


class ProjectFileReadOnlyError(ProjectFileError):
    """The requested file or new content is redacted, truncated or otherwise read-only."""


class ProjectFileTooLargeError(ProjectFileError):
    """The text to save exceeds the project explorer's byte limit."""


@dataclass(frozen=True, slots=True)
class ProjectFileEntry:
    """One non-hidden project entry, expressed relative to the project root."""

    path: str
    name: str
    directory: bool


@dataclass(frozen=True, slots=True)
class ProjectFilePreview:
    """A bounded text preview with its revision and an explicit editability decision."""

    content: str
    truncated: bool
    sha256: str | None
    editable: bool
    read_only_reason: Literal["truncated", "redacted"] | None


@dataclass(frozen=True, slots=True)
class ProjectFileRevision:
    """The exact content and digest published by one successful project file edit."""

    path: str
    content: str
    sha256: str


def _file_lock_for(root: Path) -> threading.Lock:
    """Return one in-process write lock for the resolved project root."""
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(root, threading.Lock())


def _visible_name(name: str) -> bool:
    lowered = name.lower()
    return (
        not lowered.startswith(".")
        and lowered not in _HIDDEN_DIRECTORIES
        and not lowered.startswith(("id_rsa", "id_ed25519", "credentials", "secrets"))
        and Path(lowered).suffix not in _PRIVATE_SUFFIXES
    )


def _project_path(root: Path, relative: str) -> Path:
    """Refuse traversal, hidden paths, Windows aliases and filesystem links."""
    if "\\" in relative or ":" in relative or "\0" in relative:
        raise ProjectFileError("Use a project-relative path with forward slashes.")
    parts = relative.split("/") if relative else []
    if PurePosixPath(relative).is_absolute() or any(
        part in {"", ".", ".."} or part.rstrip(". ") != part or not _visible_name(part)
        for part in parts
    ):
        raise ProjectFileError("The requested path is not available in the project explorer.")
    base = root.resolve(strict=True)
    target = base
    for part in parts:
        target /= part
        info = target.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
            or (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        ):
            raise ProjectFileError("Filesystem links are not available in the project explorer.")
    if not target.resolve(strict=True).is_relative_to(base):
        raise ProjectFileError("The requested path is outside the project.")
    return target


def list_project_files(root: Path, relative: str = "") -> tuple[tuple[ProjectFileEntry, ...], bool]:
    """List a single directory without following links or exposing runtime/credential files."""
    directory = _project_path(root, relative)
    if not directory.is_dir():
        raise ProjectFileError("The requested path is not a directory.")
    entries: list[ProjectFileEntry] = []
    truncated = False
    for child in directory.iterdir():
        if not _visible_name(child.name):
            continue
        child_path = f"{relative}/{child.name}" if relative else child.name
        try:
            resolved = _project_path(root, child_path)
            mode = resolved.stat().st_mode
        except (ProjectFileError, FileNotFoundError):
            continue
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            continue
        if len(entries) == MAX_DIRECTORY_ENTRIES:
            truncated = True
            break
        entries.append(ProjectFileEntry(child_path, child.name, stat.S_ISDIR(mode)))
    entries.sort(key=lambda entry: (not entry.directory, entry.name.casefold()))
    return tuple(entries), truncated


def read_project_file(root: Path, relative: str) -> tuple[str, bool]:
    """Return the existing tuple-shaped, redacted preview API for one project file."""
    preview = read_project_file_details(root, relative)
    return preview.content, preview.truncated


def read_project_file_details(root: Path, relative: str) -> ProjectFilePreview:
    """Return a bounded preview, exact revision when bounded, and safe editability metadata."""
    path = _project_path(root, relative)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ProjectFileError("The requested path is not a regular file.")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if opened.st_nlink != 1:
            raise ProjectFileError("Filesystem links are not available in the project explorer.")
        current = _project_path(root, relative).lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ProjectFileError("The project file changed while opening its preview.")
        data = handle.read(MAX_FILE_BYTES + 1)
    truncated = len(data) > MAX_FILE_BYTES
    data = data[:MAX_FILE_BYTES]
    if b"\0" in data:
        raise ProjectFileError("Binary files cannot be previewed as text.")
    # A truncated final UTF-8 sequence is not evidence that the complete file is binary.
    text = data.decode("utf-8", errors="replace" if truncated else "strict")
    safe = redact_export(text)
    if not isinstance(safe, str):
        raise TypeError("the text redactor returned a non-text projection")
    read_only_reason = (
        "truncated"
        if truncated
        else "redacted"
        if safe != text or _REDACTION_MARKER in text
        else None
    )
    return ProjectFilePreview(
        content=safe,
        truncated=truncated,
        sha256=None if truncated else hashlib.sha256(data).hexdigest(),
        editable=read_only_reason is None,
        read_only_reason=read_only_reason,
    )


def write_project_file(
    root: Path,
    relative: str,
    content: str,
    *,
    expected_sha256: str,
) -> ProjectFileRevision:
    """Atomically replace one existing, bounded, unredacted project text file by revision.

    Raises:
        ProjectFileConflictError: The file's current digest differs from ``expected_sha256``.
        ProjectFileReadOnlyError: The existing file or submitted text is not editable.
        ProjectFileTooLargeError: The submitted UTF-8 content exceeds :data:`MAX_FILE_BYTES`.
        ProjectFileError: The path is not a visible, existing regular file.
    """
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProjectFileError("The content must be valid UTF-8 text.") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ProjectFileTooLargeError(
            f"Project files may not exceed {MAX_FILE_BYTES} UTF-8 bytes."
        )
    if b"\0" in data:
        raise ProjectFileReadOnlyError("Binary files cannot be edited as text.")
    safe_content = redact_export(content)
    if not isinstance(safe_content, str):
        raise TypeError("the text redactor returned a non-text projection")
    if safe_content != content or _REDACTION_MARKER in content:
        raise ProjectFileReadOnlyError(
            "Content containing secrets or export-redaction masks cannot be saved."
        )

    resolved_root = root.resolve(strict=True)
    with _file_lock_for(resolved_root):
        target = _project_path(resolved_root, relative)
        try:
            with project_file_lock(target.parent):
                _write_project_file_under_lock(
                    resolved_root,
                    relative,
                    data,
                    expected_sha256,
                )
        except OwnerBusyError as exc:
            raise ProjectFileConflictError(
                "Another project file save is in progress; retry after it finishes."
            ) from exc
        except ProjectFileLockError as exc:
            raise ProjectFileError(str(exc)) from exc

    return ProjectFileRevision(
        path=relative,
        content=content,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _write_project_file_under_lock(
    root: Path,
    relative: str,
    data: bytes,
    expected_sha256: str,
) -> None:
    """Stage and publish an edit while the caller holds the directory writer lock."""
    target = _project_path(root, relative)
    parent_stat = target.parent.stat()
    parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
    _require_current_edit_revision(root, relative, expected_sha256)
    mode = stat.S_IMODE(target.stat().st_mode)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex[:12]}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)

        # This detects intervening source edits before replacement; ordinary external editors do
        # not participate in the app's advisory writer lock.
        _require_current_edit_revision(root, relative, expected_sha256)
        checked_target = _project_path(root, relative)
        checked_parent = checked_target.parent.stat()
        if (checked_parent.st_dev, checked_parent.st_ino) != parent_identity:
            raise ProjectFileConflictError(
                "The project directory changed while saving; reload before retrying."
            )
        temporary.replace(checked_target)
    finally:
        temporary.unlink(missing_ok=True)


def _require_current_edit_revision(root: Path, relative: str, expected_sha256: str) -> None:
    """Refuse a write unless the source is editable and still matches the caller's digest."""
    current = read_project_file_details(root, relative)
    if not current.editable:
        raise ProjectFileReadOnlyError(
            f"The requested file is read-only ({current.read_only_reason})."
        )
    if current.sha256 != expected_sha256:
        raise ProjectFileConflictError(
            "The file changed after it was read; reload it before saving."
        )


__all__ = [
    "MAX_FILE_BYTES",
    "ProjectFileConflictError",
    "ProjectFileError",
    "ProjectFilePreview",
    "ProjectFileReadOnlyError",
    "ProjectFileRevision",
    "ProjectFileTooLargeError",
    "list_project_files",
    "read_project_file",
    "read_project_file_details",
    "write_project_file",
]
