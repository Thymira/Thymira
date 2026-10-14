"""Run Python from a registered code artifact through the public Tool Manager."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest

from tests.thymira.fixtures_tools import development_policy
from thymira.events import InMemoryEventLog
from thymira.policies import Gate, GateMode, PolicyEngine, RiskProfile, load_policy_stack
from thymira.schemas import (
    Actor,
    ActorKind,
    Artifact,
    ArtifactKind,
    EventType,
    SandboxEnforcement,
    SandboxMode,
    ToolCallStatus,
    new_id,
)
from thymira.state import LocalArtifactStore
from thymira.tools import Tool, ToolContext, ToolManager, ToolRegistry
from thymira.tools.builtins.run_python import RunPython
from thymira.tools.sandbox import SandboxRun, StagedInput


@dataclass
class _CapturingSandbox:
    outputs: dict[str, bytes] = field(default_factory=dict)
    staged_inputs: list[tuple[StagedInput, ...]] = field(default_factory=list)
    mode: SandboxMode = SandboxMode.DANGER_FULL_ACCESS

    def run(self, argv: list[str], **kwargs: Any) -> SandboxRun:
        """Capture staged source and materialize deterministic declared outputs."""
        del argv
        workspace = Path(kwargs["workspace"])
        self.staged_inputs.append(tuple(kwargs.get("staged_inputs", ())))
        for name, content in self.outputs.items():
            output = workspace / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        return SandboxRun(
            stdout="captured",
            stderr="",
            exit_code=0,
            mode=self.mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


def _context(tmp_path: Path) -> ToolContext:
    """Build one isolated manager context with the local test policy and store."""
    run_id = new_id("run")
    event_log = InMemoryEventLog(run_id)
    return ToolContext(
        run_id=run_id,
        agent_id=new_id("agent"),
        workspace=tmp_path / "workspace",
        event_log=event_log,
        gate=Gate(PolicyEngine(development_policy()), event_log),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", run_id),
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=1.0
        ),
    )


def _manager(
    sandbox: _CapturingSandbox,
    *,
    mode: SandboxMode = SandboxMode.DANGER_FULL_ACCESS,
) -> ToolManager:
    """Build the public manager with the deterministic capturing sandbox."""
    sandbox.mode = mode
    tool = RunPython(sandbox=sandbox, mode=mode)
    return ToolManager(ToolRegistry((cast("Tool", tool),)))


def _code_artifact(context: ToolContext, name: str, source: str) -> Artifact:
    """Register a code artifact in the context's active run."""
    return context.artifact_store.save_text(
        name,
        source,
        produced_by=context.agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )


def _assert_rejected_before_gate(
    context: ToolContext, execution: Any, *, event_offset: int = 0
) -> None:
    """Assert a source refusal closed before policy review or sandbox dispatch."""
    assert execution.call.status is ToolCallStatus.FAILED
    assert not any(
        event.type
        in {
            EventType.POLICY_DECISION,
            EventType.HUMAN_APPROVAL_REQUESTED,
            EventType.TOOL_STARTED,
        }
        for event in context.event_log.events()[event_offset:]
    )


def test_run_python_executes_exact_registered_code_artifact_and_records_lineage(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    source = 'print("literal \\\\n remains")\nprint("second line")\n'
    artifact = _code_artifact(context, "eda_script.py", source)
    (context.workspace).mkdir(parents=True)
    (context.workspace / "eda_script.py").write_text(
        "raise RuntimeError('workspace copy must not run')\n", encoding="utf-8"
    )
    sandbox = _CapturingSandbox(outputs={"summary.txt": b"generated"})
    manager = _manager(sandbox)

    execution = manager.execute(
        context,
        "run_python",
        {
            "source_artifact_id": artifact.id,
            "description": "Run the registered analysis script",
            "output_artifacts": [{"path": "summary.txt"}],
        },
    )

    assert execution.result.success
    assert sandbox.staged_inputs[0][0].content == source.encode("utf-8")
    assert "workspace copy must not run" not in sandbox.staged_inputs[0][0].content.decode()
    started = next(
        event for event in context.event_log.events() if event.type is EventType.TOOL_STARTED
    )
    assert started.payload["arguments"]["code"] == source
    assert started.payload["arguments"]["source_artifact_id"] == artifact.id
    assert started.payload["arguments"]["source_artifact_sha256"] == artifact.sha256
    assert started.payload["arguments"]["source_artifact_name"] == artifact.name
    assert execution.result.value is not None
    assert execution.result.value.model_dump(mode="python")["source_artifact_id"] == artifact.id
    output = context.artifact_store.get("summary.txt")
    assert output is not None
    assert output.input_artifact_ids == (artifact.id,)


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "code": "print('inline')",
            "source_artifact_id": "artifact_other",
            "description": "Reject mixed sources",
        },
        {
            "code": "print('inline')",
            "source_artifact_sha256": "0" * 64,
            "description": "Reject forged provenance",
        },
    ],
)
def test_mixed_or_model_supplied_runtime_source_fields_fail_before_gate(
    tmp_path: Path, arguments: dict[str, str]
) -> None:
    context = _context(tmp_path)
    sandbox = _CapturingSandbox()

    execution = _manager(sandbox).execute(context, "run_python", arguments)

    assert not execution.result.success
    assert "invalid arguments for tool 'run_python'" in (execution.result.error or "")
    _assert_rejected_before_gate(context, execution)
    assert sandbox.staged_inputs == []


def test_source_artifact_with_credentials_is_rejected_without_recording_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    secret = "sk-aB3dEf0gHi1jKl2mNo3pQr4sT5u"  # noqa: S105  # fixture credential
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    artifact = _code_artifact(context, "secret.py", f"key = {secret!r}\n")
    sandbox = _CapturingSandbox()

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {"source_artifact_id": artifact.id, "description": "Reject credentials"},
    )

    assert not execution.result.success
    assert "credential-shaped" in (execution.result.error or "")
    _assert_rejected_before_gate(context, execution)
    assert sandbox.staged_inputs == []
    assert secret not in json.dumps(
        [event.payload for event in context.event_log.events()], default=str
    )


def test_foreign_run_source_artifact_is_rejected_before_gate(tmp_path: Path) -> None:
    context = _context(tmp_path)
    foreign_store = LocalArtifactStore(tmp_path / "foreign-artifacts", new_id("run"))
    artifact = foreign_store.save_text(
        "foreign.py",
        "print('foreign')\n",
        produced_by=context.agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )
    context = replace(context, artifact_store=foreign_store)
    sandbox = _CapturingSandbox()

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {"source_artifact_id": artifact.id, "description": "Reject foreign source"},
    )

    assert not execution.result.success
    assert "in this run" in (execution.result.error or "")
    _assert_rejected_before_gate(context, execution)
    assert sandbox.staged_inputs == []


@pytest.mark.parametrize("tamper", ["bytes", "invalidate"])
def test_changed_or_invalidated_source_artifact_is_rejected_before_gate(
    tmp_path: Path, tamper: str
) -> None:
    context = _context(tmp_path)
    artifact = _code_artifact(context, "analysis.py", "print('registered')\n")
    if tamper == "bytes":
        (tmp_path / "artifacts" / artifact.uri).write_bytes(b"print('tampered')\n")
    else:
        context.artifact_store.invalidate([artifact.name], "test source invalidation")
    sandbox = _CapturingSandbox()

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {"source_artifact_id": artifact.id, "description": "Reject changed source"},
    )

    assert not execution.result.success
    _assert_rejected_before_gate(context, execution)
    assert sandbox.staged_inputs == []


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        ("x = \\n print('bad')\n", "invalid Python syntax at line"),
        ("", "Python source must not be empty"),
    ],
)
def test_invalid_registered_source_fails_with_actionable_preflight_error(
    tmp_path: Path, source: str, diagnostic: str
) -> None:
    context = _context(tmp_path)
    artifact = _code_artifact(context, "invalid.py", source)
    sandbox = _CapturingSandbox()

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {"source_artifact_id": artifact.id, "description": "Validate source syntax"},
    )

    assert not execution.result.success
    assert (execution.result.error or "").startswith(
        "invalid arguments for tool 'run_python': code: Value error, "
    )
    assert diagnostic in (execution.result.error or "")
    _assert_rejected_before_gate(context, execution)
    assert sandbox.staged_inputs == []


def test_legacy_inline_request_keeps_its_canonical_argument_shape(tmp_path: Path) -> None:
    context = _context(tmp_path)

    execution = _manager(_CapturingSandbox()).execute(
        context,
        "run_python",
        {"code": "print('legacy')\n", "description": "Keep legacy arguments"},
    )

    assert execution.result.success
    started = next(
        event for event in context.event_log.events() if event.type is EventType.TOOL_STARTED
    )
    assert started.payload["arguments"]["code"] == "print('legacy')\n"
    assert "source_artifact_id" not in started.payload["arguments"]
    assert "source_artifact_name" not in started.payload["arguments"]
    assert "source_artifact_sha256" not in started.payload["arguments"]


def test_registered_reference_approval_is_bound_to_immutable_artifact_identity(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context = replace(
        context,
        gate=Gate(
            PolicyEngine(load_policy_stack()),
            context.event_log,
            approver=None,
            mode=GateMode.DEFERRED,
        ),
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=0.1
        ),
    )
    original = _code_artifact(context, "analysis.py", "print('first revision')\n")
    sandbox = _CapturingSandbox()
    manager = _manager(sandbox, mode=SandboxMode.WORKSPACE_WRITE)
    request = {
        "source_artifact_id": original.id,
        "description": "Run the approved source revision",
    }

    pending = manager.execute(context, "run_python", request)
    assert pending.pending_approval is not None
    context.event_log.append(
        EventType.HUMAN_APPROVAL,
        Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        {
            "decision_id": pending.pending_approval.id,
            "approved": True,
            "automatic": False,
        },
    )
    resumed = manager.execute(context, "run_python", request)

    assert resumed.result.success
    assert sandbox.staged_inputs[-1][0].content == b"print('first revision')\n"

    replacement = _code_artifact(context, "analysis.py", "print('second revision')\n")
    before_stale = len(context.event_log.events())
    stale = manager.execute(context, "run_python", request)
    assert not stale.result.success
    _assert_rejected_before_gate(context, stale, event_offset=before_stale)
    renewed = manager.execute(
        context,
        "run_python",
        {
            "source_artifact_id": replacement.id,
            "description": "Run the replacement source revision",
        },
    )

    assert renewed.pending_approval is not None
    assert renewed.pending_approval.id != pending.pending_approval.id
    assert len(sandbox.staged_inputs) == 1
