"""Settlement helpers for plan steps Execute stops without delegating."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.agents.settlement import Settlement, settle_stopped
from thymira.schemas import Actor, Task, new_id

if TYPE_CHECKING:
    from thymira.events import EventLog
    from thymira.thy.models import AgentTask, ThyState

_SKIP_REASON = "a dependency did not complete"


def stopped_settlement(
    state: ThyState,
    agent_task: AgentTask,
    event_log: EventLog,
    actor: Actor,
    *,
    reason: str = _SKIP_REASON,
) -> Settlement:
    """Settle a plan item Execute stopped, through the same writer a real delegation uses."""
    return settle_stopped(
        task=Task(
            id=new_id("task"),
            run_id=state.run.id,
            agent_id=new_id("agent"),
            objective=agent_task.instruction,
        ),
        parent_agent="thy",
        agent=agent_task.agent.value,
        delegation_depth=1,
        reason=reason,
        event_log=event_log,
        actor=actor,
    )
