"""Reject malformed registered Python before asking a human to approve its write."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.thymira.fixtures_tools import review_gated_context
from thymira.schemas import EventType, new_id
from thymira.tools import ToolManager
from thymira.tools.builtins.run_python import builtins_registry

if TYPE_CHECKING:
    from pathlib import Path


def test_invalid_registered_python_write_fails_before_human_review(tmp_path: Path) -> None:
    context = review_gated_context(tmp_path, run_id=new_id("run"))
    execution = ToolManager(builtins_registry()).execute(
        context,
        "write_file",
        {
            "path": "analysis.py",
            "kind": "code",
            "content": r"import pandas as pd\nprint('ready')",
            "description": "Write a Python script",
        },
    )
    assert not execution.result.success
    assert execution.pending_approval is None
    assert "literal backslash+n" in (execution.result.error or "")
    assert not (context.workspace / "analysis.py").exists()
    assert not any(e.type is EventType.HUMAN_APPROVAL_REQUESTED for e in context.event_log.events())


@pytest.mark.parametrize(
    ("path", "kind", "content"),
    [
        ("analysis.py", "code", r"print('literal \n in string')"),
        ("README.md", "report", "This is prose, not Python."),
        ("script.js", "code", "const x = 1;"),
        ("draft.py", None, "incomplete ="),
    ],
)
def test_write_preflight_preserves_valid_python_and_other_file_formats(
    tmp_path: Path,
    path: str,
    kind: str | None,
    content: str,
) -> None:
    context = review_gated_context(tmp_path, run_id=new_id("run"))
    execution = ToolManager(builtins_registry()).execute(
        context,
        "write_file",
        {
            "path": path,
            "kind": kind,
            "content": content,
            "description": "Write source or text",
        },
    )
    assert execution.pending_approval is not None
    assert execution.call.arguments["content"] == content
