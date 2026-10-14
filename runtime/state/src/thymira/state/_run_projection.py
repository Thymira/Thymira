"""Atomic publication and read-back verification of a derived Run projection."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from thymira.events import canonical_json, secure_directory, secure_file
from thymira.state._atomic import fsync_directory, replace_with_retry


def write_run_projection(run_dir: Path, projection: dict[str, Any]) -> None:
    """Replace and verify ``run.json`` while the caller holds its Run lock."""
    target = run_dir / "run.json"
    secure_directory(run_dir)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=run_dir,
        prefix=".run-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        secure_file(temporary)
        handle.write(canonical_json(projection) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        replace_with_retry(temporary, target)
        secure_file(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    try:
        readback = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"run {run_dir.name}: projection read-back failed: {exc}") from exc
    if readback != projection:
        raise ValueError(f"run {run_dir.name}: projection read-back differs from derived state")
    fsync_directory(run_dir)
