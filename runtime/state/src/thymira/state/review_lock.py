"""Cross-process locks for resolving a human review on one local Run."""

from __future__ import annotations

from pathlib import Path

from thymira.events import secure_directory
from thymira.state._lifecycle_lock import FileLock


def review_resolution_lock(run_directory: Path) -> FileLock:
    """Return the nonblocking OS lock scoped to one existing Run's approval resolution."""
    run_dir = Path(run_directory)
    lock_dir = run_dir / ".locks"
    secure_directory(lock_dir)
    return FileLock(lock_dir / "approval-resolution.lock")


__all__ = ["review_resolution_lock"]
