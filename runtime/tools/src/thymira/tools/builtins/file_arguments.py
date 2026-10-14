"""Validated input contracts for the workspace file tools."""

from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel, Field, model_validator

from thymira.schemas import ArtifactKind
from thymira.tools.builtins.argument_contracts import DESCRIPTION_FIELD, Description
from thymira.tools.builtins.python_source import validate_python_source

READ_DEFAULT_LIMIT = 2000


class ReadFileArguments(BaseModel):
    """Arguments for read_file."""

    path: str = Field(min_length=1)
    offset: int = Field(default=1, ge=1, description="1-based first line to return.")
    limit: int = Field(
        default=READ_DEFAULT_LIMIT,
        ge=1,
        le=20_000,
        description="Maximum number of lines to return. Defaults to 2000.",
    )


class WriteFileArguments(BaseModel):
    """Arguments for write_file."""

    path: str = Field(min_length=1)
    content: str
    description: Description = DESCRIPTION_FIELD
    kind: ArtifactKind | None = Field(
        default=None,
        description="When set, also register the written file as an Artifact of this kind.",
    )

    @model_validator(mode="after")
    def validate_registered_python(self) -> Self:
        """Reject unexecutable Python artifacts before the write enters human review."""
        if self.kind is ArtifactKind.CODE and PurePosixPath(self.path).suffix.lower() == ".py":
            validate_python_source(self.content)
        return self


class EditFileArguments(BaseModel):
    """Arguments for edit_file."""

    path: str = Field(min_length=1)
    old_string: str = Field(
        min_length=1, description="Literal text to replace. Must match exactly."
    )
    new_string: str = Field(
        description="Literal replacement text. Use an empty string to delete the match."
    )
    replace_all: bool = Field(
        default=False,
        description=(
            "Replace all matches. Defaults to false; then old_string must appear exactly once."
        ),
    )
    description: Description = DESCRIPTION_FIELD


class ListFilesArguments(BaseModel):
    """Arguments for list_files."""

    path: str = "."
