"""Task-wide request limits reconstructed from durable provider-dispatch evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.usage import UsageLimits

from thymira.schemas import EventType

if TYPE_CHECKING:
    from thymira.agents.spec import AgentSpec
    from thymira.events import EventLog
    from thymira.schemas import Task


def step_request_limits(spec: AgentSpec, task: Task, event_log: EventLog) -> UsageLimits:
    """Keep approvals and conversation compaction from replenishing the task's turn budget."""
    owner = f"{task.id}:{task.agent_id}"
    spent = sum(
        event.run_id == task.run_id
        and event.type is EventType.MODEL_REQUEST_RECORDED
        and any(
            isinstance(item, dict)
            and item.get("fact") == "messages"
            and item.get("owner_id") == owner
            for item in event.payload.get("owners", ())
        )
        for event in event_log.events()
    )
    return UsageLimits(request_limit=max(0, spec.max_turns - spent))
