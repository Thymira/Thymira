"""Correlate a recorded review with its response for explanation, never authorization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.schemas import ActorKind, EventType, approval_decision_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.schemas import Event


def review_resolutions(trace: Sequence[Event]) -> dict[int, dict[str, object]]:
    """Keep review requests and their exact later human response together in bounded context."""
    result: dict[int, dict[str, object]] = {}
    for request in trace:
        if request.type is not EventType.HUMAN_APPROVAL_REQUESTED:
            continue
        decision = approval_decision_id(request.payload)
        if not isinstance(decision, str) or request.subject_id is None:
            continue
        responses = [
            event
            for event in trace
            if (
                event.seq > request.seq
                and event.run_id == request.run_id
                and event.type is EventType.HUMAN_APPROVAL
                and event.subject_id == request.subject_id
                and approval_decision_id(event.payload) == decision
                and event.actor.kind is ActorKind.HUMAN
                and event.actor.authenticated
                and event.payload.get("automatic", False) is False
                and isinstance(event.payload.get("approved"), bool)
            )
        ]
        resolution: dict[str, object] = {
            "state": "no_recorded_human_response",
            "decision_id": decision,
        }
        if len(responses) == 1:
            response = responses[0]
            resolution.update(
                {
                    "state": "human_approved" if response.payload["approved"] else "human_rejected",
                    "response_seq": response.seq,
                }
            )
        elif responses:
            resolution["state"] = "multiple_recorded_responses"
        result[request.seq] = resolution
    return result
