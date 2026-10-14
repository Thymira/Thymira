"""Project-confined file browsing and optimistic text edits."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from thymira.api.deps import RuntimeDeps, get_runtime_deps
from thymira.api.errors import problem
from thymira.api.permissions import Permission
from thymira.api.principal import require_permission
from thymira.api.routes._scope import _configured_project
from thymira.core import ProjectFileError, list_project_files
from thymira.core.project_files import (
    MAX_FILE_BYTES,
    ProjectFileConflictError,
    ProjectFileReadOnlyError,
    ProjectFileTooLargeError,
    read_project_file_details,
    write_project_file,
)
from thymira.schemas import ThymiraModel

router = APIRouter(prefix="/project", tags=["project"])
_DEPS = Depends(get_runtime_deps)
_READ = Depends(require_permission(Permission.READ))
_WRITE = Depends(require_permission(Permission.WRITE))


class ProjectFileView(ThymiraModel):
    """One entry in a bounded directory listing."""

    path: str
    name: str
    kind: Literal["file", "directory"]


class ProjectFileList(ThymiraModel):
    """The visible entries in a single project directory."""

    path: str
    entries: tuple[ProjectFileView, ...]
    truncated: bool


class ProjectFileContent(ThymiraModel):
    """A bounded text preview with an explicit revision and editability decision."""

    path: str
    content: str
    truncated: bool
    sha256: str | None
    editable: bool
    read_only_reason: Literal["truncated", "redacted"] | None


class ProjectFileUpdate(ThymiraModel):
    """A compare-and-set request for one existing bounded project text file."""

    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=MAX_FILE_BYTES)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/files", response_model=ProjectFileList, dependencies=[_READ])
def list_files(
    path: str = Query(default="", max_length=1024), deps: RuntimeDeps = _DEPS
) -> ProjectFileList:
    """Browse a single directory inside the configured project."""
    _configured_project(deps)
    if deps.project_resolution is None:
        raise problem(503, "project_not_configured", "The project workspace is unavailable.")
    try:
        entries, truncated = list_project_files(deps.project_resolution.project_dir, path)
    except (OSError, ProjectFileError) as exc:
        raise problem(404, "file_unavailable", "This project directory is unavailable.") from exc
    return ProjectFileList(
        path=path,
        entries=tuple(
            ProjectFileView(
                path=entry.path,
                name=entry.name,
                kind="directory" if entry.directory else "file",
            )
            for entry in entries
        ),
        truncated=truncated,
    )


@router.get("/file", response_model=ProjectFileContent, dependencies=[_READ])
def read_file(
    path: str = Query(min_length=1, max_length=1024), deps: RuntimeDeps = _DEPS
) -> ProjectFileContent:
    """Preview one regular UTF-8 file and report whether its source can be edited safely."""
    _configured_project(deps)
    if deps.project_resolution is None:
        raise problem(503, "project_not_configured", "The project workspace is unavailable.")
    try:
        preview = read_project_file_details(deps.project_resolution.project_dir, path)
    except (OSError, UnicodeError, ProjectFileError) as exc:
        raise problem(404, "file_unavailable", "This file cannot be previewed.") from exc
    return ProjectFileContent(
        path=path,
        content=preview.content,
        truncated=preview.truncated,
        sha256=preview.sha256,
        editable=preview.editable,
        read_only_reason=preview.read_only_reason,
    )


@router.put("/file", response_model=ProjectFileContent, dependencies=[_WRITE])
def update_file(request: ProjectFileUpdate, deps: RuntimeDeps = _DEPS) -> ProjectFileContent:
    """Replace an existing safe text file only when its read revision is still current."""
    _configured_project(deps)
    if deps.project_resolution is None:
        raise problem(503, "project_not_configured", "The project workspace is unavailable.")
    try:
        revision = write_project_file(
            deps.project_resolution.project_dir,
            request.path,
            request.content,
            expected_sha256=request.expected_sha256,
        )
    except ProjectFileConflictError as exc:
        raise problem(
            409,
            "project_file_conflict",
            "The file changed after it was read; reload it before saving.",
        ) from exc
    except ProjectFileReadOnlyError as exc:
        raise problem(422, "project_file_read_only", str(exc)) from exc
    except ProjectFileTooLargeError as exc:
        raise problem(413, "project_file_too_large", str(exc)) from exc
    except (OSError, UnicodeError, ProjectFileError) as exc:
        raise problem(404, "file_unavailable", "This file cannot be edited.") from exc
    return ProjectFileContent(
        path=revision.path,
        content=revision.content,
        truncated=False,
        sha256=revision.sha256,
        editable=True,
        read_only_reason=None,
    )


__all__ = ["router"]
