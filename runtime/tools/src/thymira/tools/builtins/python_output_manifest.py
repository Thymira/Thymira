"""Bounded, execution-produced output declarations for Python tools."""

from __future__ import annotations

import json
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, TypeAdapter, ValidationError

from thymira.schemas import ArtifactKind
from thymira.state import canonical_artifact_name
from thymira.tools.builtins.files import contained_path
from thymira.tools.builtins.python_arguments import RunPythonOutputArtifact
from thymira.tools.builtins.subprocess_command import assert_no_link_components
from thymira.tools.models import ToolExecutionError
from thymira.tools.publication_progress import validated_python_output_declarations

if TYPE_CHECKING:
    from thymira.tools.models import ToolInvocation

MAX_OUTPUT_MANIFEST_BYTES = 65536
_OUTPUTS = TypeAdapter(Annotated[list[RunPythonOutputArtifact], Field(min_length=1, max_length=64)])


def output_manifest_path(invocation: ToolInvocation, name: str, *, fresh: bool) -> Path:
    """Resolve an explicit JSON path, refusing internal, noncanonical or linked destinations."""
    if Path(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise ToolExecutionError("output_manifest_path must be workspace-relative")
    try:
        canonical = canonical_artifact_name(name)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc
    if (
        canonical != name
        or name.casefold() == ".thymira"
        or name.casefold().startswith(".thymira/")
    ):
        raise ToolExecutionError("output_manifest_path must be canonical and outside .thymira/")
    if Path(name).suffix.lower() != ".json":
        raise ToolExecutionError("output_manifest_path must name a JSON file")
    workspace = Path(invocation.workspace).resolve()
    path = contained_path(workspace, name)
    assert_no_link_components(workspace, path)
    if fresh and path.exists():
        raise ToolExecutionError(
            "output_manifest_path already exists; choose a fresh path for this execution"
        )
    return path


def load_output_manifest(
    invocation: ToolInvocation, name: str
) -> tuple[RunPythonOutputArtifact, ...]:
    """Validate and return exact logical descriptors written by the script."""
    path = output_manifest_path(invocation, name, fresh=False)
    with path.open("rb") as handle:
        data = handle.read(MAX_OUTPUT_MANIFEST_BYTES + 1)
    if len(data) > MAX_OUTPUT_MANIFEST_BYTES:
        raise ToolExecutionError("Python output manifest exceeds 65536 bytes")
    try:
        entries = _OUTPUTS.validate_json(data)
    except ValidationError as exc:
        raise ToolExecutionError(
            "Python output manifest must be a JSON array of 1-64 output descriptors "
            "with path and optional kind and media_type"
        ) from exc
    try:
        return validated_python_output_declarations(tuple(entries), manifest_path=name)
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc


def manifest_publication_descriptor(name: str) -> dict[str, Any]:
    """Describe the manifest as an atomically published bookkeeping artifact."""
    return {
        "path": name,
        "kind": ArtifactKind.LOG,
        "media_type": "application/json",
    }


def publication_failure(error: Exception) -> str:
    """Distinguish a successful interpreter from a failed atomic publication."""
    return (
        f"Python execution succeeded (exit code 0), but output publication failed: {error}. "
        "No outputs were registered. Check output declarations against files actually written. "
        "Do not use read_file on PNGs or rewrite working code to fix a declaration mismatch. "
        "For data-dependent outputs, write a fresh output_manifest_path JSON from the same "
        "loop that saves the files."
    )


def published_manifest_text(stdout: str, descriptors: tuple[dict[str, Any], ...]) -> str:
    """Expose exact published paths rather than asking the model to reconstruct them."""
    return stdout + "\nPublished artifacts: " + json.dumps([item["path"] for item in descriptors])
