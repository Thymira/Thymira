"""Missing execution prerequisites must not consume human approvals or start tools."""

import subprocess
from typing import cast

import pytest

from thymira.events import InMemoryEventLog
from thymira.mira.checks import AuditContext, ControlStatus, audit_run
from thymira.policies import Gate, PolicyEngine, RiskProfile, load_policy_stack
from thymira.schemas import EventType, ToolCallStatus, new_id
from thymira.state import LocalArtifactStore
from thymira.tools import Tool, ToolContext, ToolManager, ToolRegistry
from thymira.tools.builtins import RunPython
from thymira.tools.sandbox import ContainerSandbox, readiness
from thymira.tools.sandbox.process_capture import OutputLimitExceeded


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("missing", "docker was not found"),
        ("daemon", "Docker is not reachable"),
        ("windows", "requires Linux containers"),
        ("image", "image is unavailable"),
        ("timeout", "check did not complete"),
    ],
)
def test_unavailable_docker_refuses_before_policy_review_or_execution(
    tmp_path,
    monkeypatch,
    failure,
    message,
):
    monkeypatch.setattr(
        "shutil.which", lambda _name: None if failure == "missing" else "docker-test"
    )

    def process(command, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 3)
        if command[1] == "info":
            return subprocess.CompletedProcess(
                command,
                1 if failure == "daemon" else 0,
                "windows" if failure == "windows" else "linux",
                "",
            )
        return subprocess.CompletedProcess(command, 1, "", "private error")

    monkeypatch.setattr(readiness, "run_bounded_process", process)
    run_id = new_id("run")
    log = InMemoryEventLog(run_id)
    context = ToolContext(
        run_id=run_id,
        agent_id=new_id("agent"),
        workspace=tmp_path,
        event_log=log,
        gate=Gate(PolicyEngine(load_policy_stack()), log),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", run_id),
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=1.0
        ),
    )
    tool = RunPython(sandbox=ContainerSandbox())
    result = ToolManager(ToolRegistry((cast("Tool", tool),))).execute(
        context, "run_python", {"code": "print(1)", "description": "Print a sample value"}
    )
    assert result.call.status is ToolCallStatus.FAILED
    assert message in (result.call.error or "")
    types = [event.type for event in log.events()]
    assert EventType.HUMAN_APPROVAL_REQUESTED not in types
    assert EventType.POLICY_DECISION not in types
    assert EventType.TOOL_STARTED not in types
    assert types == [EventType.TOOL_COMPLETED]
    assert log.events()[0].payload["sandbox_enforcement"] is None
    report = audit_run(
        AuditContext(
            run_id,
            log.events(),
            context.artifact_store,
            context.gate.engine.policy_sha256,
        )
    )
    confinement = next(control for control in report.controls if control.control_id == "A19")
    assert confinement.status is ControlStatus.NOT_APPLICABLE


@pytest.mark.parametrize(
    ("info_code", "os_type", "image_code", "expected"),
    [
        (1, "private daemon output", 0, "docker_unreachable"),
        (0, "windows", 0, "linux_containers_required"),
        (0, "linux", 1, "sandbox_image_missing"),
        (0, "linux", 0, "container_prerequisites_ready"),
    ],
)
def test_runtime_probe_checks_daemon_and_image_without_running_code(
    monkeypatch,
    info_code,
    os_type,
    image_code,
    expected,
):
    commands = []
    monkeypatch.setattr("shutil.which", lambda _name: "docker-test")

    def process(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout_s"] == 3
        assert kwargs["output_limit_bytes"] == 16_384
        if command[1] == "info":
            return subprocess.CompletedProcess(command, info_code, os_type, "private stderr")
        return subprocess.CompletedProcess(command, image_code, "sha256:sample", "private stderr")

    monkeypatch.setattr(readiness, "run_bounded_process", process)
    registry = ToolRegistry((cast("Tool", RunPython(sandbox=ContainerSandbox())),))
    status = readiness.python_runtime_status(registry)
    assert status.code == expected
    assert "private" not in status.model_dump_json()
    assert all(command[1] in ("info", "image") for command in commands)


@pytest.mark.parametrize(
    "error",
    [
        OSError("private path"),
        subprocess.TimeoutExpired("docker", 3),
        OutputLimitExceeded(stdout="private", stderr="private", limit_bytes=16_384),
    ],
)
def test_runtime_probe_failure_is_bounded_and_never_reported_as_ready(monkeypatch, error):
    monkeypatch.setattr("shutil.which", lambda _name: "docker-test")

    def process(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(readiness, "run_bounded_process", process)
    registry = ToolRegistry((cast("Tool", RunPython(sandbox=ContainerSandbox())),))
    status = readiness.python_runtime_status(registry)
    assert status.status == "unavailable"
    assert status.code == "docker_probe_failed"
    assert "private" not in status.model_dump_json()
