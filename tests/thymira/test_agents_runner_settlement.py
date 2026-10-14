"""Runtime failure evidence remains part of every agent output settlement."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

import pytest
from PIL import Image

from tests.thymira.fixtures_agent_output import DataProfileOutput
from tests.thymira.fixtures_tools import (
    EchoArguments,
    FakeTool,
    development_policy,
    review_gated_context,
)
from thymira.agents import AgentCatalog, AgentContext, AgentRunner, AgentSpec, LLMToolCall
from thymira.agents.llm.routing import Role
from thymira.agents.llm.scripted import ScriptedProvider
from thymira.agents.resume import ResumeStatus, load_pending_resume
from thymira.agents.retry_policy import retry_stop_for_task
from thymira.agents.task_completion import (
    assess_task_completion,
    declared_outputs_for_attempt,
    tool_attempts_for_task,
)
from thymira.events import InMemoryEventLog, JsonlEventLog
from thymira.policies import Gate, GateMode, PolicyEngine, RiskProfile, ToolCapability
from thymira.schemas import (
    Actor,
    ActorKind,
    ArtifactKind,
    EventSurface,
    EventType,
    ModelRoutePolicy,
    SandboxEnforcement,
    SandboxMode,
    Task,
    TaskStatus,
    ToolCallStatus,
    new_id,
)
from thymira.state import LocalArtifactStore
from thymira.thy.agents.coding import CodeResult
from thymira.thy.agents.evaluation import EvaluationResult
from thymira.tools import Tool, ToolContext, ToolInvocation, ToolRegistry, ToolResult
from thymira.tools.builtins.files import WriteFile
from thymira.tools.builtins.python_arguments import RunPythonOutputArtifact
from thymira.tools.builtins.run_python import PythonExecutionValue, RunPython
from thymira.tools.result_evidence import (
    recordable_publication_progress_fields,
    recordable_terminal_result,
)
from thymira.tools.results import (
    ModelEvaluationValue,
    PythonPublicationProgress,
    ToolFailureValue,
    ToolResultValidationError,
    canonical_result,
    validate_tool_result,
)
from thymira.tools.sandbox import Sandbox, SandboxRun, StagedInput

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from pydantic import BaseModel

    from thymira.schemas import Artifact, Event
    from thymira.state import ArtifactStore


@pytest.fixture(autouse=True)
def _configured_test_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")


class _NoPublicationSandbox:
    """Return a successful process result without creating declared files."""

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, workspace, timeout_s, env, staged_inputs
        return SandboxRun(
            stdout="interpreter succeeded",
            stderr="",
            exit_code=0,
            mode=SandboxMode.DANGER_FULL_ACCESS,
            enforcement=SandboxEnforcement.PARTIAL,
        )


class _ArtifactPublishingSandbox:
    """Materialize the declared fixture output when a validated script reaches the sandbox."""

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, timeout_s, env, staged_inputs
        (workspace / "a.txt").write_text("published output\n", encoding="utf-8")
        return SandboxRun(
            stdout="published output",
            stderr="",
            exit_code=0,
            mode=mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


class _ProgressPublicationSandbox:
    """Publish a partial set, then remove it to exercise monotonic progress accounting."""

    def __init__(self, *, restore_on_second_call: bool = False) -> None:
        self.calls = 0
        self.restore_on_second_call = restore_on_second_call

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, timeout_s, env, staged_inputs
        self.calls += 1
        output = workspace / "a.txt"
        if self.calls == 2 or (self.restore_on_second_call and self.calls >= 2):
            output.write_text("published output\n", encoding="utf-8")
        elif self.calls >= 3:
            output.unlink(missing_ok=True)
        return SandboxRun(
            stdout="publication attempt",
            stderr="",
            exit_code=0,
            mode=mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


class _OscillatingPublicationSandbox:
    """Expose one reduction in missing outputs, then regress a prior obligation."""

    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, timeout_s, env, staged_inputs
        self.calls += 1
        for name in ("a.txt", "b.txt", "c.txt"):
            (workspace / name).unlink(missing_ok=True)
        if self.calls == 2:
            (workspace / "a.txt").write_text("published A\n", encoding="utf-8")
        elif self.calls == 3:
            (workspace / "b.txt").write_text("published B\n", encoding="utf-8")
        return SandboxRun(
            stdout="publication attempt",
            stderr="",
            exit_code=0,
            mode=mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


@pytest.mark.parametrize("boundary", ["producer", "manager"])
@pytest.mark.parametrize("malformation", ["construct", "copy", "combined_limit"])
def test_malformed_python_publication_progress_is_rejected_at_both_boundaries(
    boundary: str, malformation: str
) -> None:
    valid = PythonPublicationProgress(missing=(("missing.txt", ArtifactKind.OTHER),))
    if malformation == "construct":
        progress = PythonPublicationProgress.model_construct(
            missing=(("", ArtifactKind.OTHER),), present=()
        )
    elif malformation == "copy":
        progress = valid.model_copy(update={"missing": (("", ArtifactKind.OTHER),)})
    else:
        progress = PythonPublicationProgress.model_construct(
            missing=tuple((f"missing-{index}.txt", ArtifactKind.OTHER) for index in range(64)),
            present=(("present.txt", ArtifactKind.OTHER),),
        )
    result = ToolResult(
        success=False,
        value=(
            PythonExecutionValue(stdout="executed", stderr="", exit_code=0)
            if boundary == "producer"
            else ToolFailureValue(text="publication failed", error="publication failed")
        ),
        stdout="executed",
        exit_code=0,
        error="declared output was missing",
        publication_progress=progress,
    )
    tool = RunPython(sandbox=cast("Sandbox", _NoPublicationSandbox()))

    def validate_boundary() -> None:
        if boundary == "producer":
            validate_tool_result(tool, result)
        else:
            recordable_publication_progress_fields("run_python", result)

    with pytest.raises(ToolResultValidationError):
        validate_boundary()


def test_copied_progress_partition_mismatch_fails_producer_and_manager_boundaries(
    tmp_path: Path,
) -> None:
    declarations = (
        RunPythonOutputArtifact(path="reports/a.txt"),
        RunPythonOutputArtifact(path="reports/b.txt"),
    )
    progress = PythonPublicationProgress(missing=(("reports/a.txt", ArtifactKind.OTHER),))
    result = ToolResult(
        success=False,
        value=PythonExecutionValue(stdout="executed", stderr="", exit_code=0),
        stdout="executed",
        exit_code=0,
        error="declared output was missing",
        publication_progress=progress.model_copy(),
        output_declarations=declarations,
    )
    tool = RunPython(sandbox=cast("Sandbox", _NoPublicationSandbox()))
    arguments = {
        "code": "print('run')",
        "output_artifacts": [
            {"path": "reports/a.txt", "kind": "other"},
            {"path": "reports/b.txt", "kind": "other"},
        ],
    }
    invocation = ToolInvocation(
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        workspace=tmp_path / "workspace",
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", new_id("run")),
    )

    with pytest.raises(ToolResultValidationError):
        validate_tool_result(tool, result)
    with pytest.raises(ToolResultValidationError):
        recordable_terminal_result(
            "run_python",
            result,
            tool=cast("Tool", tool),
            arguments=arguments,
            invocation=invocation,
        )


class _ProcessThenPublicationFailureSandbox:
    """Fail once in the interpreter, then succeed without producing a declared output."""

    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, workspace, timeout_s, env, staged_inputs
        self.calls += 1
        exit_code = 1 if self.calls == 1 else 0
        return SandboxRun(
            stdout="process failed" if exit_code else "interpreter succeeded",
            stderr="error" if exit_code else "",
            exit_code=exit_code,
            mode=mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


class _DynamicManifestSandbox:
    """Write fresh manifests and selected logical outputs for deterministic publication tests."""

    def __init__(
        self,
        *,
        publish_calls: frozenset[int] = frozenset(),
        declarations_by_call: dict[int, tuple[str, ...]] | None = None,
        published_paths_by_call: dict[int, tuple[str, ...]] | None = None,
        removed_paths_by_call: dict[int, tuple[str, ...]] | None = None,
    ) -> None:
        self.calls = 0
        self.publish_calls = publish_calls
        self.declarations_by_call = declarations_by_call or {}
        self.published_paths_by_call = published_paths_by_call or {}
        self.removed_paths_by_call = removed_paths_by_call or {}

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, timeout_s, env, staged_inputs
        self.calls += 1
        manifest = workspace / f"outputs-{self.calls}.json"
        declared = self.declarations_by_call.get(self.calls, ("reports/summary.txt",))
        manifest.write_text(
            json.dumps([{"path": path, "kind": "other"} for path in declared]),
            encoding="utf-8",
        )
        published = self.published_paths_by_call.get(self.calls)
        if published is None and self.calls in self.publish_calls:
            published = ("reports/summary.txt",)
        for name in self.removed_paths_by_call.get(self.calls, ()):
            (workspace / name).unlink(missing_ok=True)
        for name in published or ():
            output = workspace / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(f"fresh {name.rsplit('/', 1)[-1].split('.', 1)[0]}\n".encode())
        return SandboxRun(
            stdout="dynamic manifest execution",
            stderr="",
            exit_code=0,
            mode=mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


class _ManifestCorrectionSandbox:
    """Make a generic publication failure, then publish its full corrected declaration."""

    def __init__(self, *, path: str, kind: str, invalid_first: bytes | None, corrected: bytes):
        self.path = path
        self.kind = kind
        self.invalid_first = invalid_first
        self.corrected = corrected
        self.calls = 0

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, timeout_s, env, staged_inputs
        self.calls += 1
        manifest = workspace / f"outputs-{self.calls}.json"
        manifest.write_text(json.dumps([{"path": self.path, "kind": self.kind}]), encoding="utf-8")
        produced = self.invalid_first if self.calls == 1 else self.corrected
        if produced is not None:
            output = workspace / self.path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(produced)
        return SandboxRun(
            stdout="dynamic manifest execution",
            stderr="",
            exit_code=0,
            mode=mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


def _run_agent(
    tmp_path: Path,
    schema: type[BaseModel],
    responses: Sequence[LLMToolCall],
    *,
    tool_allowlist: tuple[str, ...] = ("run_python",),
    registry: ToolRegistry | None = None,
    allowed_tools: frozenset[str] | None = None,
    registered_source: tuple[str, bytes] | None = None,
    source_response_factory: Callable[[Artifact], Sequence[LLMToolCall]] | None = None,
    provider_capture: list[ScriptedProvider] | None = None,
) -> tuple[TaskStatus, InMemoryEventLog, LocalArtifactStore]:
    run_id = new_id("run")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    task = Task(
        id=new_id("task"),
        run_id=run_id,
        agent_id=new_id("agent"),
        objective="Complete the requested analysis.",
    )
    source = (
        store.save_bytes(
            registered_source[0],
            registered_source[1],
            produced_by=task.agent_id,
            kind=ArtifactKind.CODE,
            media_type="text/x-python",
        )
        if registered_source is not None
        else None
    )
    if source_response_factory is not None:
        assert source is not None
        responses = source_response_factory(source)
    tool_context = ToolContext(
        run_id=run_id,
        agent_id=task.agent_id,
        task_id=task.id,
        workspace=tmp_path / "workspace",
        event_log=log,
        gate=Gate(PolicyEngine(development_policy()), log),
        artifact_store=store,
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=1.0
        ),
        allowed_tools=allowed_tools,
    )
    spec = AgentSpec(
        name="coding",
        role=Role.AGENT,
        task_kinds=("code",),
        tool_allowlist=tool_allowlist,
        max_turns=4,
        max_depth=1,
        system_prompt_ref="coding",
        output_schema_ref="tests.thymira.fixtures_agent_output:DataProfileOutput",
    )
    catalog = AgentCatalog(
        (spec,),
        system_prompts={"coding": "Complete the requested analysis."},
        output_schemas={"coding": schema},
    )
    provider = ScriptedProvider(responses)
    if provider_capture is not None:
        provider_capture.append(provider)
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
            tool_registry=registry
            or ToolRegistry(
                (
                    cast(
                        "Tool",
                        RunPython(
                            sandbox=cast("Sandbox", _NoPublicationSandbox()),
                            mode=SandboxMode.DANGER_FULL_ACCESS,
                        ),
                    ),
                )
            ),
            tool_context=tool_context,
        ),
    )
    assert log.verify().valid
    assert store.verify() == []
    return result.task_status, log, store


def _missing_output(call_id: str, path: str) -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        name="run_python",
        arguments={
            "code": f"print('{call_id}')",
            "description": f"Attempt output {path}",
            "output_artifacts": [{"path": path}],
        },
    )


def _missing_outputs(call_id: str, *paths: str) -> LLMToolCall:
    return LLMToolCall(
        id=call_id,
        name="run_python",
        arguments={
            "code": f"print('{call_id}')",
            "description": f"Attempt outputs {paths}",
            "output_artifacts": [{"path": path} for path in paths],
        },
    )


def test_same_missing_python_outputs_stop_before_a_third_model_request(tmp_path: Path) -> None:
    providers: list[ScriptedProvider] = []
    status, log, store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_output("missing-first", "required.txt"),
            _missing_output("missing-second", "required.txt"),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "complete", "exit_code": 0, "artifacts": []},
            ),
        ],
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 2
    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED
        and event.payload.get("tool") == "run_python"
        and event.payload.get("status") == ToolCallStatus.FAILED.value
    ]
    assert len(failures) == 2
    assert all(event.payload.get("exit_code") == 0 for event in failures)
    assert not any(
        event.type is EventType.AGENT_COMPLETED
        and event.payload.get("status") == TaskStatus.COMPLETED.value
        for event in log.events()
    )
    assert store.verify() == []


def test_read_only_call_does_not_reset_the_same_publication_failure_count(
    tmp_path: Path,
) -> None:
    read_tool = FakeTool(
        "read_file",
        ToolCapability(id="read_file", external_effects=()),
        arguments_model=EchoArguments,
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", _NoPublicationSandbox()),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
            read_tool,
        )
    )
    providers: list[ScriptedProvider] = []
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_output("missing-before-read", "required.txt"),
            LLMToolCall(
                id="read-only",
                name="read_file",
                arguments={"value": "inspect current evidence"},
            ),
            _missing_output("missing-after-read", "required.txt"),
            LLMToolCall(
                id="unrequested-third-python",
                name="run_python",
                arguments={
                    "code": "print('third')",
                    "description": "Try the same missing output a third time",
                    "output_artifacts": [{"path": "required.txt"}],
                },
            ),
        ],
        tool_allowlist=("run_python", "read_file"),
        registry=registry,
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 3
    read_events = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "read_file"
    ]
    assert len(read_events) == 1
    assert read_events[0].payload["status"] == ToolCallStatus.COMPLETED.value


def test_distinct_python_output_destinations_have_independent_retry_groups(tmp_path: Path) -> None:
    providers: list[ScriptedProvider] = []
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_output("destination-a", "a.txt"),
            _missing_output("destination-b", "b.txt"),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            ),
        ],
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 3
    starts = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "run_python"
    ]
    assert [event.payload["arguments"]["output_artifacts"][0]["path"] for event in starts] == [
        "a.txt",
        "b.txt",
    ]


def test_changed_python_source_does_not_reset_same_missing_output_budget(tmp_path: Path) -> None:
    providers: list[ScriptedProvider] = []
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_output("source-one", "required.txt"),
            _missing_output("source-two", "required.txt"),
            LLMToolCall(
                id="unrequested-third",
                name="final_result",
                arguments={"stdout": "complete", "exit_code": 0, "artifacts": []},
            ),
        ],
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 2
    assert (
        len(
            [
                event
                for event in log.events()
                if event.type is EventType.TOOL_COMPLETED
                and event.payload.get("tool") == "run_python"
            ]
        )
        == 2
    )


def test_partial_publication_grants_one_monotonic_attempt_then_stops(
    tmp_path: Path,
) -> None:
    sandbox = _ProgressPublicationSandbox()
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(sandbox=cast("Sandbox", sandbox), mode=SandboxMode.DANGER_FULL_ACCESS),
            ),
        )
    )
    providers: list[ScriptedProvider] = []
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_outputs("missing-all", "a.txt", "b.txt"),
            _missing_outputs("partial-a", "a.txt", "b.txt"),
            _missing_outputs("regressed-all", "a.txt", "b.txt"),
            LLMToolCall(
                id="unrequested-fourth",
                name="final_result",
                arguments={"stdout": "complete", "exit_code": 0, "artifacts": []},
            ),
        ],
        registry=registry,
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 3
    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert len(failures) == 3
    assert failures[0].payload["publication_progress"] == {
        "missing": [["a.txt", "other"], ["b.txt", "other"]],
        "present": [],
    }
    assert failures[1].payload["publication_progress"] == {
        "missing": [["b.txt", "other"]],
        "present": [["a.txt", "other"]],
    }
    assert failures[2].payload["publication_progress"] == {
        "missing": [["a.txt", "other"], ["b.txt", "other"]],
        "present": [],
    }
    assert all(event.payload["value"]["kind"] == "failure" for event in failures)
    assert not any(
        event.type is EventType.AGENT_COMPLETED
        and event.payload.get("status") == TaskStatus.COMPLETED.value
        for event in log.events()
    )


def test_missing_output_progress_oscillation_does_not_grant_another_band(
    tmp_path: Path,
) -> None:
    sandbox = _OscillatingPublicationSandbox()
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(sandbox=cast("Sandbox", sandbox), mode=SandboxMode.DANGER_FULL_ACCESS),
            ),
        )
    )
    providers: list[ScriptedProvider] = []
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_outputs("missing-all", "a.txt", "b.txt", "c.txt"),
            _missing_outputs("published-a", "a.txt", "b.txt", "c.txt"),
            _missing_outputs("regressed-b", "a.txt", "b.txt", "c.txt"),
            LLMToolCall(
                id="unrequested-fourth",
                name="final_result",
                arguments={"stdout": "complete", "exit_code": 0, "artifacts": []},
            ),
        ],
        registry=registry,
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 3
    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert [event.payload["publication_progress"]["missing"] for event in failures] == [
        [["a.txt", "other"], ["b.txt", "other"], ["c.txt", "other"]],
        [["b.txt", "other"], ["c.txt", "other"]],
        [["a.txt", "other"], ["c.txt", "other"]],
    ]


def test_valid_python_publication_repair_completes_through_normal_assessment(
    tmp_path: Path,
) -> None:
    sandbox = _ProgressPublicationSandbox(restore_on_second_call=True)
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(sandbox=cast("Sandbox", sandbox), mode=SandboxMode.DANGER_FULL_ACCESS),
            ),
        )
    )
    status, log, store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_output("missing-first", "a.txt"),
            _missing_output("published-repair", "a.txt"),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "published", "exit_code": 0, "artifacts": ["a.txt"]},
            ),
        ],
        registry=registry,
    )

    assert status is TaskStatus.COMPLETED
    assert store.get("a.txt") is not None
    assert sum(event.type is EventType.TOOL_COMPLETED for event in log.events()) == 2
    completion = next(
        event for event in reversed(log.events()) if event.type is EventType.AGENT_COMPLETED
    )
    assert completion.payload["completion_evidence"]["repaired_tool_call_ids"]


@pytest.mark.parametrize(
    ("repair_mode", "should_repair"),
    [
        ("canonical_success", True),
        ("failure_envelope", False),
        ("other_task", False),
        ("foreign_result_id", False),
    ],
)
def test_evaluation_repair_requires_canonical_task_owned_metrics_publication(
    tmp_path: Path, repair_mode: str, should_repair: bool
) -> None:
    run_id = new_id("run")
    task_id = new_id("task")
    other_task_id = new_id("task")
    agent_id = new_id("agent")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    metrics = store.save_json(
        "metrics/evaluation.json",
        {"accuracy": 0.8},
        produced_by=agent_id,
        kind=ArtifactKind.METRICS,
        media_type="application/json",
    )
    arguments = {
        "model_artifact": new_id("artifact"),
        "dataset": "datasets/validated.csv",
        "target_column": "label",
        "split": "test",
        "evaluation_context_artifact": None,
    }
    failed_value = canonical_result(ToolFailureValue(error="evaluation failed"), success=False)
    log.append(
        EventType.TOOL_STARTED,
        Actor.system(),
        {
            "task_id": task_id,
            "tool_call_id": "evaluation-failed",
            "tool": "evaluate_model",
            "arguments": arguments,
        },
        subject_id="evaluation-failed",
    )
    log.append(
        EventType.TOOL_COMPLETED,
        Actor.system(),
        {
            "task_id": task_id,
            "tool_call_id": "evaluation-failed",
            "tool": "evaluate_model",
            "status": ToolCallStatus.FAILED.value,
            "error": "evaluation failed",
            "artifact_ids": [],
            "value": failed_value,
        },
        subject_id="evaluation-failed",
    )

    repair_task_id = other_task_id if repair_mode == "other_task" else task_id
    result_artifact_id = new_id("artifact") if repair_mode == "foreign_result_id" else metrics.id
    if repair_mode == "failure_envelope":
        repair_value = failed_value
    else:
        repair_value = canonical_result(
            ModelEvaluationValue(
                model_artifact=arguments["model_artifact"],
                dataset=arguments["dataset"],
                split=arguments["split"],
                metrics={"accuracy": 0.8},
                evaluated_rows=10,
                averaging="binary",
                artifact_id=result_artifact_id,
            ),
            success=True,
        )
    log.append(
        EventType.TOOL_STARTED,
        Actor.system(),
        {
            "task_id": repair_task_id,
            "tool_call_id": "evaluation-repair",
            "tool": "evaluate_model",
            "arguments": arguments,
        },
        subject_id="evaluation-repair",
    )
    log.append(
        EventType.TOOL_COMPLETED,
        Actor.system(),
        {
            "task_id": repair_task_id,
            "tool_call_id": "evaluation-repair",
            "tool": "evaluate_model",
            "status": ToolCallStatus.COMPLETED.value,
            "artifact_ids": [metrics.id],
            "value": repair_value,
        },
        subject_id="evaluation-repair",
    )

    evidence = assess_task_completion(log.events(), task_id, store)

    assert (evidence.repaired_tool_call_ids == ("evaluation-failed",)) is should_repair
    assert (not evidence.unresolved_failures) is should_repair
    assert (metrics.id in evidence.verified_artifact_ids) is should_repair
    assert log.verify().valid
    assert store.verify() == []


def test_process_failure_is_not_counted_as_a_publication_failure(tmp_path: Path) -> None:
    sandbox = _ProcessThenPublicationFailureSandbox()
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(sandbox=cast("Sandbox", sandbox), mode=SandboxMode.DANGER_FULL_ACCESS),
            ),
        )
    )
    providers: list[ScriptedProvider] = []
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_output("process-failure", "required.txt"),
            _missing_output("publication-failure-one", "required.txt"),
            _missing_output("publication-failure-two", "required.txt"),
            LLMToolCall(
                id="unrequested-third-publication",
                name="final_result",
                arguments={"stdout": "complete", "exit_code": 0, "artifacts": []},
            ),
        ],
        registry=registry,
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 3
    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert [event.payload["exit_code"] for event in failures] == [1, 0, 0]
    assert "publication_progress" not in failures[0].payload
    assert all("publication_progress" in event.payload for event in failures[1:])


def test_same_registered_python_syntax_failure_stops_without_execution_or_approval(
    tmp_path: Path,
) -> None:
    providers: list[ScriptedProvider] = []

    def repeated_registered_source(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"registered-syntax-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Execute the same registered source revision.",
                },
            )
            for index in (1, 2)
        ] + [
            LLMToolCall(
                id="unrequested-final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registered_source=("analysis.py", b"print("),
        source_response_factory=repeated_registered_source,
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 2
    assert not any(
        event.type
        in {
            EventType.TOOL_STARTED,
            EventType.POLICY_DECISION,
            EventType.HUMAN_APPROVAL,
            EventType.HUMAN_APPROVAL_REQUESTED,
        }
        for event in log.events()
    )
    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert len(failures) == 2
    assert all(event.payload["exit_code"] is None for event in failures)


def test_python_retry_counts_are_isolated_by_task_within_one_run(tmp_path: Path) -> None:
    run_id = new_id("run")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    spec = AgentSpec(
        name="coding",
        role=Role.AGENT,
        task_kinds=("code",),
        tool_allowlist=("run_python",),
        max_turns=4,
        max_depth=1,
        system_prompt_ref="coding",
        output_schema_ref="tests.thymira.fixtures_agent_output:DataProfileOutput",
    )
    catalog = AgentCatalog(
        (spec,),
        system_prompts={"coding": "Run the requested Python source."},
        output_schemas={"coding": CodeResult},
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", _NoPublicationSandbox()),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )

    def run_task(task: Task, provider: ScriptedProvider) -> TaskStatus:
        tool_context = ToolContext(
            run_id=run_id,
            agent_id=task.agent_id,
            task_id=task.id,
            workspace=tmp_path / "workspace",
            event_log=log,
            gate=Gate(PolicyEngine(development_policy()), log),
            artifact_store=store,
            risk_profile=RiskProfile(
                risk_level="limited", activity_category="analysis", confidence=1.0
            ),
        )
        return (
            AgentRunner()
            .run(
                spec,
                task,
                AgentContext(
                    route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="tests"),
                    catalog=catalog,
                    event_log=log,
                    provider=provider,
                    tool_registry=registry,
                    tool_context=tool_context,
                ),
            )
            .task_status
        )

    first_task = Task(
        id=new_id("task"),
        run_id=run_id,
        agent_id=new_id("agent"),
        objective="Run the same broken source in task one.",
    )
    second_task = Task(
        id=new_id("task"),
        run_id=run_id,
        agent_id=new_id("agent"),
        objective="Run the same broken source in task two.",
    )

    def responses(call_id: str) -> list[LLMToolCall]:
        return [
            LLMToolCall(
                id=call_id,
                name="run_python",
                arguments={"code": "print(", "description": "Run broken source."},
            ),
            LLMToolCall(
                id=f"{call_id}-final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            ),
        ]

    first_provider = ScriptedProvider(responses("task-one-call"))
    second_provider = ScriptedProvider(responses("task-two-call"))
    assert run_task(first_task, first_provider) is TaskStatus.FAILED
    assert run_task(second_task, second_provider) is TaskStatus.FAILED
    assert len(first_provider.calls) == 2
    assert len(second_provider.calls) == 2
    assert [
        event.payload["task_id"]
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ] == [first_task.id, second_task.id]


def test_publication_retry_evidence_survives_jsonl_reopen_and_malformed_progress_is_ignored(
    tmp_path: Path,
) -> None:
    run_id = new_id("run")
    task = Task(
        id=new_id("task"),
        run_id=run_id,
        agent_id=new_id("agent"),
        objective="Publish the required output.",
    )
    event_path = tmp_path / "events.jsonl"
    workspace = tmp_path / "workspace"
    artifact_root = tmp_path / "artifacts"
    log = JsonlEventLog(event_path, run_id)
    store = LocalArtifactStore(artifact_root, run_id)
    spec = AgentSpec(
        name="coding",
        role=Role.AGENT,
        task_kinds=("code",),
        tool_allowlist=("run_python",),
        max_turns=4,
        max_depth=1,
        system_prompt_ref="coding",
        output_schema_ref="tests.thymira.fixtures_agent_output:DataProfileOutput",
    )
    catalog = AgentCatalog(
        (spec,),
        system_prompts={"coding": "Publish the required output."},
        output_schemas={"coding": CodeResult},
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", _NoPublicationSandbox()),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )

    def context(current_log: JsonlEventLog, current_store: LocalArtifactStore) -> ToolContext:
        return ToolContext(
            run_id=run_id,
            agent_id=task.agent_id,
            task_id=task.id,
            workspace=workspace,
            event_log=current_log,
            gate=Gate(PolicyEngine(development_policy()), current_log),
            artifact_store=current_store,
            risk_profile=RiskProfile(
                risk_level="limited", activity_category="analysis", confidence=1.0
            ),
        )

    first_provider = ScriptedProvider(
        [
            _missing_output("missing-first", "required.txt"),
            _missing_output("missing-second", "required.txt"),
            LLMToolCall(
                id="unrequested-third",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            ),
        ]
    )
    first_result = AgentRunner().run(
        spec,
        task,
        AgentContext(
            route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="tests"),
            catalog=catalog,
            event_log=log,
            provider=first_provider,
            tool_registry=registry,
            tool_context=context(log, store),
        ),
    )
    assert first_result.task_status is TaskStatus.FAILED
    assert len(first_provider.calls) == 2
    terminal = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ][-1]
    assert terminal.payload["publication_progress"] == {
        "missing": [["required.txt", "other"]],
        "present": [],
    }
    assert terminal.payload["value"]["kind"] == "failure"
    shadowed_messages = [
        event.seq for event in log.events() if event.surface is EventSurface.MODEL_VISIBLE
    ]
    log.append(
        EventType.CONTEXT_COMPACTED,
        Actor.system(),
        {
            "text": "Prior Python publication attempts were compacted into this summary.",
            "shadowed_seqs": shadowed_messages,
        },
        surface=EventSurface.MODEL_VISIBLE,
    )
    assert log.verify().valid

    reopened_log = JsonlEventLog(event_path, run_id)
    reopened_store = LocalArtifactStore(artifact_root, run_id)
    assert reopened_log.verify().valid
    assert reopened_store.verify() == []
    reopened_terminal = next(
        event
        for event in reversed(reopened_log.events())
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    )
    assert (
        reopened_terminal.payload["publication_progress"]
        == terminal.payload["publication_progress"]
    )
    second_provider = ScriptedProvider(
        [
            LLMToolCall(
                id="unrequested-after-reopen",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]
    )
    second_result = AgentRunner().run(
        spec,
        task,
        AgentContext(
            route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="tests"),
            catalog=catalog,
            event_log=reopened_log,
            provider=second_provider,
            tool_registry=registry,
            tool_context=context(reopened_log, reopened_store),
        ),
    )
    assert second_result.task_status is TaskStatus.FAILED
    assert second_provider.calls == []

    malformed = []
    malformed_count = 0
    for original_event in reopened_log.events():
        event = original_event
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python":
            payload = dict(event.payload)
            if malformed_count == 0:
                payload.pop("publication_progress", None)
            else:
                payload["publication_progress"] = {
                    "missing": [],
                    "present": [["required.txt", "other"]],
                }
            event = event.model_copy(update={"payload": payload})
            malformed_count += 1
        malformed.append(event)
    stop = retry_stop_for_task(
        malformed,
        task.id,
        artifact_store=reopened_store,
        tool_registry=None,
        tool_context=None,
    )
    assert stop is not None
    assert stop.kind == "publication"
    assert (
        stop.terminal_seq
        == [
            event.seq
            for event in reopened_log.events()
            if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
        ][1]
    )


def test_independent_python_success_does_not_clear_a_missing_required_output(
    tmp_path: Path,
) -> None:
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            _missing_output("failed-a", "source_a.txt"),
            LLMToolCall(
                id="independent-b",
                name="run_python",
                arguments={"code": "print('independent B')", "description": "Explore B"},
            ),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            ),
        ],
    )

    assert status is TaskStatus.FAILED
    calls = [event for event in log.events() if event.type is EventType.TOOL_COMPLETED]
    assert [
        (
            event.payload["status"].value
            if hasattr(event.payload["status"], "value")
            else event.payload["status"]
        ).lower()
        for event in calls
    ] == ["failed", "completed"]


def test_manifest_backed_python_failure_cannot_be_cleared_by_outputless_run(
    tmp_path: Path,
) -> None:
    """A registered manifest-backed source failure survives a successful no-output rerun."""

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id="manifest-a",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish manifest-backed source outputs",
                    "output_manifest_path": "outputs.json",
                },
            ),
            LLMToolCall(
                id="same-source-no-output",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Rerun the same registered source without declared outputs",
                },
            ),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            ),
        ]

    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registered_source=("analysis.py", b"print('registered source')"),
        source_response_factory=source_calls,
    )

    assert status is TaskStatus.FAILED
    attempts = [event for event in log.events() if event.type is EventType.TOOL_COMPLETED]
    assert len(attempts) == 2
    started = [event for event in log.events() if event.type is EventType.TOOL_STARTED]
    assert started[0].payload["arguments"]["output_manifest_path"] == "outputs.json"
    assert started[0].payload["arguments"]["source_artifact_name"] == "analysis.py"
    assert (
        started[0].payload["arguments"]["source_artifact_id"]
        == started[1].payload["arguments"]["source_artifact_id"]
    )
    first_status = getattr(attempts[0].payload["status"], "value", attempts[0].payload["status"])
    second_status = getattr(attempts[1].payload["status"], "value", attempts[1].payload["status"])
    assert first_status == "FAILED"
    assert second_status == "COMPLETED"


def test_registered_dynamic_manifest_failure_is_repaired_by_full_publication(
    tmp_path: Path,
) -> None:
    """A later successful call publishes the exact outputs declared by the failed manifest."""
    logical_outputs = ("reports/summary.txt", "reports/details.txt")
    sandbox = _DynamicManifestSandbox(
        declarations_by_call={1: logical_outputs, 2: logical_outputs},
        published_paths_by_call={1: ("reports/summary.txt",), 2: logical_outputs},
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", sandbox),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id="manifest-failure",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish the registered analysis output",
                    "output_manifest_path": "outputs-1.json",
                },
            ),
            LLMToolCall(
                id="manifest-repair",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Correct the registered analysis publication",
                    "output_manifest_path": "outputs-2.json",
                },
            ),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "published", "exit_code": 0, "artifacts": []},
            ),
        ]

    status, log, store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registry=registry,
        registered_source=("analysis.py", b"print('registered analysis')\n"),
        source_response_factory=source_calls,
    )

    terminals = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert len(terminals) == 2
    assert terminals[0].payload["exit_code"] == 0
    assert terminals[0].payload["value"]["kind"] == "failure"
    assert terminals[0].payload["artifact_ids"] == []
    assert terminals[0].payload["publication_progress"] == {
        "missing": [["reports/details.txt", "other"]],
        "present": [["reports/summary.txt", "other"]],
    }
    assert terminals[0].payload["output_declarations"] == [
        {"path": "reports/summary.txt", "kind": "other"},
        {"path": "reports/details.txt", "kind": "other"},
    ]
    attempts = tool_attempts_for_task(log.events(), terminals[0].payload["task_id"])
    assert declared_outputs_for_attempt(attempts[0]) == (
        ("reports/summary.txt", "other"),
        ("reports/details.txt", "other"),
    )
    assert status is TaskStatus.COMPLETED
    assert terminals[1].payload["status"] == ToolCallStatus.COMPLETED.value
    for name, body in (
        ("reports/summary.txt", b"fresh summary\n"),
        ("reports/details.txt", b"fresh details\n"),
    ):
        published = store.get(name)
        assert published is not None
        assert published.kind is ArtifactKind.OTHER
        assert published.sha256 == hashlib.sha256(body).hexdigest()
        initial_arguments = attempts[0].arguments
        assert initial_arguments is not None
        assert published.input_artifact_ids == (initial_arguments["source_artifact_id"],)
    completion = next(
        event for event in reversed(log.events()) if event.type is EventType.AGENT_COMPLETED
    )
    assert completion.payload["completion_evidence"]["repaired_tool_call_ids"]


@pytest.mark.parametrize("failure_mode", ["stale", "invalid_content"])
def test_generic_manifest_publication_failure_keeps_obligations_for_full_repair(
    tmp_path: Path, failure_mode: str
) -> None:
    """A valid manifest remains authoritative when atomic publication fails generically."""
    if failure_mode == "stale":
        path, kind, invalid_first, corrected = (
            "reports/summary.txt",
            "other",
            None,
            b"corrected summary\n",
        )
        stale = tmp_path / "workspace" / path
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"untouched prior bytes\n")
    else:
        png = BytesIO()
        Image.new("RGB", (1, 1), color="red").save(png, format="PNG")
        path, kind, invalid_first, corrected = (
            "reports/plot.png",
            "plot",
            b"not a PNG image",
            png.getvalue(),
        )
    sandbox = _ManifestCorrectionSandbox(
        path=path, kind=kind, invalid_first=invalid_first, corrected=corrected
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", sandbox),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"manifest-{failure_mode}-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish the registered output contract",
                    "output_manifest_path": f"outputs-{index}.json",
                },
            )
            for index in (1, 2)
        ] + [
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "published", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registry=registry,
        registered_source=("analysis.py", b"print('registered analysis')\n"),
        source_response_factory=source_calls,
    )

    terminals = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert len(terminals) == 2
    assert terminals[0].payload["status"] == ToolCallStatus.FAILED.value
    assert terminals[0].payload["exit_code"] == 0
    assert terminals[0].payload["value"]["kind"] == "failure"
    assert terminals[0].payload["artifact_ids"] == []
    assert terminals[0].payload["output_declarations"] == [{"path": path, "kind": kind}]
    assert store.get(path) is not None
    assert store.get("outputs-1.json") is None
    assert store.get("outputs-2.json") is not None
    assert status is TaskStatus.COMPLETED


def test_repeated_registered_dynamic_manifest_failure_stops_before_third_request(
    tmp_path: Path,
) -> None:
    """A fresh manifest filename does not reset an unchanged dynamic publication failure."""
    sandbox = _DynamicManifestSandbox()
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", sandbox),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )
    providers: list[ScriptedProvider] = []

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"manifest-failure-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish the same registered analysis output",
                    "output_manifest_path": f"outputs-{index}.json",
                },
            )
            for index in (1, 2)
        ] + [
            LLMToolCall(
                id="unrequested-final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registry=registry,
        registered_source=("analysis.py", b"print('registered analysis')\n"),
        source_response_factory=source_calls,
        provider_capture=providers,
    )

    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert len(failures) == 2
    assert all(event.payload["exit_code"] == 0 for event in failures)
    assert all(event.payload["value"]["kind"] == "failure" for event in failures)
    attempts = tool_attempts_for_task(log.events(), failures[0].payload["task_id"])
    assert declared_outputs_for_attempt(attempts[0]) == (("reports/summary.txt", "other"),)
    assert declared_outputs_for_attempt(attempts[1]) == (("reports/summary.txt", "other"),)
    assert len(providers[0].calls) == 2
    assert status is TaskStatus.FAILED
    retry_stop = retry_stop_for_task(
        log.events(),
        attempts[0].task_id,
        artifact_store=_store,
        tool_registry=None,
        tool_context=None,
    )
    assert retry_stop is not None
    assert retry_stop.kind == "publication"

    event_path = tmp_path / "dynamic-publication.jsonl"
    persisted = JsonlEventLog(event_path, failures[0].run_id)
    for event in log.events():
        persisted.append(
            event.type,
            event.actor,
            event.payload,
            subject_id=event.subject_id,
            surface=event.surface,
        )
    assert persisted.verify().valid
    reopened = JsonlEventLog(event_path, failures[0].run_id)
    reopened_attempts = tool_attempts_for_task(reopened.events(), attempts[0].task_id)
    assert declared_outputs_for_attempt(reopened_attempts[0]) == declared_outputs_for_attempt(
        attempts[0]
    )
    reopened_stop = retry_stop_for_task(
        reopened.events(),
        attempts[0].task_id,
        artifact_store=_store,
        tool_registry=None,
        tool_context=None,
    )
    assert reopened_stop == retry_stop


def test_dynamic_manifest_omission_does_not_reset_publication_retry_boundary(
    tmp_path: Path,
) -> None:
    """A fresh manifest omitting a failed declaration cannot simulate monotonic progress."""
    sandbox = _DynamicManifestSandbox(
        declarations_by_call={
            1: ("reports/summary.txt", "reports/details.txt"),
            2: ("reports/summary.txt",),
        }
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", sandbox),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )
    providers: list[ScriptedProvider] = []

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"omission-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish every declared analysis output",
                    "output_manifest_path": f"outputs-{index}.json",
                },
            )
            for index in (1, 2)
        ] + [
            LLMToolCall(
                id="unrequested-third",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registry=registry,
        registered_source=("analysis.py", b"print('registered analysis')\n"),
        source_response_factory=source_calls,
        provider_capture=providers,
    )

    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert status is TaskStatus.FAILED
    assert len(failures) == 2
    attempts = tool_attempts_for_task(log.events(), failures[0].payload["task_id"])
    assert declared_outputs_for_attempt(attempts[0]) == (
        ("reports/summary.txt", "other"),
        ("reports/details.txt", "other"),
    )
    assert declared_outputs_for_attempt(attempts[1]) == (("reports/summary.txt", "other"),)
    assert len(providers[0].calls) == 2


def test_dynamic_manifest_strict_reduction_is_bounded_after_oscillation(
    tmp_path: Path,
) -> None:
    """A strict dynamic missing-set reduction earns one band; later oscillation earns none."""
    outputs = ("reports/a.txt", "reports/b.txt", "reports/c.txt")
    sandbox = _DynamicManifestSandbox(
        declarations_by_call=dict.fromkeys((1, 2, 3), outputs),
        published_paths_by_call={2: ("reports/a.txt",), 3: ("reports/b.txt",)},
        removed_paths_by_call={3: ("reports/a.txt",)},
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", sandbox),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )
    providers: list[ScriptedProvider] = []

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"dynamic-progress-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish the exact dynamic output set",
                    "output_manifest_path": f"outputs-{index}.json",
                },
            )
            for index in (1, 2, 3)
        ] + [
            LLMToolCall(
                id="unrequested-fourth",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registry=registry,
        registered_source=("analysis.py", b"print('registered analysis')\n"),
        source_response_factory=source_calls,
        provider_capture=providers,
    )

    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert status is TaskStatus.FAILED
    assert len(failures) == 3
    assert [event.payload["publication_progress"]["missing"] for event in failures] == [
        [[name, "other"] for name in outputs],
        [[name, "other"] for name in outputs[1:]],
        [[name, "other"] for name in (outputs[0], outputs[2])],
    ]
    assert len(providers[0].calls) == 3
    assert store.list_active()  # Registered source remains active; no partial output is published.
    assert all(not event.payload["artifact_ids"] for event in failures)


def test_reordered_dynamic_declarations_preserve_a_strict_reduction_band(
    tmp_path: Path,
) -> None:
    """Declaration order does not erase a genuine set reduction in publication progress."""
    outputs = ("reports/a.txt", "reports/b.txt")
    sandbox = _DynamicManifestSandbox(
        declarations_by_call={1: outputs, 2: tuple(reversed(outputs)), 3: outputs},
        published_paths_by_call={2: ("reports/a.txt",), 3: ("reports/a.txt",)},
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", sandbox),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )
    providers: list[ScriptedProvider] = []

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"reordered-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish both outputs",
                    "output_manifest_path": f"outputs-{index}.json",
                },
            )
            for index in (1, 2, 3)
        ] + [
            LLMToolCall(
                id="unrequested-third",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registry=registry,
        registered_source=("analysis.py", b"print('registered analysis')\n"),
        source_response_factory=source_calls,
        provider_capture=providers,
    )
    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert status is TaskStatus.FAILED
    assert len(failures) == 3
    assert failures[1].payload["output_declarations"] == [
        {"path": path, "kind": "other"} for path in reversed(outputs)
    ]
    assert len(providers[0].calls) == 3
    stop = retry_stop_for_task(
        log.events(),
        failures[0].payload["task_id"],
        artifact_store=_store,
        tool_registry=None,
        tool_context=None,
    )
    assert stop is not None
    assert stop.terminal_seq == failures[-1].seq


def test_dynamic_omission_stops_after_source_revision_changes(
    tmp_path: Path,
) -> None:
    """A fresh registered revision cannot reset retries by omitting a failed declaration."""
    run_id = new_id("run")
    task_id = new_id("task")
    agent_id = new_id("agent")
    log = InMemoryEventLog(run_id)
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    declarations = (
        (("reports/summary.txt", "other"), ("reports/details.txt", "other")),
        (("reports/summary.txt", "other"),),
    )
    for index, output_set in enumerate(declarations, start=1):
        manifest = f"outputs-{index}.json"
        log.append(
            EventType.TOOL_STARTED,
            actor,
            {
                "task_id": task_id,
                "tool_call_id": f"revision-{index}",
                "tool": "run_python",
                "arguments": {
                    "source_artifact_id": f"source-revision-{index}",
                    "source_artifact_name": "analysis.py",
                    "source_artifact_sha256": str(index) * 64,
                    "output_manifest_path": manifest,
                },
            },
            subject_id=f"revision-{index}",
        )
        path_kind = [[path, kind] for path, kind in output_set]
        log.append(
            EventType.TOOL_COMPLETED,
            actor,
            {
                "task_id": task_id,
                "tool_call_id": f"revision-{index}",
                "tool": "run_python",
                "status": ToolCallStatus.FAILED,
                "exit_code": 0,
                "artifact_ids": [],
                "error": "publication failed",
                "value": {
                    "kind": "failure",
                    "value": {
                        "text": "publication failed",
                        "error": "publication failed",
                        "code": "TOOL_FAILURE",
                    },
                },
                "output_declarations": [{"path": path, "kind": kind} for path, kind in output_set],
                "publication_progress": {"missing": path_kind, "present": []},
            },
            subject_id=f"revision-{index}",
        )

    stop = retry_stop_for_task(
        log.events(),
        task_id,
        artifact_store=None,
        tool_registry=None,
        tool_context=None,
    )

    assert stop is not None
    assert stop.kind == "publication"
    assert stop.terminal_seq == log.events()[-1].seq


@pytest.mark.parametrize("manifest_case", ["self", "case_self", "malformed"])
def test_reopened_dynamic_manifest_declarations_reject_self_reference_and_bad_paths(
    manifest_case: str,
) -> None:
    """Copied terminal declarations cannot turn the manifest LOG or malformed paths logical."""
    run_id = new_id("run")
    task_id = new_id("task")
    agent_id = new_id("agent")
    log = InMemoryEventLog(run_id)
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    for index in (1, 2):
        manifest = f"outputs-{index}.json"
        declared = (
            "../outside.txt"
            if manifest_case == "malformed"
            else manifest.upper()
            if manifest_case == "case_self"
            else manifest
        )
        descriptor = {"path": declared, "kind": "log"}
        log.append(
            EventType.TOOL_STARTED,
            actor,
            {
                "task_id": task_id,
                "tool_call_id": f"copied-{index}",
                "tool": "run_python",
                "arguments": {
                    "source_artifact_id": "same-source",
                    "code": "print('registered')",
                    "output_manifest_path": manifest,
                },
            },
            subject_id=f"copied-{index}",
        )
        log.append(
            EventType.TOOL_COMPLETED,
            actor,
            {
                "task_id": task_id,
                "tool_call_id": f"copied-{index}",
                "tool": "run_python",
                "status": ToolCallStatus.FAILED,
                "exit_code": 0,
                "artifact_ids": [],
                "error": "copied malformed publication evidence",
                "value": {
                    "kind": "failure",
                    "value": {
                        "text": "copied malformed publication evidence",
                        "error": "copied malformed publication evidence",
                        "code": "TOOL_FAILURE",
                    },
                },
                "output_declarations": [descriptor],
                "publication_progress": {"missing": [[declared, "log"]], "present": []},
            },
            subject_id=f"copied-{index}",
        )

    attempts = tool_attempts_for_task(log.events(), task_id)
    assert attempts
    assert all(declared_outputs_for_attempt(attempt) is None for attempt in attempts)
    assert (
        retry_stop_for_task(
            log.events(), task_id, artifact_store=None, tool_registry=None, tool_context=None
        )
        is None
    )


def test_dynamic_manifest_growth_does_not_grant_a_retry_band(tmp_path: Path) -> None:
    """Adding a new declaration is not progress against the earlier complete obligation set."""
    sandbox = _DynamicManifestSandbox(
        declarations_by_call={
            1: ("reports/a.txt", "reports/b.txt"),
            2: ("reports/a.txt", "reports/b.txt", "reports/c.txt"),
        }
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", sandbox),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )
    providers: list[ScriptedProvider] = []

    def source_calls(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"dynamic-growth-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Publish every output declared by this attempt",
                    "output_manifest_path": f"outputs-{index}.json",
                },
            )
            for index in (1, 2)
        ] + [
            LLMToolCall(
                id="unrequested-third",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registry=registry,
        registered_source=("analysis.py", b"print('registered analysis')\n"),
        source_response_factory=source_calls,
        provider_capture=providers,
    )

    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert status is TaskStatus.FAILED
    assert len(failures) == 2
    assert len(providers[0].calls) == 2


@pytest.mark.parametrize(
    ("repair_mode", "should_repair"),
    [
        ("complete", True),
        ("partial", False),
        ("unknown", False),
        ("unknown_failed", False),
        ("other_task", False),
        ("source_less", False),
        ("two_failures", False),
    ],
)
def test_new_registered_source_filename_repairs_only_complete_same_task_manifest_outputs(
    tmp_path: Path, repair_mode: str, should_repair: bool
) -> None:
    """A corrected registered source repairs only complete authoritative same-task outputs."""
    run_id = new_id("run")
    agent_id = new_id("agent")
    task_id = new_id("task")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    original = store.save_text(
        "analysis.py",
        "print('initial analysis')",
        produced_by=agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )
    corrected = store.save_text(
        "analysis-corrected.py",
        "print('corrected analysis')",
        produced_by=agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )
    logical_outputs = ("reports/summary.txt", "reports/details.txt")
    published = {
        name: store.save_bytes(
            name,
            f"corrected {name.rsplit('/', 1)[-1]}\n".encode(),
            produced_by=agent_id,
            kind=ArtifactKind.OTHER,
            media_type="text/plain",
        )
        for name in logical_outputs
    }
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    repaired_paths = logical_outputs[:1] if repair_mode == "partial" else logical_outputs
    call_records = (
        (
            "initial",
            original,
            "outputs-1.json",
            task_id,
            logical_outputs,
            repair_mode == "unknown_failed",
        ),
        (
            "corrected",
            corrected,
            "outputs-2.json",
            new_id("task") if repair_mode == "other_task" else task_id,
            repaired_paths,
            repair_mode == "unknown",
        ),
    )
    for call_id, source, manifest, owner_task, output_paths, unknown in call_records:
        call_arguments: dict[str, object] = {"output_manifest_path": manifest}
        if not (call_id == "corrected" and repair_mode == "source_less"):
            call_arguments.update(
                {
                    "source_artifact_id": source.id,
                    "source_artifact_name": source.name,
                    "source_artifact_sha256": source.sha256,
                }
            )
        log.append(
            EventType.TOOL_STARTED,
            actor,
            {
                "task_id": owner_task,
                "tool_call_id": call_id,
                "tool": "run_python",
                "arguments": call_arguments,
            },
            subject_id=call_id,
        )
        success = call_id == "corrected" and repair_mode != "two_failures"
        terminal_payload: dict[str, object] = {
            "task_id": owner_task,
            "tool_call_id": call_id,
            "tool": "run_python",
            "status": ToolCallStatus.COMPLETED if success else ToolCallStatus.FAILED,
            "exit_code": 0,
            "artifact_ids": [published[name].id for name in output_paths] if success else [],
        }
        if not success:
            terminal_payload.update(
                {
                    "error": "output publication failed",
                    "value": {
                        "kind": "failure",
                        "value": {
                            "text": "output publication failed",
                            "error": "output publication failed",
                            "code": "TOOL_FAILURE",
                        },
                    },
                }
            )
            if not unknown:
                terminal_payload["publication_progress"] = {
                    "missing": [[name, "other"] for name in logical_outputs],
                    "present": [],
                }
        if not unknown:
            terminal_payload["output_declarations"] = [
                {"path": name, "kind": "other"} for name in output_paths
            ]
        log.append(
            EventType.TOOL_COMPLETED,
            actor,
            terminal_payload,
            subject_id=call_id,
        )

    evidence = assess_task_completion(log.events(), task_id, store)
    if should_repair:
        assert evidence.unresolved_failures == ()
        assert evidence.repaired_tool_call_ids == ("initial",)
        assert {item.id for item in published.values()}.issubset(evidence.verified_artifact_ids)
    elif repair_mode == "two_failures":
        assert [attempt.call_id for attempt in evidence.unresolved_failures] == [
            "initial",
            "corrected",
        ]
        stop = retry_stop_for_task(
            log.events(),
            task_id,
            artifact_store=store,
            tool_registry=None,
            tool_context=None,
        )
        assert stop is not None
        assert stop.kind == "publication"
        assert stop.terminal_seq == log.events()[-1].seq
    else:
        assert [attempt.call_id for attempt in evidence.unresolved_failures] == ["initial"]


def test_repeated_registered_manifest_syntax_failure_stops_without_execution(
    tmp_path: Path,
) -> None:
    """Syntax retry identity uses the explicit fresh manifest destination without loading it."""
    providers: list[ScriptedProvider] = []

    def repeated_registered_source(source: Artifact) -> Sequence[LLMToolCall]:
        return [
            LLMToolCall(
                id=f"registered-manifest-syntax-{index}",
                name="run_python",
                arguments={
                    "source_artifact_id": source.id,
                    "description": "Execute the same registered manifest-backed source.",
                    "output_manifest_path": "outputs.json",
                },
            )
            for index in (1, 2)
        ] + [
            LLMToolCall(
                id="unrequested-final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]

    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [],
        registered_source=("analysis.py", b"print("),
        source_response_factory=repeated_registered_source,
        provider_capture=providers,
    )

    assert status is TaskStatus.FAILED
    assert len(providers[0].calls) == 2
    assert not any(event.type is EventType.TOOL_STARTED for event in log.events())
    failures = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_python"
    ]
    assert len(failures) == 2
    assert all(event.payload["exit_code"] is None for event in failures)


def test_independent_write_does_not_clear_failed_registered_source(tmp_path: Path) -> None:
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            LLMToolCall(
                id="invalid-a",
                name="write_file",
                arguments={
                    "path": "analysis.py",
                    "kind": "code",
                    "content": r"import pandas as pd\nprint('invalid A')",
                    "description": "Write the requested source A",
                },
            ),
            LLMToolCall(
                id="independent-b",
                name="write_file",
                arguments={
                    "path": "independent.py",
                    "kind": "code",
                    "content": "print('independent B')",
                    "description": "Write independent source B",
                },
            ),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            ),
        ],
        tool_allowlist=("write_file",),
        registry=ToolRegistry((cast("Tool", WriteFile()),)),
    )

    assert status is TaskStatus.FAILED
    assert log.verify().valid


def test_changed_write_to_same_logical_source_repairs_preflight_failure(tmp_path: Path) -> None:
    status, log, store = _run_agent(
        tmp_path,
        CodeResult,
        [
            LLMToolCall(
                id="invalid-a",
                name="write_file",
                arguments={
                    "path": "analysis.py",
                    "kind": "code",
                    "content": r"import pandas as pd\nprint('invalid A')",
                    "description": "Write the requested source A",
                },
            ),
            LLMToolCall(
                id="repair-a",
                name="write_file",
                arguments={
                    "path": "analysis.py",
                    "kind": "code",
                    "content": "print('repaired A')",
                    "description": "Repair the requested source A",
                },
            ),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={
                    "stdout": "done",
                    "exit_code": 0,
                    "artifacts": ["analysis.py"],
                },
            ),
        ],
        tool_allowlist=("write_file",),
        registry=ToolRegistry((cast("Tool", WriteFile()),)),
    )
    assert status is TaskStatus.COMPLETED
    failures = [event for event in log.events() if event.type is EventType.TOOL_COMPLETED]
    assert failures[0].payload["arguments"]["path"] == "analysis.py"
    assert store.get("analysis.py") is not None
    assert log.verify().valid


def test_omitted_run_python_kind_uses_the_registered_other_default_for_repair(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                RunPython(
                    sandbox=cast("Sandbox", _ArtifactPublishingSandbox()),
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )
    status, log, store = _run_agent(
        tmp_path,
        CodeResult,
        [
            LLMToolCall(
                id="invalid-a",
                name="run_python",
                arguments={
                    "code": "print(",
                    "description": "Attempt output A with invalid source",
                    "output_artifacts": [{"path": "a.txt"}],
                },
            ),
            LLMToolCall(
                id="repair-a",
                name="run_python",
                arguments={
                    "code": (
                        "from pathlib import Path; Path('a.txt').write_text('published output\\n')"
                    ),
                    "description": "Repair output A with a valid script",
                    "output_artifacts": [{"path": "a.txt"}],
                },
            ),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={
                    "stdout": "output A is available",
                    "exit_code": 0,
                    "artifacts": ["a.txt"],
                },
            ),
        ],
        registry=registry,
    )

    assert status is TaskStatus.COMPLETED
    artifact = store.get("a.txt")
    assert artifact is not None
    assert artifact.valid
    attempts = [event for event in log.events() if event.type is EventType.TOOL_COMPLETED]
    assert len(attempts) == 2
    assert attempts[0].payload["status"] == "FAILED"
    assert attempts[0].payload["arguments"]["output_artifacts"] == [{"path": "a.txt"}]
    assert attempts[1].payload["status"] == "COMPLETED"
    assert attempts[1].payload["artifact_ids"] == [artifact.id]
    successful_start = next(event for event in log.events() if event.type is EventType.TOOL_STARTED)
    assert successful_start.payload["arguments"]["output_artifacts"][0]["kind"] == "other"
    completion = [event for event in log.events() if event.type is EventType.AGENT_COMPLETED][-1]
    assert completion.payload["completion_evidence"]["repaired_tool_call_ids"] == [
        attempts[0].payload["tool_call_id"]
    ]
    assert log.verify().valid


@pytest.mark.parametrize("include_unrelated_failure", [True, False])
def test_approval_resume_preserves_unrelated_failure_and_is_one_shot(
    tmp_path: Path, include_unrelated_failure: bool
) -> None:
    """A reopened exact approval resumes B once and never clears failed source A."""
    run_id = new_id("run")
    task = Task(
        id=new_id("task"),
        run_id=run_id,
        agent_id=new_id("agent"),
        objective="Write and validate analysis.py.",
    )
    event_path = tmp_path / "events.jsonl"
    log = JsonlEventLog(event_path, run_id)
    initial = review_gated_context(
        tmp_path,
        run_id=run_id,
        log=log,
        engine=PolicyEngine(development_policy()),
    )
    store = initial.artifact_store
    spec = AgentSpec(
        name="coding",
        role=Role.AGENT,
        task_kinds=("code",),
        tool_allowlist=("write_file",),
        max_turns=4,
        max_depth=1,
        system_prompt_ref="coding",
        output_schema_ref="tests.thymira.fixtures_agent_output:DataProfileOutput",
    )
    catalog = AgentCatalog(
        (spec,),
        system_prompts={"coding": "Write the requested source."},
        output_schemas={"coding": CodeResult},
    )

    def run_pass(
        current_log: JsonlEventLog,
        current_store: ArtifactStore,
        responses: Sequence[LLMToolCall],
        *,
        resume: Any = None,
    ) -> Any:
        """Run one pass against the reopened log and store with task-scoped authority."""
        gate = Gate(PolicyEngine(development_policy()), current_log, mode=GateMode.DEFERRED)
        context = replace(
            initial,
            agent_id=task.agent_id,
            task_id=task.id,
            event_log=current_log,
            gate=gate,
            artifact_store=current_store,
        )
        return AgentRunner().run(
            spec,
            task,
            AgentContext(
                route_policy=ModelRoutePolicy.from_routes(
                    ("test-model",), authority="code-owned-tests"
                ),
                catalog=catalog,
                event_log=current_log,
                provider=ScriptedProvider(responses),
                tool_registry=ToolRegistry((cast("Tool", WriteFile()),)),
                tool_context=context,
            ),
            resume=resume,
        )

    first_calls: list[LLMToolCall] = []
    if include_unrelated_failure:
        first_calls.append(
            LLMToolCall(
                id="failed-source-a",
                name="write_file",
                arguments={
                    "path": "analysis.py",
                    "kind": "code",
                    "content": r"import pandas as pd\nprint('invalid A')",
                    "description": "Write the required source A",
                },
            )
        )
    first_calls.append(
        LLMToolCall(
            id="approved-independent-b",
            name="write_file",
            arguments={
                "path": "independent.py",
                "kind": "code",
                "content": "print('independent B')",
                "description": "Write independent source B",
            },
        )
    )
    parked = run_pass(log, store, first_calls)
    assert parked.task_status is TaskStatus.PENDING
    pending_request = next(
        event
        for event in reversed(log.events())
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    failed_source = next(
        (
            event
            for event in log.events()
            if event.type is EventType.TOOL_COMPLETED
            and event.payload.get("arguments", {}).get("path") == "analysis.py"
        ),
        None,
    )
    assert (failed_source is not None) is include_unrelated_failure
    if failed_source is not None:
        assert failed_source.payload["arguments"]["path"] == "analysis.py"

    log.append(
        EventType.HUMAN_APPROVAL,
        Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        {
            "decision_id": pending_request.payload["decision_id"],
            "approved": True,
            "automatic": False,
        },
        subject_id=pending_request.payload["decision_id"],
    )
    reopened_log = JsonlEventLog(event_path, run_id)
    reopened_store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    assert reopened_log.verify().valid
    assert reopened_store.verify() == []
    resume_context = replace(
        initial,
        agent_id=task.agent_id,
        task_id=task.id,
        event_log=reopened_log,
        gate=Gate(PolicyEngine(development_policy()), reopened_log, mode=GateMode.DEFERRED),
        artifact_store=reopened_store,
    )
    lookup = load_pending_resume(reopened_store, resume_context, task.agent_id, task_id=task.id)
    assert lookup.status is ResumeStatus.READY
    assert lookup.resume is not None

    after_approval = (
        [
            LLMToolCall(
                id="approved-independent-b",
                name="write_file",
                arguments={
                    "path": "independent.py",
                    "kind": "code",
                    "content": "print('independent B')",
                    "description": "Write independent source B",
                },
            )
        ]
        if include_unrelated_failure
        else [
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ]
    )
    resumed = run_pass(
        reopened_log,
        reopened_store,
        after_approval,
        resume=lookup.resume,
    )
    assert reopened_store.get("independent.py") is not None
    _assert_approval_resume_outcome(
        include_unrelated_failure=include_unrelated_failure,
        resumed=resumed,
        task=task,
        log=reopened_log,
        store=reopened_store,
        context=resume_context,
        pending_request=pending_request,
        failed_source=failed_source,
        run_pass=run_pass,
    )


def _assert_approval_resume_outcome(
    *,
    include_unrelated_failure: bool,
    resumed: Any,
    task: Task,
    log: JsonlEventLog,
    store: ArtifactStore,
    context: ToolContext,
    pending_request: Event,
    failed_source: Event | None,
    run_pass: Callable[..., Any],
) -> None:
    """Check both exact-ticket resumption and rejection of an unapproved repeat call."""
    if not include_unrelated_failure:
        assert resumed.task_status is TaskStatus.COMPLETED
        completion = next(
            event
            for event in reversed(log.events())
            if event.type is EventType.AGENT_COMPLETED and event.payload.get("task_id") == task.id
        )
        assert completion.payload["status"] == TaskStatus.COMPLETED.value
        assert completion.payload["completion_evidence"]["unresolved_tool_call_ids"] == []
        assert completion.payload["completion_evidence"]["repaired_tool_call_ids"] == []
        assert log.verify().valid
        assert store.verify() == []
        return

    assert resumed.task_status is TaskStatus.PENDING
    duplicate_requests = [
        event
        for event in log.events()
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
        and event.payload.get("decision_id") != pending_request.payload["decision_id"]
    ]
    assert len(duplicate_requests) == 1
    decision_id = duplicate_requests[0].payload["decision_id"]
    assert not any(
        event.type is EventType.TOOL_STARTED and event.payload.get("decision_id") == decision_id
        for event in log.events()
    )
    log.append(
        EventType.HUMAN_APPROVAL,
        Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        {"decision_id": decision_id, "approved": False, "automatic": False},
        subject_id=decision_id,
    )
    final_lookup = load_pending_resume(store, context, task.agent_id, task_id=task.id)
    assert final_lookup.status is ResumeStatus.READY
    assert final_lookup.resume is not None
    final = run_pass(
        log,
        store,
        [
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            )
        ],
        resume=final_lookup.resume,
    )
    assert final.task_status is TaskStatus.FAILED
    completion = next(
        event
        for event in reversed(log.events())
        if event.type is EventType.AGENT_COMPLETED and event.payload.get("task_id") == task.id
    )
    evidence = completion.payload["completion_evidence"]
    assert len(evidence["unresolved_tool_call_ids"]) == 3
    pending = next(
        event
        for event in log.events()
        if event.type is EventType.TOOL_DENIED
        and event.payload.get("ticket_outcome") == "pending"
        and event.payload.get("decision_id") == decision_id
    )
    denied = next(
        event
        for event in log.events()
        if event.type is EventType.TOOL_DENIED and event.payload.get("ticket_outcome") == "rejected"
    )
    assert evidence["repaired_tool_call_ids"] == []
    assert failed_source is not None
    assert set(evidence["unresolved_tool_call_ids"]) == {
        failed_source.payload["tool_call_id"],
        pending.payload["tool_call_id"],
        denied.payload["tool_call_id"],
    }
    assert (
        sum(
            event.type is EventType.TOOL_STARTED
            and event.payload.get("tool") == "write_file"
            and event.payload.get("task_id") == task.id
            for event in log.events()
        )
        == 1
    )
    assert log.verify().valid
    assert store.verify() == []


def test_denied_tool_call_is_runtime_failure_evidence_for_code_result(tmp_path: Path) -> None:
    tool = FakeTool(
        "probe",
        ToolCapability(id="probe", external_effects=()),
        arguments_model=EchoArguments,
    )
    status, log, _store = _run_agent(
        tmp_path,
        CodeResult,
        [
            LLMToolCall(id="denied", name="probe", arguments={"value": "x"}),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "done", "exit_code": 0, "artifacts": []},
            ),
        ],
        tool_allowlist=("probe",),
        registry=ToolRegistry((tool,)),
        allowed_tools=frozenset(),
    )

    assert status is TaskStatus.FAILED
    denied = [event for event in log.events() if event.type is EventType.TOOL_DENIED]
    assert len(denied) == 1
    assert denied[0].payload["task_id"] is not None


@pytest.mark.parametrize(
    ("schema", "final_arguments"),
    [
        (
            DataProfileOutput,
            {"row_count": 1, "columns": ["value"]},
        ),
        (
            EvaluationResult,
            {"summary": "Evaluation completed.", "metrics_artifact_id": "artifact_fabricated"},
        ),
    ],
)
def test_output_without_exit_code_keeps_a_failed_tool_failure(
    tmp_path: Path, schema: type[BaseModel], final_arguments: dict[str, Any]
) -> None:
    status, _log, _store = _run_agent(
        tmp_path,
        schema,
        [
            _missing_output("failed-a", "source_a.txt"),
            LLMToolCall(id="final", name="final_result", arguments=final_arguments),
        ],
    )

    assert status is TaskStatus.FAILED
