"""Keep THY response evidence provisional until exact final governance authorizes release."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.core.execution_review import human_approval_outcome
from thymira.core.governance_binding import final_governance_binding
from thymira.events import scrub_credentials
from thymira.policies import UnknownApprovalError, decision_from_events
from thymira.schemas import Actor, Decision, EventType, RunCondition, RunOutcome

if TYPE_CHECKING:
    from thymira.events import EventLog
    from thymira.schemas import Event, Run, RunState


def record_conversation_response(
    run: Run, event_log: EventLog, summary: str | None, *, finished: bool
) -> None:
    """Record finished THY output as provisional evidence, before MIRA and the findings Gate."""
    if run.conversation is not None and finished and summary:
        event_log.append(
            EventType.AGENT_MESSAGE,
            Actor.system(),
            {"kind": "conversation.response", "content": summary[:16000], "provisional": True},
            subject_id=run.id,
            producer="thymira.core",
        )


def approved_conversation_response(
    run: Run, state: RunState, events: tuple[Event, ...]
) -> str | None:
    """Release only the response covered by an authorized, audited terminal completion.

    The caller verifies the event chain. Neither a finished THY pass, a projection's latest
    decision nor a bare COMPLETED flag is sufficient. Old response events without the provisional
    marker follow the same release rule; reads never rewrite the audit record.
    """
    transitions = [event for event in events if event.type is EventType.RUN_TRANSITIONED]
    if (
        state.condition is not RunCondition.TERMINAL
        or state.outcome is not RunOutcome.COMPLETED
        or not transitions
    ):
        return None
    terminal = transitions[-1]
    decision_id = terminal.payload.get("policy_decision_id")
    if (
        terminal.payload.get("command") != "complete"
        or terminal.actor != Actor.system()
        or terminal.producer != "thymira.core"
        or not isinstance(decision_id, str)
    ):
        return None
    closing_events = tuple(event for event in events if event.seq < terminal.seq)
    try:
        decision = decision_from_events(closing_events, decision_id)
        binding = final_governance_binding(run.id, decision, closing_events)
    except (UnknownApprovalError, RuntimeError, ValueError):
        return None
    if any(terminal.payload.get(key) != value for key, value in binding.items()):
        return None
    authorized = decision.decision in (Decision.PASS, Decision.WARNING) or (
        decision.decision is Decision.REQUIRE_HUMAN_REVIEW
        and human_approval_outcome(closing_events, decision) is True
    )
    if not authorized:
        return None
    return _audited_response(run.id, closing_events, binding["audit_terminal_hash"])


def _audited_response(run_id: str, events: tuple[Event, ...], audit_hash: str) -> str | None:
    """Select the last THY response of this pass and require inclusion in the audit snapshot."""
    candidate = None
    for event in events:
        if event.type is EventType.REWORK_STARTED:
            candidate = None
        elif (
            event.type is EventType.AGENT_MESSAGE
            and event.subject_id == run_id
            and event.actor == Actor.system()
            and event.producer == "thymira.core"
            and event.payload.get("kind") == "conversation.response"
        ):
            candidate = event
    snapshot = next((event for event in events if event.hash == audit_hash), None)
    if candidate is None or snapshot is None or candidate.seq > snapshot.seq:
        return None
    content = candidate.payload.get("content")
    return scrub_credentials(content) if isinstance(content, str) and content.strip() else None


__all__ = ["approved_conversation_response", "record_conversation_response"]
