"""Recover risk answers and authorize a Run continuation under its review lock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from thymira.core.execution_review import (
    PLAN_RESUME_TARGET,
    block_rejected_execution,
    continuation_target,
    human_approval_outcome,
    parked_review_decision,
    pending_review_decision,
    review_resume_target,
)
from thymira.core.human_review import RunApprovalError, RunNotAwaitingApprovalError
from thymira.core.run_state import RunTransitionKind
from thymira.schemas import RunCondition

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.core.control_plane import RunController
    from thymira.core.risk_interview import RiskInterviewService
    from thymira.schemas import Actor, Event, Id, PolicyDecision, Run


class RunNotResumableError(RuntimeError):
    """Raised when a Run cannot be continued by the configured execution dispatcher."""


class RunInformationRequiredError(RunApprovalError):
    """Raised when a Run must receive its pending activity-profile answer before resuming."""


@dataclass(frozen=True, slots=True)
class RunResumeDependencies:
    """Collaborators used while the caller holds the Run's review lock."""

    controller: RunController
    read_events: Callable[[Id], list[Event]]
    interview: RiskInterviewService | None
    dispatcher_available: bool
    check_audit: Callable[[Id, PolicyDecision | None], None]
    check_plan_review: Callable[[Id, PolicyDecision], None]


def prepare_run_resume(run: Run, actor: Actor, deps: RunResumeDependencies) -> bool:
    """Recover published answers and authorize one transition under the caller's review lock."""
    run_id = run.id
    current = deps.controller.current_state(run_id)
    if current.condition is RunCondition.TERMINAL:
        return False
    _refuse_active_resume(run_id, current.condition)
    if not deps.dispatcher_available:
        raise RunNotResumableError("no execution dispatcher is configured")
    if (
        current.condition is RunCondition.WAITING
        and current.wait_reason is not None
        and current.wait_reason.value == "information"
        and deps.interview is not None
    ):
        deps.interview.recover_answer_publication(run)
        current = deps.controller.current_state(run_id)
    parked: PolicyDecision | None = None
    if (
        current.condition is RunCondition.WAITING
        and current.wait_reason is not None
        and current.wait_reason.value == "approval"
    ):
        allowed, parked = _prepare_approval_resume(run_id, actor, deps)
        if not allowed:
            return False
    deps.check_audit(run_id, parked)
    if current.condition is RunCondition.WAITING and current.wait_reason is not None:
        if current.wait_reason.value == "information" and (
            deps.interview is None or deps.interview.has_pending_question(run_id)
        ):
            raise RunInformationRequiredError(
                f"run {run_id!r} is waiting for an activity-profile answer"
            )
        payload: dict[str, Any] = {"resumed_by": actor.to_json_dict()}
        if current.wait_reason.value == "information":
            payload["resume_from"] = "information"
        elif current.wait_reason.value == "approval":
            payload["resume_from"] = continuation_target(deps.read_events(run_id), parked)
        deps.controller.advance(run_id, RunTransitionKind.RESUME, payload=payload)
    elif current.condition is RunCondition.PAUSED:
        deps.controller.advance(
            run_id, RunTransitionKind.RESUME, payload={"resumed_by": actor.to_json_dict()}
        )
    return True


def _prepare_approval_resume(
    run_id: Id, actor: Actor, deps: RunResumeDependencies
) -> tuple[bool, PolicyDecision | None]:
    """Refuse unresolved reviews and preserve an out-of-band rejection during resume."""
    events = deps.read_events(run_id)
    parked = parked_review_decision(events, run_id)
    target = review_resume_target(events, parked.id) if parked is not None else None
    if target is not None and parked is not None:
        outcome = human_approval_outcome(events, parked)
        if outcome is None:
            raise RunNotAwaitingApprovalError(f"run {run_id!r} is waiting for human approval")
        if outcome is False:
            block_rejected_execution(deps.controller, run_id, events, parked, actor)
            return False, parked
        if target == PLAN_RESUME_TARGET:
            deps.check_plan_review(run_id, parked)
    elif pending_review_decision(events, run_id) is not None:
        raise RunNotAwaitingApprovalError(f"run {run_id!r} is waiting for human approval")
    return True, parked


def _refuse_active_resume(run_id: Id, condition: RunCondition) -> None:
    """Prevent a second dispatcher from racing the owner of an active Run."""
    if condition is RunCondition.ACTIVE:
        raise RunNotResumableError(f"run {run_id!r} is already active")
