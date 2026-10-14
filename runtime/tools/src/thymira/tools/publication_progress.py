"""Runtime-owned evidence for failed Python artifact publication."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any

from pydantic import Field, ValidationError, model_validator

from thymira.schemas import ArtifactKind, ThymiraModel
from thymira.state import canonical_artifact_name
from thymira.tools.builtins.python_arguments import RunPythonOutputArtifact

if TYPE_CHECKING:
    from pydantic import BaseModel

    from thymira.tools.models import ToolResult

MAX_PYTHON_PUBLICATION_OUTPUTS = 64


class PythonPublicationProgress(ThymiraModel):
    """Validated missing and present output identities from one failed Python publication."""

    missing: tuple[tuple[str, ArtifactKind], ...] = Field(
        default=(), max_length=MAX_PYTHON_PUBLICATION_OUTPUTS
    )
    present: tuple[tuple[str, ArtifactKind], ...] = Field(
        default=(), max_length=MAX_PYTHON_PUBLICATION_OUTPUTS
    )

    @model_validator(mode="after")
    def _identities_are_disjoint(self) -> PythonPublicationProgress:
        """Require unique non-empty paths and keep missing and present sets disjoint."""
        missing_paths = [path for path, _kind in self.missing]
        present_paths = [path for path, _kind in self.present]
        if (
            len(missing_paths) + len(present_paths) > MAX_PYTHON_PUBLICATION_OUTPUTS
            or any(not path for path in (*missing_paths, *present_paths))
            or len(missing_paths) != len(set(missing_paths))
            or len(present_paths) != len(set(present_paths))
            or set(missing_paths).intersection(present_paths)
        ):
            raise ValueError("Python publication paths must be unique and disjoint")
        return self


def validated_python_publication_progress(progress: Any) -> PythonPublicationProgress:
    """Round-trip a publication witness so copied or constructed instances are validated."""
    if type(progress) is not PythonPublicationProgress:
        raise ValueError("Python publication progress must use its typed model")
    try:
        normalized = PythonPublicationProgress.model_validate(progress.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise ValueError("Python publication progress is malformed") from exc
    if not normalized.missing:
        raise ValueError("Python publication progress must identify missing outputs")
    return normalized


def validated_python_output_declarations(
    declarations: Any, *, manifest_path: str | None = None
) -> tuple[RunPythonOutputArtifact, ...]:
    """Round-trip and bound exact logical output descriptors before they become evidence."""
    if type(declarations) is not tuple or len(declarations) > MAX_PYTHON_PUBLICATION_OUTPUTS:
        raise ValueError("Python output declarations must be a bounded descriptor tuple")
    normalized: list[RunPythonOutputArtifact] = []
    exact_paths: set[str] = set()
    folded_paths: set[str] = set()
    manifest_key = manifest_path.casefold() if isinstance(manifest_path, str) else None
    for raw in declarations:
        if type(raw) is not RunPythonOutputArtifact:
            raise ValueError("Python output declarations must use the registered descriptor model")
        try:
            item = RunPythonOutputArtifact.model_validate(raw.model_dump(mode="python"))
            normalized_path = item.path.replace("\\", "/")
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise ValueError("Python output declaration is malformed") from exc
        canonical = canonical_artifact_name(normalized_path)
        path = item.path.replace("\\", "/")
        if path.casefold() == ".thymira" or path.casefold().startswith(".thymira/"):
            raise ValueError("output artifacts may not be stored in .thymira/")
        if (
            canonical != path
            or Path(normalized_path).is_absolute()
            or PureWindowsPath(normalized_path).is_absolute()
            or PureWindowsPath(normalized_path).drive
            or (manifest_key is not None and path.casefold() == manifest_key)
            or path in exact_paths
            or path.casefold() in folded_paths
        ):
            raise ValueError("Python output declaration paths must be canonical and unique")
        exact_paths.add(path)
        folded_paths.add(path.casefold())
        normalized.append(item.model_copy(update={"path": canonical}))
    if not normalized:
        raise ValueError("Python output declarations must not be empty")
    return tuple(normalized)


def validated_publication_result_progress(
    tool: Any, result: ToolResult, model: type[BaseModel]
) -> PythonPublicationProgress | None:
    """Accept only a typed run_python witness after a successful process and failed publication."""
    progress = result.publication_progress
    if progress is None:
        return None
    normalized = validated_python_publication_progress(progress)
    if (
        type(tool).__module__ != "thymira.tools.builtins.run_python"
        or type(tool).__name__ != "RunPython"
        or getattr(tool, "name", None) != "run_python"
        or model.__module__ != "thymira.tools.builtins.run_python"
        or model.__name__ != "PythonExecutionValue"
        or result.success
        or result.exit_code != 0
    ):
        raise ValueError("Python publication progress is outside its result contract")
    try:
        value = model.model_validate(result.value)
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError("Python publication progress requires its typed execution value") from exc
    if getattr(value, "exit_code", None) != 0:
        raise ValueError("Python publication progress requires a successful interpreter exit")
    declarations = validated_python_output_declarations(result.output_declarations)
    expected = {(item.path.replace("\\", "/"), item.kind) for item in declarations}
    observed = {
        *normalized.missing,
        *normalized.present,
    }
    if observed != expected or not normalized.missing:
        raise ValueError("Python publication progress must partition this call's outputs")
    return normalized


__all__ = [
    "MAX_PYTHON_PUBLICATION_OUTPUTS",
    "PythonPublicationProgress",
    "validated_publication_result_progress",
    "validated_python_output_declarations",
    "validated_python_publication_progress",
]
