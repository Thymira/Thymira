"""Readiness checks for the project datasets needed by a new conversation turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from thymira.core.project_inputs import (
    CONFIG_FILE,
    THYMIRA_DIRECTORY,
    ProjectInputError,
    ProjectInputs,
)


@dataclass(frozen=True, slots=True)
class MissingSessionDataset:
    """A declared dataset whose project-relative source file is absent."""

    name: str
    path: str


class SessionInputsMissingError(RuntimeError):
    """Raised when a new conversation turn has declared datasets missing from its project."""

    def __init__(self, datasets: tuple[MissingSessionDataset, ...]) -> None:
        self.datasets = datasets
        super().__init__("one or more declared project datasets are missing")


class SessionInputsUnavailableError(RuntimeError):
    """Raised when declared project datasets cannot be checked safely."""


def require_session_inputs_ready(workspace: Path) -> None:
    """Refuse new turns when their configured project declares an absent dataset.

    The API's existing Run/session conventions pass the project's ``.thymira`` directory as
    ``workspace``. A project root is also accepted when it has a config file. Minimal in-process
    callers without a project config have no declared datasets and remain usable.

    Raises:
        SessionInputsMissingError: A declared dataset file is absent.
        SessionInputsUnavailableError: The project configuration could not be read or validated.
    """
    supplied = Path(workspace)
    if supplied.name == THYMIRA_DIRECTORY:
        project_dir = supplied.parent
    elif (supplied / THYMIRA_DIRECTORY / CONFIG_FILE).is_file():
        project_dir = supplied
    else:
        return

    try:
        datasets = ProjectInputs(project_dir).datasets()
        project_root = project_dir.resolve()
        if any(
            not (project_root / dataset.path).resolve().is_relative_to(project_root)
            for dataset in datasets
        ):
            raise SessionInputsUnavailableError
    except (OSError, UnicodeError, ProjectInputError) as exc:
        raise SessionInputsUnavailableError from exc

    missing = tuple(
        MissingSessionDataset(name=dataset.name, path=dataset.path)
        for dataset in datasets
        if not dataset.present
    )
    if missing:
        raise SessionInputsMissingError(missing)


__all__ = [
    "MissingSessionDataset",
    "SessionInputsMissingError",
    "SessionInputsUnavailableError",
    "require_session_inputs_ready",
]
