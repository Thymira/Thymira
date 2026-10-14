"""Coding agent error recovery: diagnose a failed run, bounded retry (THY-17)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.thymira.fixtures_tools import development_policy
from thymira.agents import AgentContext, Delegator, LLMToolCall, ScriptedProvider
from thymira.events import InMemoryEventLog
from thymira.policies import Gate, PolicyEngine, RiskProfile, auto_approve
from thymira.schemas import EventType, ModelRoutePolicy, SandboxMode, TaskStatus, new_id
from thymira.state import LocalArtifactStore
from thymira.thy import CODING_AGENT_SPEC, coding_agent_catalog
from thymira.tools import Tool, ToolContext, ToolRegistry
from thymira.tools.builtins import (
    ExportPdf,
    GitDiff,
    ReadFile,
    RunNotebook,
    RunPython,
    WriteFile,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from thymira.agents.llm.base import LLMMessage

TEST_ROUTE_POLICY = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")


@pytest.fixture(autouse=True)
def _configured_test_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the direct AgentContext provider seam to an explicit code-owned test route."""
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")


_BROKEN_SCRIPT = "1 / 0\n"
_FIXED_SCRIPT = "print('recovered')\n"


def _tool_context(tmp_path: Path, *, run_id: str, log: InMemoryEventLog) -> ToolContext:
    return ToolContext(
        run_id=run_id,
        agent_id=new_id("agent"),
        workspace=tmp_path / "workspace",
        event_log=log,
        gate=Gate(PolicyEngine(development_policy()), log, approver=auto_approve),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", run_id),
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=1.0
        ),
    )


def test_coding_agent_diagnoses_a_failure_and_completes_on_the_second_attempt(
    tmp_path: Path,
) -> None:
    run_id = new_id("run")
    log = InMemoryEventLog(run_id)
    context = _tool_context(tmp_path, run_id=run_id, log=log)
    registry = ToolRegistry(
        (
            cast("Tool", WriteFile()),
            cast("Tool", RunPython(mode=SandboxMode.DANGER_FULL_ACCESS)),
            cast("Tool", ReadFile()),
            cast("Tool", ExportPdf()),
            cast("Tool", RunNotebook(mode=SandboxMode.DANGER_FULL_ACCESS)),
            cast("Tool", GitDiff()),
        )
    )
    provider = ScriptedProvider(
        [
            LLMToolCall(
                id="call-1",
                name="write_file",
                arguments={
                    "path": "script.py",
                    "content": _BROKEN_SCRIPT,
                    "kind": "code",
                    "description": "Write the broken divide-by-zero script",
                },
            ),
            _registered_run_call(context, "call-2", "Run the broken registered script"),
            LLMToolCall(
                id="call-3",
                name="write_file",
                arguments={
                    "path": "script.py",
                    "content": _FIXED_SCRIPT,
                    "kind": "code",
                    "description": "Write the fixed recovery script",
                },
            ),
            _registered_run_call(context, "call-4", "Run the fixed registered script"),
            LLMToolCall(
                id="call-5",
                name="final_result",
                arguments={"stdout": "recovered\n", "exit_code": 0, "artifacts": []},
            ),
        ]
    )
    ctx = AgentContext(
        route_policy=TEST_ROUTE_POLICY,
        catalog=coding_agent_catalog(),
        event_log=log,
        provider=provider,
        tool_registry=registry,
        tool_context=context,
    )
    delegator = Delegator(ctx)

    task = delegator.delegate("thy", CODING_AGENT_SPEC, "run the script", depth=0).task

    assert task.status is TaskStatus.COMPLETED
    assert task.error is None
    assert task.summary is not None
    assert '"exit_code":0' in task.summary.replace(" ", "")
    completed = [event for event in log.events() if event.type is EventType.AGENT_COMPLETED][-1]
    assert completed.payload["completion_evidence"]["repaired_tool_call_ids"]


def _registered_run_call(
    context: ToolContext, call_id: str, description: str
) -> Callable[[Sequence[LLMMessage]], LLMToolCall]:
    """Build a RunPython request from the current task-owned CODE artifact revision."""

    def request(_messages: Sequence[LLMMessage]) -> LLMToolCall:
        artifact = context.artifact_store.get("script.py")
        if artifact is None:
            raise AssertionError("the script source must be registered before execution")
        return LLMToolCall(
            id=call_id,
            name="run_python",
            arguments={"source_artifact_id": artifact.id, "description": description},
        )

    return request


def test_coding_agent_ends_failed_with_a_diagnosis_after_persistent_failure(
    tmp_path: Path,
) -> None:
    run_id = new_id("run")
    log = InMemoryEventLog(run_id)
    context = _tool_context(tmp_path, run_id=run_id, log=log)
    registry = ToolRegistry(
        (
            cast("Tool", WriteFile()),
            cast("Tool", RunPython(mode=SandboxMode.DANGER_FULL_ACCESS)),
            cast("Tool", ReadFile()),
            cast("Tool", ExportPdf()),
            cast("Tool", RunNotebook(mode=SandboxMode.DANGER_FULL_ACCESS)),
            cast("Tool", GitDiff()),
        )
    )
    # Script one more run_python attempt than CODING_AGENT_SPEC.max_turns allows, and never
    # supply a final_result, so the agent exhausts its turn budget still failing.
    provider = ScriptedProvider(
        [
            LLMToolCall(
                id=f"call-{i}",
                name="run_python",
                arguments={
                    "code": _BROKEN_SCRIPT,
                    "description": "Run the broken divide-by-zero script",
                },
            )
            for i in range(CODING_AGENT_SPEC.max_turns + 1)
        ]
    )
    ctx = AgentContext(
        route_policy=TEST_ROUTE_POLICY,
        catalog=coding_agent_catalog(),
        event_log=log,
        provider=provider,
        tool_registry=registry,
        tool_context=context,
    )
    delegator = Delegator(ctx)

    task = delegator.delegate("thy", CODING_AGENT_SPEC, "run the script", depth=0).task

    assert task.status is TaskStatus.FAILED
    assert task.summary is None
    assert task.error is not None
    assert task.error != "agent exceeded max_turns"
    assert "ZeroDivisionError" in task.error
