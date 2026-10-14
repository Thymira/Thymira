"""One durable correction for Python outputs omitted from publication arguments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai import DeferredToolRequests, RunContext
from pydantic_ai.exceptions import ModelRetry

from thymira.agents.request_budget import step_request_limits
from thymira.agents.task_completion import assess_task_completion
from thymira.schemas import EventSurface, EventType

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from thymira.agents.spec import AgentSpec
    from thymira.agents.task_completion import TaskCompletionValidator
    from thymira.events import EventLog
    from thymira.schemas import Actor, Task
    from thymira.state import ArtifactStore

_FEEDBACK_FORM = "python_publication_feedback"
_PUBLICATION_CORRECTION_REQUESTS = 2


def publication_output_validator(
    spec: AgentSpec,
    task: Task,
    event_log: EventLog,
    store: ArtifactStore | None,
    validator: TaskCompletionValidator | None,
    actor: Actor,
) -> Callable[
    [RunContext[None], BaseModel | DeferredToolRequests], BaseModel | DeferredToolRequests
]:
    """Request publication once, preserving final validation and the task's request limit."""

    def validate(
        context: RunContext[None], output: BaseModel | DeferredToolRequests
    ) -> BaseModel | DeferredToolRequests:
        if (
            isinstance(output, DeferredToolRequests)
            or validator is None
            or getattr(output, "exit_code", None) != 0
            or not getattr(output, "artifacts", ())
        ):
            return output
        events = event_log.events()
        if any(
            event.type is EventType.AGENT_MESSAGE
            and event.payload.get("task_id") == task.id
            and event.payload.get("form") == _FEEDBACK_FORM
            for event in events
        ):
            return output
        evidence = validator(task, output, events, assess_task_completion(events, task.id, store))
        last_python = next(
            (attempt for attempt in reversed(evidence.attempts) if attempt.tool == "run_python"),
            None,
        )
        if (
            not evidence.validation_failures
            or evidence.unresolved_failures
            or last_python is None
            or not last_python.succeeded
            or last_python.arguments is None
            or last_python.arguments.get("output_artifacts")
            or last_python.arguments.get("output_manifest_path")
        ):
            return output
        durable_remaining = step_request_limits(spec, task, event_log).request_limit
        local_remaining = spec.max_turns - context.usage.requests
        if (
            durable_remaining is None
            or min(durable_remaining, local_remaining) < _PUBLICATION_CORRECTION_REQUESTS
        ):
            return output
        message = (
            "The final result cannot be accepted: "
            + "; ".join(evidence.validation_failures)
            + ". The successful run_python call omitted output publication. "
            "Correct it through run_python with output_artifacts listing the exact paths and "
            "kinds, or output_manifest_path naming a fresh JSON array written by the script. "
            "Use the registered source_artifact_id when available. The call still requires "
            "normal policy approval. Only registered, verified outputs may be claimed in "
            "final_result; do not remove requested deliverables to claim success. "
            "This is the task's only final-output publication correction."
        )
        event_log.append(
            EventType.AGENT_MESSAGE,
            actor,
            {"task_id": task.id, "form": _FEEDBACK_FORM, "text": message},
            subject_id=task.agent_id,
            surface=EventSurface.MODEL_VISIBLE,
        )
        raise ModelRetry(message)

    return validate
