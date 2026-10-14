"""Cross-process locks for serializing one Session's conversation turns."""

from __future__ import annotations

from pathlib import Path

from thymira.events import secure_directory
from thymira.state._lifecycle_lock import FileLock


def conversation_lock(root: Path, session_id: str) -> FileLock:
    """Return the nonblocking OS lock for one conversation Session."""
    if Path(session_id).name != session_id:
        raise ValueError("session_id must be a path-safe identifier")
    lock_root = Path(root) / ".locks"
    secure_directory(lock_root)
    return FileLock(lock_root / f"conversation-{session_id}.lock")


__all__ = ["conversation_lock"]
