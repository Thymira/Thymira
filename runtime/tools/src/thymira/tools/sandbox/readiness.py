"""Bounded, read-only checks of the configured execution prerequisites."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from thymira.schemas import SandboxMode
from thymira.schemas.tool_runtime import ToolRuntimeStatus
from thymira.tools.sandbox.container import ContainerSandbox
from thymira.tools.sandbox.local import LocalSubprocessSandbox
from thymira.tools.sandbox.process_capture import OutputLimitExceeded, run_bounded_process

if TYPE_CHECKING:
    from thymira.tools.models import Tool
    from thymira.tools.registry import ToolRegistry

_INSTALL = "Install/start Docker with Linux containers, then build the configured sandbox image."


def missing_runtime(tool: Tool) -> str | None:
    """Refuse unavailable container prerequisites before approval; never replace the sandbox."""
    sandbox = getattr(tool, "sandbox", None)
    if isinstance(sandbox, ContainerSandbox):
        status = _container_status(sandbox)
        if status.status != "ready":
            return f"container runtime unavailable: {status.summary} {status.next_step}"
    return None


def python_runtime_status(registry: ToolRegistry) -> ToolRuntimeStatus:
    """Check the registered Python backend without executing user code or changing the host."""
    try:
        tool = registry.get("run_python")
    except KeyError:
        return ToolRuntimeStatus(
            status="not_configured",
            code="python_not_registered",
            backend="unknown",
            summary="Python execution is not registered in this runtime.",
        )
    sandbox = getattr(tool, "sandbox", None)
    if isinstance(sandbox, ContainerSandbox):
        return _container_status(sandbox)
    if isinstance(sandbox, LocalSubprocessSandbox):
        if tool.capability.sandbox_mode is not SandboxMode.DANGER_FULL_ACCESS:
            return ToolRuntimeStatus(
                status="unavailable",
                code="local_confinement_unavailable",
                backend="local",
                summary="The local backend cannot enforce the requested Python confinement.",
                next_step="Configure the container sandbox before executing Python.",
            )
        return ToolRuntimeStatus(
            status="unknown",
            code="local_unconfined",
            backend="local",
            summary="Local Python execution is configured without container confinement.",
            next_step="Tool policy and audit checks still apply to every execution.",
        )
    return ToolRuntimeStatus(
        status="unknown",
        code="backend_not_probed",
        backend="unknown",
        summary="This Python backend does not provide a prerequisite check.",
    )


def _container_status(sandbox: ContainerSandbox) -> ToolRuntimeStatus:
    """Probe only daemon OS and image presence with time/output bounds and fixed diagnostics."""
    docker = shutil.which("docker")
    if docker is None:
        return _unavailable("docker_missing", "Python unavailable: docker was not found.")
    try:
        info = _probe([docker, "info", "--format", "{{.OSType}}"])
        if info.returncode != 0:
            return _unavailable(
                "docker_unreachable", "Python unavailable: Docker is not reachable."
            )
        if info.stdout.strip() != "linux":
            return _unavailable("linux_containers_required", "Python requires Linux containers.")
        image = _probe([docker, "image", "inspect", "--format", "{{.Id}}", sandbox.image])
        if image.returncode != 0 or not image.stdout.strip():
            return _unavailable(
                "sandbox_image_missing", "The configured sandbox image is unavailable."
            )
    except (OSError, subprocess.TimeoutExpired, OutputLimitExceeded):
        return _unavailable(
            "docker_probe_failed", "The Docker prerequisite check did not complete."
        )
    return ToolRuntimeStatus(
        status="ready",
        code="container_prerequisites_ready",
        backend="container",
        summary="Docker and the configured sandbox image are available.",
        next_step="Tool approval and execution-time confinement checks still apply.",
    )


def _probe(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Keep prerequisite checks short and avoid buffering unbounded daemon responses."""
    return run_bounded_process(command, cwd=Path.cwd(), timeout_s=3, output_limit_bytes=16_384)


def _unavailable(code: str, summary: str) -> ToolRuntimeStatus:
    """Return a fixed diagnostic without publishing host paths, configuration or stderr."""
    return ToolRuntimeStatus(
        status="unavailable",
        code=code,
        backend="container",
        summary=summary,
        next_step=_INSTALL,
    )
