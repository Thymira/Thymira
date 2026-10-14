"""Cross-process exclusion for edits made through the project explorer."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from thymira.state._lifecycle_lock import FileLock

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOCK_NAME = ".thymira-file-edit.lock"


class ProjectFileLockError(ValueError):
    """The project edit lock path is not a safe regular file."""


def _validate_lock_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or info.st_nlink != 1
    ):
        raise ProjectFileLockError("The project edit lock is not a safe regular file.")


@contextmanager
def project_file_lock(directory: Path) -> Iterator[None]:
    """Exclusively lock one validated project directory without blocking a second writer.

    The hidden lock is placed beside the file being edited, not under the runtime evidence tree.
    Existing symlinks and reparse points are rejected before the shared OS lock opens the path.
    """
    directory = Path(directory)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ProjectFileLockError("The project edit lock directory is not a safe directory.")

    path = directory / _LOCK_NAME
    _validate_lock_path(path)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)

    _validate_lock_path(path)
    with FileLock(path):
        _validate_lock_path(path)
        yield


__all__ = ["ProjectFileLockError", "project_file_lock"]
