"""Read-only execution prerequisite status, never an authorization to execute."""

from typing import Literal

from thymira.schemas.base import ThymiraModel


class ToolRuntimeStatus(ThymiraModel):
    """Report what was checked for the configured Python execution backend."""

    status: Literal["ready", "unavailable", "unknown", "not_configured"]
    code: str
    backend: Literal["container", "local", "unknown"]
    summary: str
    next_step: str = ""
