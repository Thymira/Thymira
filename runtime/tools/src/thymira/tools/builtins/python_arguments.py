"""Model-facing argument contracts for bounded Python execution."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from thymira.schemas import ArtifactKind
from thymira.tools.builtins.argument_contracts import DESCRIPTION_FIELD, Description
from thymira.tools.builtins.python_source import MAX_PYTHON_SOURCE_BYTES, validate_python_source

_MAX_OUTPUT_ARTIFACTS = 64


class RunPythonOutputArtifact(BaseModel):
    """A raw file produced or explicitly reused by ``run_python``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    kind: ArtifactKind = ArtifactKind.OTHER
    media_type: str | None = None
    reuse_artifact_id: str | None = Field(default=None, min_length=1)


class RunPythonArguments(BaseModel):
    """Validated arguments for inline Python or one registered source artifact."""

    # Provider schemas stay plain objects; require_one_source enforces exclusivity locally.
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PYTHON_SOURCE_BYTES,
        description="Inline source; provide exactly one of code or source_artifact_id.",
    )
    source_artifact_id: str | None = Field(
        default=None,
        min_length=1,
        description="Active .py code artifact ID returned by write_file; use instead of code.",
    )
    description: Description = DESCRIPTION_FIELD
    timeout_s: float = Field(default=30.0, gt=0, le=300)
    output_artifacts: tuple[RunPythonOutputArtifact, ...] = Field(
        default=(), max_length=_MAX_OUTPUT_ARTIFACTS
    )

    output_manifest_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description=(
            "Fresh workspace-relative JSON path written by the script: an array of output "
            "descriptors. Use instead of output_artifacts for data-dependent outputs."
        ),
    )

    @field_validator("code")
    @classmethod
    def validate_python_code(cls, code: str | None) -> str | None:
        """Reject invalid inline source before approval, preserving its bytes."""
        del cls
        return validate_python_source(code) if code is not None else None

    @model_validator(mode="after")
    def require_one_source(self) -> Self:
        """Accept exactly one inline source or registered source identity."""
        if (self.code is None) == (self.source_artifact_id is None):
            raise ValueError("provide exactly one of code or source_artifact_id")
        if self.output_manifest_path is not None and self.output_artifacts:
            raise ValueError("provide output_artifacts or output_manifest_path, not both")
        return self
