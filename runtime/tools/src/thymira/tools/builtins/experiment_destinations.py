"""Argument and preflight rules for run_experiment artifact destinations."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from thymira.schemas import SandboxEnforcement, SandboxMode
from thymira.tools.builtins.argument_contracts import DESCRIPTION_FIELD, Description
from thymira.tools.models import ToolResult


class RunExperimentArguments(BaseModel):
    """Arguments for the deterministic baseline experiment."""

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=1)
    target_column: str = Field(min_length=1)
    description: Description = DESCRIPTION_FIELD
    experiment_name: str = Field(default="thymira-experiment", min_length=1)
    model_artifact_name: str | None = Field(default=None, min_length=1)
    metrics_artifact_name: str | None = Field(default=None, min_length=1)
    seed: int = Field(default=7, ge=0)
    timeout_s: float = Field(default=120.0, gt=0, le=600)
    code: str | None = Field(default=None, max_length=100_000)

    @model_validator(mode="before")
    @classmethod
    def _validate_destinations(cls, value: Any) -> Any:
        """Keep destination policy in a pure function and leave the model one validator."""
        del cls
        return validate_argument_destinations(value)


def validate_argument_destinations(value: Any) -> Any:
    """Normalize and validate both optional output paths before tool authorization."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    result["model_artifact_name"] = _validate_artifact_name(
        value.get("model_artifact_name"), ".joblib", "binary model"
    )
    result["metrics_artifact_name"] = _validate_artifact_name(
        value.get("metrics_artifact_name"), ".json", "metrics"
    )
    model_name = result["model_artifact_name"]
    metrics_name = result["metrics_artifact_name"]
    if (
        isinstance(model_name, str)
        and isinstance(metrics_name, str)
        and model_name
        and model_name.casefold() == metrics_name.casefold()
    ):
        raise ValueError("model and metrics artifact destinations collide")
    return result


def _validate_artifact_name(value: Any, suffix: str, label: str) -> Any:
    """Normalize one destination while allowing Pydantic to report type/empty errors."""
    if not isinstance(value, str) or not value:
        return value
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or PureWindowsPath(normalized).is_absolute()
        or PureWindowsPath(normalized).drive
        or ".." in path.parts
        or normalized == ".thymira"
        or normalized.startswith(".thymira/")
    ):
        raise ValueError("artifact name must be workspace-relative")
    if path.suffix.casefold() != suffix:
        if label == "binary model":
            raise ValueError("binary model artifacts must use the .joblib extension")
        raise ValueError("metrics artifacts must use the .json extension")
    return normalized


def preflight_artifact_destinations(
    arguments: dict[str, Any], mode: SandboxMode
) -> tuple[str, str] | ToolResult:
    """Validate output paths before staging, training or tracker mutation begins."""
    experiment_name = arguments.get("experiment_name", "thymira-experiment")
    model_name = (
        arguments.get("model_artifact_name") or f"models/{experiment_name}.joblib"
    ).replace("\\", "/")
    metrics_name = (
        arguments.get("metrics_artifact_name") or f"metrics/{experiment_name}.json"
    ).replace("\\", "/")
    if model_name.casefold() == metrics_name.casefold():
        return _destination_refusal(mode, "model and metrics artifact destinations collide")

    names = ((model_name, ".joblib", "binary model"), (metrics_name, ".json", "metrics"))
    for name, suffix, label in names:
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or PureWindowsPath(name).is_absolute()
            or PureWindowsPath(name).drive
            or ".." in path.parts
            or name != path.as_posix()
            or path.parts[0] == ".thymira"
        ):
            return _destination_refusal(
                mode, f"{label} artifact name must be canonical and workspace-relative"
            )
        if path.suffix.casefold() != suffix:
            return _destination_refusal(mode, f"{label} artifact must use the {suffix} extension")
    return model_name, metrics_name


def _destination_refusal(mode: SandboxMode, reason: str) -> ToolResult:
    """Report a refusal before a sandbox process or artifact write has started."""
    return ToolResult(
        success=False,
        exit_code=125,
        error=reason,
        sandbox_mode=mode,
        sandbox_enforcement=SandboxEnforcement.UNUSABLE,
    )


__all__ = ["RunExperimentArguments", "preflight_artifact_destinations"]
