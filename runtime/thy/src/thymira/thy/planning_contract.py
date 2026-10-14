"""Model-facing plan choices, separated from execution-owned provenance and recovery state."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from thymira.schemas import ArtifactKind, Id, ThymiraModel
from thymira.thy.models import AgentTask, ArtifactRequirement

_RUNTIME_ARTIFACT_FIELDS = (
    "model_sha256",
    "dataset_sha256",
    "source_run_id",
    "source_model_artifact_id",
    "evaluation_split",
)
_TASK_FIELDS = ("model_input_ids", "recovery_of_position")


class ArtifactReference(ThymiraModel):
    """A prior-run artifact mentioned as context, never as a new output obligation."""

    name: str = Field(min_length=1)
    source_run_id: Id

    @field_validator("name")
    @classmethod
    def _workspace_relative_name(cls, value: str) -> str:
        """Keep referenced artifact names workspace-relative and host-independent."""
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ":" in normalized or ".." in path.parts:
            raise ValueError("artifact reference must be a workspace-relative path")
        return normalized


def _hide_evidence_fields(schema: dict[str, Any]) -> None:
    for name in _RUNTIME_ARTIFACT_FIELDS:
        schema.get("properties", {}).pop(name, None)


def _hide_task_fields(schema: dict[str, Any]) -> None:
    for name in _TASK_FIELDS:
        schema.get("properties", {}).pop(name, None)


def _hide_contextual_task_fields(schema: dict[str, Any]) -> None:
    _hide_task_fields(schema)
    schema.get("properties", {}).pop("required_artifacts", None)


class PlanningRequirement(ArtifactRequirement):
    """Accept only empty legacy provenance; never let a model invent runtime evidence."""

    model_config = ConfigDict(json_schema_extra=_hide_evidence_fields)

    @model_validator(mode="after")
    def _no_runtime_evidence(self) -> PlanningRequirement:
        if any(getattr(self, name) is not None for name in _RUNTIME_ARTIFACT_FIELDS):
            raise ValueError("artifact provenance is assigned by the runtime")
        return self


class ExactRequirement(PlanningRequirement):
    """A user-requested filename, available for every artifact kind."""

    match: Literal["exact"] = "exact"


class MetricRequirement(PlanningRequirement):
    """A content contract whose cross-field restrictions are visible in JSON Schema."""

    match: Literal["content"] = "content"
    kind: Literal[ArtifactKind.METRICS] = ArtifactKind.METRICS
    metric_keys: tuple[str, ...] = Field(min_length=1)


class PlanningTask(AgentTask):
    """A proposal may select deliverables but cannot populate execution bookkeeping."""

    model_config = ConfigDict(json_schema_extra=_hide_task_fields)
    required_artifacts: tuple[ExactRequirement | MetricRequirement, ...] = ()

    @model_validator(mode="after")
    def _no_runtime_bookkeeping(self) -> PlanningTask:
        if self.model_input_ids or self.recovery_of_position is not None:
            raise ValueError("input bindings and recovery positions are assigned by the runtime")
        return self


class ContextualPlanningTask(PlanningTask):
    """A contextual proposal with explicit outputs and separate prior-run references."""

    model_config = ConfigDict(json_schema_extra=_hide_contextual_task_fields)
    # Keep the old wire key parseable so the output validator can return a bounded, detailed
    # correction. It is hidden from the model schema and is never compiled into runtime output.
    required_artifacts: tuple[dict[str, Any], ...] = ()
    outputs_to_publish: tuple[ExactRequirement | MetricRequirement, ...] = Field(
        default=(),
        description=(
            "Files this task promises to publish for the current request. Keep prior-run inputs "
            "in artifact_references; registered dataset names are logical inputs, not outputs."
        ),
    )
    artifact_references: tuple[ArtifactReference, ...] = Field(
        default=(),
        description=(
            "Prior-run artifact inputs only, each with a linked source_run_id. Keep them separate "
            "from required outputs; registered dataset names are logical inputs, not references."
        ),
    )
