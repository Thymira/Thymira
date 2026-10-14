"""A coding agent executes the registered source identity returned by its write."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

from tests.thymira.fixtures_tools import human_approved_context, review_gated_context
from thymira.agents import AgentContext, AgentRunner, LLMToolCall, ScriptedProvider
from thymira.agents.history import TOOL_RETURN_SUCCESS_METADATA_KEY
from thymira.agents.resume import ResumeStatus, load_pending_resume
from thymira.agents.tool_bridge import repeated_invalid_python_source_reason
from thymira.schemas import (
    Actor,
    ActorKind,
    ArtifactKind,
    EventType,
    ModelRoutePolicy,
    SandboxEnforcement,
    SandboxMode,
    Task,
    TaskStatus,
    new_id,
)
from thymira.thy.agents.coding import CODING_AGENT_SPEC, coding_agent_catalog
from thymira.tools import Tool, ToolManager, ToolRegistry, value_text
from thymira.tools.builtins.files import WriteFile
from thymira.tools.builtins.run_python import builtins_registry
from thymira.tools.sandbox import SandboxRun

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pytest

    from thymira.agents.llm.base import LLMMessage


@dataclass
class SourceCapture:
    """Capture the actual interpreter input without launching an external runtime."""

    sources: list[bytes] = field(default_factory=list)

    def run(self, argv: list[str], **kwargs: Any) -> SandboxRun:
        del argv
        self.sources.append(kwargs["staged_inputs"][0].content)
        return SandboxRun(
            stdout="done\n",
            stderr="",
            exit_code=0,
            mode=SandboxMode.WORKSPACE_WRITE,
            enforcement=SandboxEnforcement.PARTIAL,
        )


def test_registered_write_exposes_its_identity_to_the_model(tmp_path: Path) -> None:
    context = human_approved_context(tmp_path, run_id=new_id("run"))
    manager = ToolManager(ToolRegistry((cast("Tool", WriteFile()),)))
    execution = manager.execute(
        context,
        "write_file",
        {
            "path": "analysis.py",
            "content": "print('done')\n",
            "kind": "code",
            "description": "Register the script",
        },
    )
    assert execution.result.success
    assert execution.result.value is not None
    visible = json.loads(value_text(execution.result.value))
    assert visible["artifact_id"] == execution.result.artifact_ids[0]


def test_registered_invalid_source_limit_survives_history_reconstruction() -> None:
    source_id = new_id("artifact")
    history = []
    for index in range(2):
        call_id = f"call-{index}"
        history.extend(
            (
                ModelResponse(
                    parts=[ToolCallPart("run_python", {"source_artifact_id": source_id}, call_id)]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "run_python",
                            "Error: invalid arguments for tool 'run_python': code: Value error, "
                            "invalid Python syntax at line 1, column 2",
                            call_id,
                            metadata={TOOL_RETURN_SUCCESS_METADATA_KEY: False},
                        )
                    ]
                ),
            )
        )
    assert repeated_invalid_python_source_reason(history) is not None


def test_coding_agent_uses_visible_source_id_without_resending_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")
    context = human_approved_context(tmp_path, run_id=new_id("run"))
    source = "value = r'a\\nb'\nprint(value)\n"
    capture = SourceCapture()
    registry = builtins_registry(sandbox=capture)
    chosen_ids: list[str] = []

    def execute_written_source(messages: Sequence[LLMMessage]) -> LLMToolCall:
        visible = next(message.content for message in reversed(messages) if message.role == "tool")
        match = re.search(r'"artifact_id"\s*:\s*"(artifact_[a-z0-9]+)"', visible)
        assert match is not None, visible
        chosen_ids.append(match[1])
        return LLMToolCall(
            id="execute",
            name="run_python",
            arguments={
                "source_artifact_id": match[1],
                "description": "Execute the saved script",
            },
        )

    provider = ScriptedProvider(
        [
            LLMToolCall(
                id="write",
                name="write_file",
                arguments={
                    "path": "analysis.py",
                    "content": source,
                    "kind": "code",
                    "description": "Register the script",
                },
            ),
            execute_written_source,
            LLMToolCall(
                id="result",
                name="final_result",
                arguments={
                    "stdout": "done\n",
                    "exit_code": 0,
                    "artifacts": ["analysis.py"],
                },
            ),
        ]
    )
    task = Task(
        id=new_id("task"),
        run_id=context.run_id,
        agent_id=context.agent_id,
        objective="Write and execute the analysis script",
    )
    result = AgentRunner().run(
        CODING_AGENT_SPEC,
        task,
        AgentContext(
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
            catalog=coding_agent_catalog(),
            event_log=context.event_log,
            provider=provider,
            tool_registry=registry,
            tool_context=context,
        ),
    )
    assert result.task_status is TaskStatus.COMPLETED
    assert capture.sources == [source.encode("utf-8")]
    starts = [
        event
        for event in context.event_log.events()
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "run_python"
    ]
    assert len(starts) == 1
    assert starts[0].payload["arguments"]["source_artifact_id"] == chosen_ids[0]
    assert starts[0].payload["arguments"]["code"] == source
    assert context.event_log.verify().valid


def test_source_reference_survives_agent_approval_and_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")
    context = review_gated_context(tmp_path, run_id=new_id("run"))
    source = "print(r'preserve\\nthis')\n"
    artifact = context.artifact_store.save_text(
        "analysis.py",
        source,
        produced_by=context.agent_id,
        kind=ArtifactKind.CODE,
    )
    capture = SourceCapture()
    registry = builtins_registry(sandbox=capture)
    task = Task(
        id=new_id("task"),
        run_id=context.run_id,
        agent_id=context.agent_id,
        objective="Execute the registered analysis",
    )
    catalog = coding_agent_catalog()
    route = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")
    first_provider = ScriptedProvider(
        [
            LLMToolCall(
                id="execute",
                name="run_python",
                arguments={
                    "source_artifact_id": artifact.id,
                    "description": "Run the saved analysis",
                },
            )
        ]
    )
    parked = AgentRunner().run(
        CODING_AGENT_SPEC,
        task,
        AgentContext(
            route_policy=route,
            catalog=catalog,
            event_log=context.event_log,
            provider=first_provider,
            tool_registry=registry,
            tool_context=context,
        ),
    )
    assert parked.task_status is TaskStatus.PENDING
    assert capture.sources == []
    request = next(
        event
        for event in context.event_log.events()
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    decision_id = request.payload["decision_id"]
    context.event_log.append(
        EventType.HUMAN_APPROVAL,
        Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        {"decision_id": decision_id, "approved": True, "automatic": False},
        subject_id=decision_id,
    )
    lookup = load_pending_resume(
        context.artifact_store,
        context,
        task.agent_id,
        task_id=task.id,
    )
    assert lookup.status is ResumeStatus.READY
    assert lookup.resume is not None
    # A mutable workspace file is not the source selected and approved above.
    context.workspace.mkdir(parents=True, exist_ok=True)
    (context.workspace / "analysis.py").write_text(
        "raise RuntimeError('wrong version')", encoding="utf-8"
    )
    next_provider = ScriptedProvider(
        [
            LLMToolCall(
                id="result",
                name="final_result",
                arguments={"stdout": "done\n", "exit_code": 0, "artifacts": ["analysis.py"]},
            )
        ]
    )
    result = AgentRunner().run(
        CODING_AGENT_SPEC,
        task,
        AgentContext(
            route_policy=route,
            catalog=catalog,
            event_log=context.event_log,
            provider=next_provider,
            tool_registry=registry,
            tool_context=context,
        ),
        resume=lookup.resume,
    )
    assert result.task_status is TaskStatus.COMPLETED
    assert capture.sources == [source.encode("utf-8")]
    assert (
        sum(
            event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in context.event_log.events()
        )
        == 1
    )
    assert context.event_log.verify().valid


def test_agent_stops_repeating_one_invalid_registered_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")
    context = human_approved_context(tmp_path, run_id=new_id("run"))
    artifact = context.artifact_store.save_text(
        "broken.py",
        r"print(1)\nprint(2)",
        produced_by=context.agent_id,
        kind=ArtifactKind.CODE,
    )
    capture = SourceCapture()
    provider = ScriptedProvider(
        [
            LLMToolCall(
                id=f"attempt-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": artifact.id,
                    "description": "Execute the registered script",
                },
            )
            for index in range(3)
        ]
    )
    task = Task(
        id=new_id("task"),
        run_id=context.run_id,
        agent_id=context.agent_id,
        objective="Execute the analysis",
    )
    result = AgentRunner().run(
        CODING_AGENT_SPEC,
        task,
        AgentContext(
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
            catalog=coding_agent_catalog(),
            event_log=context.event_log,
            provider=provider,
            tool_registry=builtins_registry(sandbox=capture),
            tool_context=context,
        ),
    )
    assert result.task_status is TaskStatus.FAILED
    assert len(provider.calls) == 2
    assert capture.sources == []
    assert not any(
        event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in context.event_log.events()
    )
