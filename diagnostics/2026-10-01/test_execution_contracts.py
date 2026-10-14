"""Offline diagnostic witnesses; real manager/log/store, scripted provider and sandbox."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from tests.thymira.fixtures_agent_output import DataProfileOutput
from tests.thymira.fixtures_tools import FakeTool, development_policy

from thymira.agents import (
    AgentCatalog,
    AgentContext,
    AgentResult,
    AgentRunner,
    AgentSpec,
    LLMMessage,
    LLMToolCall,
    ScriptedProvider,
)
from thymira.agents.llm.routing import Role
from thymira.events import InMemoryEventLog
from thymira.policies import Gate, PolicyEngine, RiskProfile, ToolCapability
from thymira.schemas import (
    ModelRoutePolicy,
    SandboxEnforcement,
    SandboxMode,
    Task,
    TaskStatus,
    new_id,
)
from thymira.state import LocalArtifactStore
from thymira.thy.agents.coding import CodeResult
from thymira.thy.agents.evaluation import EvaluationResult
from thymira.tools import ToolContext, ToolRegistry, ToolResult
from thymira.tools.builtins.files import WriteFile
from thymira.tools.builtins.run_python import RunPython
from thymira.tools.sandbox import SandboxRun

type ScriptedResponse = LLMToolCall | Callable[[Sequence[LLMMessage]], LLMToolCall]

MAX_SCRIPTED_REQUESTS = 3
EXPECTED_REQUESTS_BEFORE_STOP = 2


@dataclass
class DiagnosticSandbox:
    """Return interpreter success without creating a requested output."""

    def run(self, _argv: list[str], **_kwargs: Any) -> SandboxRun:
        """Return a successful no-op run for the diagnostic."""
        return SandboxRun(
            stdout="interpreter succeeded",
            stderr="",
            exit_code=0,
            mode=SandboxMode.DANGER_FULL_ACCESS,
            enforcement=SandboxEnforcement.PARTIAL,
        )


@pytest.fixture(autouse=True)
def routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route all diagnostic provider calls through the scripted model."""
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")


def run_scenario(
    tmp_path: Path,
    calls: list[ScriptedResponse],
    schema: type[BaseModel],
    *,
    allowlist: tuple[str, ...] = ("run_python",),
    max_turns: int = 4,
    custom_registry: ToolRegistry | None = None,
) -> tuple[AgentResult, InMemoryEventLog, ScriptedProvider]:
    """Run one offline agent scenario against temporary state."""
    run_id = new_id("run")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    context = ToolContext(
        run_id=run_id,
        agent_id=new_id("agent"),
        workspace=tmp_path / "workspace",
        event_log=log,
        gate=Gate(PolicyEngine(development_policy()), log),
        artifact_store=store,
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=1.0
        ),
    )
    spec = AgentSpec(
        name="coding",
        role=Role.AGENT,
        task_kinds=("code",),
        tool_allowlist=allowlist,
        max_turns=max_turns,
        max_depth=1,
        system_prompt_ref="p",
        output_schema_ref="s",
    )
    task = Task(
        id=new_id("task"),
        run_id=run_id,
        agent_id=new_id("agent"),
        objective="Produce requested independent outputs.",
    )
    catalog = AgentCatalog(
        (spec,),
        system_prompts={"coding": "Produce the requested outputs."},
        output_schemas={"coding": schema},
    )
    provider = ScriptedProvider(calls)
    registry = custom_registry or ToolRegistry(
        (RunPython(sandbox=DiagnosticSandbox(), mode=SandboxMode.DANGER_FULL_ACCESS), WriteFile())
    )
    result = AgentRunner().run(
        spec,
        task,
        AgentContext(
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
            catalog=catalog,
            event_log=log,
            provider=provider,
            tool_registry=registry,
            tool_context=context,
        ),
    )
    assert log.verify().valid  # noqa: S101  # diagnostic property probe
    assert store.verify() == []  # noqa: S101  # diagnostic property probe
    return result, log, provider


def missing_output_call(call_id: str, code: str = "print('source A')") -> LLMToolCall:
    """Build a Python call that declares an output the sandbox will omit."""
    return LLMToolCall(
        id=call_id,
        name="run_python",
        arguments={
            "code": code,
            "description": "Publish source A output",
            "output_artifacts": [{"path": "source_a.txt"}],
        },
    )


def final_code(call_id: str = "final") -> LLMToolCall:
    """Build a successful final response for the CodeResult schema."""
    return LLMToolCall(
        id=call_id,
        name="final_result",
        arguments={"stdout": "all done", "exit_code": 0, "artifacts": []},
    )


def test_later_unrelated_python_success_does_not_clear_missing_output(tmp_path: Path) -> None:
    """Keep an earlier publication failure after an unrelated successful Python call."""
    result, _log, _provider = run_scenario(
        tmp_path,
        [
            missing_output_call("missing"),
            LLMToolCall(
                id="unrelated",
                name="run_python",
                arguments={
                    "code": "print('unrelated B')",
                    "description": "Run independent source B",
                },
            ),
            final_code(),
        ],
        CodeResult,
    )
    assert result.task_status is TaskStatus.FAILED  # noqa: S101  # diagnostic property probe


def test_output_without_exit_code_does_not_hide_failed_tool(tmp_path: Path) -> None:
    """Keep a failed tool failure even when its final response omits an exit code."""
    result, _log, _provider = run_scenario(
        tmp_path,
        [
            missing_output_call("missing"),
            LLMToolCall(
                id="final", name="final_result", arguments={"row_count": 1, "columns": ["value"]}
            ),
        ],
        DataProfileOutput,
    )
    assert result.task_status is TaskStatus.FAILED  # noqa: S101  # diagnostic property probe


def test_identical_invalid_registered_write_stops_before_third_request(tmp_path: Path) -> None:
    """Stop repeating identical invalid registered source before a third request."""
    invalid = "x = 1\\ny = 2"
    calls = [
        LLMToolCall(
            id=f"invalid-{n}",
            name="write_file",
            arguments={
                "path": "analysis.py",
                "content": invalid,
                "kind": "code",
                "description": "Write registered Python source",
            },
        )
        for n in range(MAX_SCRIPTED_REQUESTS)
    ]
    _result, _log, provider = run_scenario(
        tmp_path,
        calls,
        CodeResult,
        allowlist=("write_file",),
        max_turns=MAX_SCRIPTED_REQUESTS,
    )
    assert len(provider.calls) == EXPECTED_REQUESTS_BEFORE_STOP  # noqa: S101  # diagnostic property probe


def test_changed_python_sources_without_publication_progress_stop_early(tmp_path: Path) -> None:
    """Stop changing source when each version still makes no publication progress."""
    _result, _log, provider = run_scenario(
        tmp_path,
        [
            missing_output_call(f"attempt-{n}", f"print('revision {n}')")
            for n in range(MAX_SCRIPTED_REQUESTS)
        ],
        CodeResult,
        max_turns=MAX_SCRIPTED_REQUESTS,
    )
    assert len(provider.calls) < MAX_SCRIPTED_REQUESTS  # noqa: S101  # diagnostic property probe


def test_current_exact_inline_syntax_guard_stops_at_two(tmp_path: Path) -> None:
    """Stop after the same inline syntax failure reaches the diagnostic limit."""
    calls = [
        LLMToolCall(
            id=f"invalid-{n}",
            name="run_python",
            arguments={"code": "x = 1\\ny = 2", "description": "Run invalid source"},
        )
        for n in range(MAX_SCRIPTED_REQUESTS)
    ]
    result, _log, provider = run_scenario(
        tmp_path, calls, CodeResult, max_turns=MAX_SCRIPTED_REQUESTS
    )
    assert result.task_status is TaskStatus.FAILED  # noqa: S101  # diagnostic property probe
    assert len(provider.calls) == EXPECTED_REQUESTS_BEFORE_STOP  # noqa: S101  # diagnostic property probe


def test_real_evaluation_schema_does_not_complete_after_tool_failure(tmp_path: Path) -> None:
    """Keep a failed evaluation from being declared successful by final output."""
    tool = FakeTool(
        "evaluate_model",
        ToolCapability(id="evaluate_model", external_effects=()),
        result=ToolResult(success=False, error="model evaluation failed", exit_code=1),
    )
    result, _log, _provider = run_scenario(
        tmp_path,
        [
            LLMToolCall(id="failed-eval", name="evaluate_model", arguments={}),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={
                    "summary": "Evaluation succeeded",
                    "metrics_artifact_id": "artifact_claimed",
                },
            ),
        ],
        EvaluationResult,
        allowlist=("evaluate_model",),
        custom_registry=ToolRegistry((tool,)),
    )
    assert result.task_status is TaskStatus.FAILED  # noqa: S101  # diagnostic property probe


def test_tool_capable_structured_retry_preserves_field_feedback(tmp_path: Path) -> None:
    """Expose validation feedback to a scripted repair after malformed output."""
    observed: list[LLMMessage] = []

    def answer_after_retry(messages: Sequence[LLMMessage]) -> LLMToolCall:
        """Return a repaired response after recording the messages shown to the model."""
        observed.extend(messages)
        return final_code("repaired")

    result, _log, _provider = run_scenario(
        tmp_path,
        [
            LLMToolCall(
                id="invalid-final", name="final_result", arguments={"stdout": "missing exit code"}
            ),
            answer_after_retry,
        ],
        CodeResult,
    )
    feedback = [
        message.content for message in observed if "invalid CodeResult output" in message.content
    ]
    assert result.task_status is TaskStatus.COMPLETED  # noqa: S101  # diagnostic property probe
    assert feedback  # noqa: S101  # diagnostic property probe
    assert "exit_code" in feedback[-1]  # noqa: S101  # diagnostic property probe
