"""Recover created chat Runs without dispatching a running/parked/completed Run twice."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.api.routes.runs import _submit_run
from thymira.schemas import RunStatus
from thymira.state import OwnerBusyError, conversation_lock

if TYPE_CHECKING:
    from thymira.api.deps import RuntimeDeps


def submit_session_run(deps: RuntimeDeps, run_id: str) -> None:
    """Serialize HTTP retries through an OS lock and recheck durable execution state."""
    try:
        with conversation_lock(deps.session_chat.root / "dispatch", run_id):
            if deps.run_service.get_run(run_id).status is RunStatus.CREATED:
                _submit_run(deps, run_id)
    except OwnerBusyError:
        # The first dispatcher owns this Run. A duplicate request must not fail its lifecycle.
        return
