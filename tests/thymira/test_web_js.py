"""Run the console's node unit tests (pure folds, markdown parser, router, palette matching)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node") or ""
SUITE = Path(__file__).resolve().parent / "web_js"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_console_node_suite_passes() -> None:
    files = sorted(str(path) for path in SUITE.glob("*.test.mjs"))
    assert files, "no node tests found"
    result = subprocess.run(
        [NODE, "--test", *files], capture_output=True, text=True, check=False, encoding="utf-8"
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
