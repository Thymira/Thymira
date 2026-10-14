"""Resolve and re-check exact Python source held in one run's active artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from thymira.events import scrub_credentials, sha256_bytes
from thymira.schemas import ArtifactKind
from thymira.tools.builtins.python_source import MAX_PYTHON_SOURCE_BYTES, validate_python_source
from thymira.tools.models import ToolExecutionError

if TYPE_CHECKING:
    from thymira.tools.models import ToolInvocation

_SOURCE_ARTIFACT_ID = "source_artifact_id"
_SOURCE_ARTIFACT_NAME = "source_artifact_name"
_SOURCE_ARTIFACT_SHA256 = "source_artifact_sha256"
_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class PythonSourceReference:
    """A verified artifact identity paired with the exact UTF-8 bytes it names."""

    artifact_id: str
    name: str
    sha256: str
    source: str
    content: bytes


def load_python_source_reference(
    invocation: ToolInvocation, artifact_id: str
) -> PythonSourceReference:
    """Load only an active, bounded ``CODE`` ``.py`` artifact owned by this run.

    The artifact store is the only source of bytes. No workspace path, basename lookup, source
    normalization, or fallback is used.
    """
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ToolExecutionError("source_artifact_id must identify an active CODE .py artifact")
    try:
        artifact = next(
            (item for item in invocation.artifact_store.list_active() if item.id == artifact_id),
            None,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ToolExecutionError("cannot inspect active Python source artifacts") from exc
    if (
        artifact is None
        or not artifact.valid
        or artifact.run_id != invocation.run_id
        or artifact.kind is not ArtifactKind.CODE
        or PurePosixPath(artifact.name).suffix.casefold() != ".py"
    ):
        raise ToolExecutionError(
            "source_artifact_id must identify an active CODE .py artifact in this run"
        )
    if artifact.size_bytes > MAX_PYTHON_SOURCE_BYTES:
        raise ToolExecutionError(
            f"source artifact exceeds the {MAX_PYTHON_SOURCE_BYTES}-byte limit"
        )
    try:
        content = invocation.artifact_store.load_bytes_bounded(
            artifact.name, MAX_PYTHON_SOURCE_BYTES
        )
    except (KeyError, OSError, ValueError) as exc:
        raise ToolExecutionError("cannot read the active Python source artifact") from exc
    try:
        current = invocation.artifact_store.get(artifact.name)
    except (OSError, TypeError, ValueError) as exc:
        raise ToolExecutionError("cannot verify the active Python source artifact") from exc
    if (
        current is None
        or current != artifact
        or not current.valid
        or current.run_id != invocation.run_id
    ):
        raise ToolExecutionError("Python source artifact is no longer active in this run")
    if len(content) != artifact.size_bytes or sha256_bytes(content) != artifact.sha256:
        raise ToolExecutionError("Python source artifact does not match its registered bytes")
    if (
        scrub_credentials(artifact.id) != artifact.id
        or scrub_credentials(artifact.name) != artifact.name
    ):
        raise ToolExecutionError("Python source artifact identity contains credential-shaped text")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError("Python source artifact is not valid UTF-8") from exc
    if scrub_credentials(source) != source:
        raise ToolExecutionError("Python source artifact contains credential-shaped text")
    if not source:
        raise ValueError("Python source must not be empty")
    return PythonSourceReference(
        artifact_id=artifact.id,
        name=artifact.name,
        sha256=artifact.sha256,
        source=validate_python_source(source),
        content=content,
    )


def verify_prepared_python_source(
    invocation: ToolInvocation, arguments: dict[str, Any]
) -> PythonSourceReference:
    """Re-load and compare runtime-prepared identity before staging executable bytes."""
    artifact_id = arguments.get(_SOURCE_ARTIFACT_ID)
    artifact_name = arguments.get(_SOURCE_ARTIFACT_NAME)
    artifact_sha256 = arguments.get(_SOURCE_ARTIFACT_SHA256)
    code = arguments.get("code")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ToolExecutionError(
            "run_python source artifact request was not prepared by Tool Manager"
        )
    if not isinstance(artifact_name, str) or not artifact_name:
        raise ToolExecutionError(
            "run_python source artifact request was not prepared by Tool Manager"
        )
    if not isinstance(code, str) or not code:
        raise ToolExecutionError(
            "run_python source artifact request was not prepared by Tool Manager"
        )
    if not isinstance(artifact_sha256, str) or len(artifact_sha256) != _SHA256_HEX_LENGTH:
        raise ToolExecutionError(
            "run_python source artifact request was not prepared by Tool Manager"
        )
    reference = load_python_source_reference(invocation, artifact_id)
    if (
        reference.name != artifact_name
        or reference.sha256 != artifact_sha256
        or reference.source != code
    ):
        raise ToolExecutionError("Python source artifact changed after preflight")
    return reference
