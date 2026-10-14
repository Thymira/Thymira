"""Observe workspace files before Python execution to reject stale output bytes."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from thymira.events import sha256_bytes, sha256_file
from thymira.tools.builtins.files import contained_path
from thymira.tools.builtins.subprocess_command import assert_no_link_components
from thymira.tools.models import ToolExecutionError

if TYPE_CHECKING:
    import os


@dataclass(frozen=True, slots=True)
class _FileWitness:
    sha256: str
    fingerprint: tuple[int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class PythonOutputFreshnessSnapshot:
    """Prior file witnesses relevant to one fixed or data-dependent output set."""

    files: dict[str, _FileWitness]
    unreadable_paths: tuple[str, ...]


def capture_python_output_freshness(
    workspace: Path, output_paths: tuple[str, ...] | None
) -> PythonOutputFreshnessSnapshot:
    """Capture hashes and stat identity for exact outputs or all workspace files for a manifest."""
    root = workspace.resolve()
    if output_paths is not None:
        return _capture_fixed_output_freshness(root, output_paths)

    files: dict[str, _FileWitness] = {}
    unreadable: set[str] = set()
    if not root.exists():
        # A nonexistent workspace contains no prior dynamic outputs. The normal workspace
        # setup immediately before execution will create and validate its root.
        return PythonOutputFreshnessSnapshot(files=files, unreadable_paths=())

    def on_walk_error(error: OSError) -> None:
        failed = Path(error.filename) if error.filename else root
        try:
            relative = failed.relative_to(root).as_posix()
        except ValueError:
            relative = "."
        unreadable.add(relative.casefold())

    for directory, child_directories, file_names in root.walk(
        top_down=True, on_error=on_walk_error, follow_symlinks=False
    ):
        relative_directory = directory.relative_to(root).as_posix()
        child_directories[:] = [
            name
            for name in child_directories
            if not (relative_directory == "." and name.casefold() == ".thymira")
        ]
        for name in file_names:
            path = directory / name
            try:
                relative = path.relative_to(root).as_posix()
                if relative.split("/", 1)[0].casefold() == ".thymira":
                    continue
                assert_no_link_components(root, path)
                witness = _file_witness(root, path)
                if witness is not None:
                    files[relative.casefold()] = witness
            except (OSError, ValueError):
                try:
                    unreadable.add(path.relative_to(root).as_posix().casefold())
                except ValueError:
                    unreadable.add(".")
    return PythonOutputFreshnessSnapshot(files=files, unreadable_paths=tuple(sorted(unreadable)))


def _capture_fixed_output_freshness(
    root: Path, output_paths: tuple[str, ...]
) -> PythonOutputFreshnessSnapshot:
    """Capture witnesses for fixed outputs without changing publication error ordering."""
    files: dict[str, _FileWitness] = {}
    unreadable: set[str] = set()
    for name in output_paths:
        try:
            path = contained_path(root, name)
            relative = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            # Path validity is checked at publication, after the process, as before this
            # freshness witness was added. Invalid fixed declarations gain no prior witness.
            continue
        key = relative.casefold()
        try:
            assert_no_link_components(root, path)
        except (OSError, ValueError):
            # Link-shaped destinations still fail in the original ordered validation.
            continue
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            unreadable.add(key)
            continue
        try:
            witness = _file_witness(root, path)
            if witness is not None:
                files[key] = witness
        except OSError:
            # A prior contained destination with no reliable witness cannot prove freshness.
            unreadable.add(key)
        except ValueError:
            # A path that became link-shaped still follows the original publication checks.
            continue
    return PythonOutputFreshnessSnapshot(files=files, unreadable_paths=tuple(sorted(unreadable)))


def require_fresh_python_output(
    workspace: Path,
    path: Path,
    data: bytes,
    snapshot: PythonOutputFreshnessSnapshot,
    *,
    copied_workspace: bool,
    explicitly_reused: bool,
) -> None:
    """Require a new file, an observed write/change, or verified explicit artifact reuse."""
    if explicitly_reused:
        return
    root = workspace.resolve()
    relative = path.relative_to(root).as_posix()
    key = relative.casefold()
    if any(
        failed in {".", key} or key.startswith(failed.rstrip("/") + "/")
        for failed in snapshot.unreadable_paths
    ):
        raise ToolExecutionError(
            f"could not verify whether declared output {relative!r} predates this execution"
        )
    previous = snapshot.files.get(key)
    if previous is None:
        return
    current = _file_witness(root, path)
    if current is None:
        raise ToolExecutionError(f"declared output {relative!r} is not a regular file")
    content_changed = sha256_bytes(data) != previous.sha256
    observed_write = current.fingerprint != previous.fingerprint
    if not content_changed and (copied_workspace or not observed_write):
        raise ToolExecutionError(
            f"declared output {relative!r} predates this execution; rewrite it or explicitly "
            "reuse a verified artifact"
        )


def _file_witness(workspace: Path, path: Path) -> _FileWitness | None:
    """Hash one regular file only while its no-follow stat identity remains stable."""
    assert_no_link_components(workspace, path)
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        return None
    digest = sha256_file(path)
    after = path.stat(follow_symlinks=False)
    fingerprint = _fingerprint(before)
    if fingerprint != _fingerprint(after):
        raise OSError("file changed while its freshness witness was captured")
    return _FileWitness(sha256=digest, fingerprint=fingerprint)


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    """Return stable device, inode, size and high-resolution change timestamps."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = [
    "PythonOutputFreshnessSnapshot",
    "capture_python_output_freshness",
    "require_fresh_python_output",
]
