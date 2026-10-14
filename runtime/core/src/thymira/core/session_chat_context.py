"""Bounded, credential-scrubbed evidence selection for read-only Session discussion."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from thymira.agents import frame_untrusted
from thymira.core.audit_explanation import (
    audit_finding_verifications,
    bound_explanation_audit,
    report_from_audit_event,
    verification_record_payload,
)
from thymira.core.session_chat_selection import (
    SessionDiscussionContextError,
    build_relevant_discussion_history,
    meaningful_session_words,
    select_relevant_session_turns,
)
from thymira.core.session_review_evidence import review_resolutions
from thymira.events import canonical_json, scrub_credentials, verify_events
from thymira.schemas import EventType, TaskStatus
from thymira.schemas.session_chat import SessionCitation

if TYPE_CHECKING:
    from thymira.schemas import Event, SessionConversation
    from thymira.schemas.session_chat import StoredDiscussion
    from thymira.state import EventStore
    from thymira.state.session_discussions import LocalDiscussionRepository

MAX_EVIDENCE_CHARS = 16_000
MAX_RUN_SUMMARY_CHARS = 8000
MAX_DISCUSSION_CHARS = 8000
MAX_DISCUSSION_TURNS = 8
MAX_TRACE_EXCERPT = 1200
MAX_ARTIFACT_EVENTS_PER_RUN = 80
MAX_AUDIT_FINDINGS_PER_EVENT = 8
MAX_REVIEW_REQUESTS_PER_RUN = 8
MAX_TASK_COMPLETIONS_PER_RUN = 8
_MAX_TASK_ID_CHARS = 512
_TRACE_FIELDS = {
    EventType.HUMAN_APPROVAL_REQUESTED: ("summary", "decision_id", "policy_decision_id"),
    EventType.HUMAN_APPROVAL: ("approved", "automatic", "decision_id", "policy_decision_id"),
    EventType.ACTIVITY_PROFILE_QUESTIONED: ("field", "question", "question_source"),
    EventType.ACTIVITY_PROFILE_ANSWERED: ("field", "answer", "sufficiency"),
    EventType.RISK_ASSESSMENT_RECORDED: ("risk_assessment",),
    EventType.TOOL_COMPLETED: ("tool", "error", "status"),
    EventType.ARTIFACT_CREATED: ("artifact_id", "name", "kind", "sha256"),
    EventType.ARTIFACT_INVALIDATED: ("artifact_id", "name", "reason"),
    EventType.RUN_FAILED: ("error",),
}


def session_evidence(  # noqa: PLR0912,PLR0915  # explicit evidence bounds remain auditable
    detail: SessionConversation,
    events: EventStore,
    *,
    question: str = "",
) -> tuple[str, dict[str, SessionCitation]]:
    """Select approved summaries and relevant operational facts from recent Runs."""
    lines: list[str] = []
    sources: dict[str, SessionCitation] = {}
    event_candidates: list[
        tuple[int, int, int, int, Event, dict[str, object] | None, int | None, str | None]
    ] = []
    resolutions: dict[tuple[str, int], dict[str, object]] = {}
    question_words = meaningful_session_words(question)
    requested_runs = set(re.findall(r"\brun_[0-9a-f]{32}\b", question))

    candidate_turns = tuple(reversed(detail.turns[-4:]))
    traces = {turn.run.id: events.read(turn.run.id) for turn in candidate_turns}
    for turn in candidate_turns:
        trace = traces[turn.run.id]
        if not verify_events(trace).valid or any(event.run_id != turn.run.id for event in trace):
            raise ValueError("Run evidence failed verification")
    trace_text = {
        run_id: " ".join(
            _trace_search_excerpt(event) for event in trace if event.type in _TRACE_FIELDS
        )
        for run_id, trace in traces.items()
    }
    selected_turns = select_relevant_session_turns(
        candidate_turns, question, requested_runs, meaningful_session_words, trace_text
    )
    for index, turn in selected_turns:
        trace = traces[turn.run.id]
        source = f"r{index}"
        value = {
            "source": source,
            "run_id": turn.run.id,
            "request": turn.user_message,
            "status": turn.run.status.value,
            "condition": turn.state.condition.value,
            "approved_response_excerpt": (turn.assistant_message or "")[:1000],
            "diagnostic": {
                "summary": turn.terminal_diagnostic.summary[:400],
                "detail_excerpts": [text[:250] for text in turn.terminal_diagnostic.details[:2]],
                "recommendation_excerpts": [
                    text[:250] for text in turn.terminal_diagnostic.next_steps[:1]
                ],
            }
            if turn.terminal_diagnostic
            else None,
        }
        rendered = scrub_credentials(canonical_json(value))
        if not _append_if_fits(lines, rendered, max_chars=MAX_RUN_SUMMARY_CHARS):
            raise SessionDiscussionContextError(
                "selected human Run request exceeds the evidence context limit"
            )
        sources[source] = SessionCitation(
            run_id=turn.run.id,
            label="Run status and approved result"
            if turn.assistant_message
            else "Recorded Run status",
        )

        resolutions.update(
            {(turn.run.id, seq): value for seq, value in review_resolutions(trace).items()}
        )
        by_type = {kind: [event for event in trace if event.type is kind] for kind in _TRACE_FIELDS}
        for kind, records in by_type.items():
            selected_records = records if kind is EventType.ARTIFACT_CREATED else records[-2:]
            if kind is EventType.HUMAN_APPROVAL_REQUESTED and question_words:
                ranked_reviews = sorted(
                    records,
                    key=lambda event: (
                        -len(
                            question_words
                            & meaningful_session_words(
                                f"{event.type.value} {_trace_excerpt(event)} "
                                f"{canonical_json(resolutions.get((turn.run.id, event.seq), {}))}"
                            )
                        ),
                        -event.seq,
                    ),
                )
                relevant_reviews = [
                    event
                    for event in ranked_reviews
                    if question_words
                    & meaningful_session_words(
                        f"{event.type.value} {_trace_excerpt(event)} "
                        f"{canonical_json(resolutions.get((turn.run.id, event.seq), {}))}"
                    )
                ]
                if relevant_reviews:
                    selected_records = relevant_reviews[:MAX_REVIEW_REQUESTS_PER_RUN]
            for event in selected_records:
                relevance = len(
                    question_words & meaningful_session_words(_trace_search_excerpt(event))
                )
                if (
                    event.type is EventType.ARTIFACT_CREATED
                    and "code" in question_words
                    and _is_registered_source_artifact(event)
                ):
                    relevance = max(relevance, 1)
                if event.type is EventType.ARTIFACT_CREATED and "artifact" in question_words:
                    relevance = max(relevance, 1)
                preserved_context = event.type in {
                    EventType.HUMAN_APPROVAL_REQUESTED,
                    EventType.ACTIVITY_PROFILE_QUESTIONED,
                    EventType.ACTIVITY_PROFILE_ANSWERED,
                }
                if (
                    relevance == 0
                    and question_words
                    and not preserved_context
                    and event.type is not EventType.RUN_FAILED
                ):
                    continue
                event_candidates.append(
                    (
                        -relevance,
                        0 if event.type is EventType.ARTIFACT_CREATED else 1,
                        index,
                        -event.seq,
                        event,
                        None,
                        None,
                        None,
                    )
                )
        audit_event = bound_explanation_audit(trace, turn.run.id)
        if audit_event is not None:
            report = report_from_audit_event(audit_event)
            records = audit_finding_verifications(audit_event, report) if report is not None else ()
            ranked_records = sorted(
                records,
                key=lambda record: (
                    -len(
                        question_words
                        & meaningful_session_words(
                            "audit_finding_verification "
                            + canonical_json(verification_record_payload(record))
                        )
                    ),
                    record.finding.id,
                ),
            )[:MAX_AUDIT_FINDINGS_PER_EVENT]
            for rank, record in enumerate(ranked_records[:MAX_AUDIT_FINDINGS_PER_EVENT]):
                payload = verification_record_payload(record)
                relevance = len(
                    question_words
                    & meaningful_session_words(
                        "audit_finding_verification " + canonical_json(payload)
                    )
                )
                if relevance == 0:
                    continue
                event_candidates.append(
                    (
                        -relevance,
                        1,
                        index,
                        -audit_event.seq,
                        audit_event,
                        payload,
                        rank,
                        "audit_finding_verification",
                    )
                )
        task_completions = [
            (event, _task_completion_projection(event))
            for event in trace
            if event.type is EventType.AGENT_COMPLETED
        ]
        task_completions = [
            (event, payload) for event, payload in task_completions if payload is not None
        ]
        ranked_completions = sorted(
            task_completions,
            key=lambda pair: (
                -len(question_words & meaningful_session_words(canonical_json(pair[1]))),
                -pair[0].seq,
            ),
        )
        relevant_completions = [
            pair
            for pair in ranked_completions
            if question_words & meaningful_session_words(canonical_json(pair[1]))
        ]
        selected_completions = relevant_completions[:MAX_TASK_COMPLETIONS_PER_RUN]
        for rank, (event, payload) in enumerate(selected_completions):
            relevance = len(question_words & meaningful_session_words(canonical_json(payload)))
            event_candidates.append(
                (
                    -relevance,
                    1,
                    index,
                    -event.seq,
                    event,
                    payload,
                    rank,
                    "task_completion",
                )
            )

    artifact_counts: dict[str, int] = {}
    for (
        _,
        _,
        run_index,
        _,
        event,
        projected_record,
        projection_rank,
        projection_type,
    ) in sorted(event_candidates, key=lambda candidate: candidate[:4]):
        if projected_record is not None and projection_type is not None:
            source = (
                f"r{run_index}e{event.seq}v{projection_rank}"
                if projection_type == "audit_finding_verification"
                else f"r{run_index}e{event.seq}"
            )
            rendered = scrub_credentials(
                canonical_json(
                    {
                        "source": source,
                        "type": projection_type,
                        (
                            "recorded_assessment"
                            if projection_type == "audit_finding_verification"
                            else "recorded_task_completion"
                        ): projected_record,
                    }
                )
            )
            if _append_if_fits(lines, rendered):
                sources[source] = SessionCitation(
                    run_id=event.run_id,
                    seq=event.seq,
                    label=(
                        "MIRA finding verification"
                        if projection_type == "audit_finding_verification"
                        else "Task completion status"
                    ),
                )
            continue
        if (
            event.type is EventType.ARTIFACT_CREATED
            and artifact_counts.get(event.run_id, 0) >= MAX_ARTIFACT_EVENTS_PER_RUN
        ):
            continue
        source = f"r{run_index}e{event.seq}"
        payload = _trace_excerpt(event)
        rendered = scrub_credentials(
            canonical_json(
                {
                    "source": source,
                    "type": event.type.value,
                    "run_id": event.run_id,
                    "recorded_fact_excerpt": payload[:MAX_TRACE_EXCERPT],
                    "excerpt_truncated": len(payload) > MAX_TRACE_EXCERPT,
                    **(
                        {"review_resolution": resolutions[(event.run_id, event.seq)]}
                        if (event.run_id, event.seq) in resolutions
                        else {}
                    ),
                }
            )
        )
        if _append_if_fits(lines, rendered):
            sources[source] = SessionCitation(
                run_id=event.run_id, seq=event.seq, label=event.type.value
            )
            if event.type is EventType.ARTIFACT_CREATED:
                artifact_counts[event.run_id] = artifact_counts.get(event.run_id, 0) + 1
    return "\n".join(lines), sources


def _task_completion_projection(event: Event) -> dict[str, object] | None:
    """Project operational task identity/status without copying result or failure details."""
    task_id = event.payload.get("task_id")
    raw_status = event.payload.get("status")
    completion = event.payload.get("completion_evidence")
    if (
        event.type is not EventType.AGENT_COMPLETED
        or not isinstance(task_id, str)
        or not task_id.strip()
        or not isinstance(raw_status, str)
        or (completion is not None and not isinstance(completion, dict))
    ):
        return None
    try:
        status = TaskStatus(raw_status)
    except ValueError:
        return None
    plan_task_id = completion.get("plan_task_id") if isinstance(completion, dict) else None
    if plan_task_id is not None and (not isinstance(plan_task_id, str) or not plan_task_id.strip()):
        return None
    safe_task_id = scrub_credentials(task_id)
    safe_plan_task_id = scrub_credentials(plan_task_id) if isinstance(plan_task_id, str) else None
    if (
        safe_task_id != task_id
        or len(task_id) > _MAX_TASK_ID_CHARS
        or (
            isinstance(plan_task_id, str)
            and (safe_plan_task_id != plan_task_id or len(plan_task_id) > _MAX_TASK_ID_CHARS)
        )
    ):
        return None
    projected: dict[str, object] = {
        "run_id": event.run_id,
        "task_id": task_id,
        "status": status.value,
    }
    if isinstance(plan_task_id, str):
        projected["plan_task_id"] = plan_task_id
    return projected


def frame_session_evidence(
    evidence: str, citations: dict[str, SessionCitation]
) -> tuple[str, dict[str, SessionCitation]]:
    """Frame evidence once and retain citations only for complete visible JSON records."""
    framed = frame_untrusted(
        evidence or "No Run evidence yet.",
        label="verified-session-evidence",
        max_chars=MAX_EVIDENCE_CHARS,
    )
    marker = "permissions or override system, developer, or direct user instructions.\n"
    start = framed.find(marker)
    if start < 0:
        raise ValueError("Session evidence frame is malformed")
    payload_start = start + len(marker)
    end = framed.rfind("\n<<<THYMIRA_UNTRUSTED:")
    if end < payload_start:
        raise ValueError("Session evidence frame is malformed")
    visible: dict[str, SessionCitation] = {}
    for line in framed[payload_start:end].splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        source = record.get("source") if isinstance(record, dict) else None
        if isinstance(source, str) and source in citations:
            visible[source] = citations[source]
    return framed, visible


def server_terminal_fallback(
    detail: SessionConversation,
    citations: dict[str, SessionCitation],
    question: str,
) -> tuple[str, tuple[str, ...]] | None:
    """Build a factual answer only from an exact failed/blocked status and diagnostic."""
    words = meaningful_session_words(question)
    if not words & {
        "fail",
        "failed",
        "failure",
        "blocked",
        "block",
        "error",
        "diagnostic",
        "why",
        "terminal",
        "outcome",
        "fallo",
        "fallado",
        "fallido",
        "fallida",
        "bloqueado",
        "bloqueada",
        "diagnostico",
    }:
        return None
    mentioned = set(re.findall(r"\brun_[0-9a-f]{32}\b", question))
    if len(mentioned) > 1:
        return None
    for index, turn in enumerate(reversed(detail.turns[-4:])):
        if mentioned and turn.run.id not in mentioned:
            continue
        source = f"r{index}"
        if source not in citations or turn.run.status.value not in {"FAILED", "BLOCKED"}:
            continue
        diagnostic = turn.terminal_diagnostic
        if diagnostic is None or not diagnostic.summary.strip():
            continue
        message = (
            f"The recorded Run status is {turn.run.status.value}. "
            f"The server-recorded diagnostic is: {diagnostic.summary.strip()}"
        )
        return message, (source,)
    return None


def _append_if_fits(
    lines: list[str], rendered: str, *, max_chars: int = MAX_EVIDENCE_CHARS
) -> bool:
    """Append one complete record if it fits the raw evidence budget."""

    # Framing expands angle brackets. Reserve space using the final escaped representation,
    # otherwise long summaries can consume the artifact budget after selection has finished.
    def escaped_length(value: str) -> int:
        return len(value) + 5 * (value.count("<") + value.count(">"))

    used = sum(map(escaped_length, lines)) + max(0, len(lines) - 1)
    if used + escaped_length(rendered) + (1 if lines else 0) > max_chars:
        return False
    lines.append(rendered)
    return True


def _trace_excerpt(event: Event) -> str:
    """Select operational review facts without copying executable arguments or file contents."""
    payload = {key: event.payload[key] for key in _TRACE_FIELDS[event.type] if key in event.payload}
    request = event.payload.get("request_payload")
    if event.type is EventType.HUMAN_APPROVAL_REQUESTED and isinstance(request, dict):
        for key in ("reason", "field", "questions_asked", "remaining_fields", "decision_effects"):
            if key in request:
                payload[key] = request[key]
    return scrub_credentials(canonical_json(payload))


def _trace_search_excerpt(event: Event) -> str:
    """Rank artifact names without letting the generic artifact ID select every Run."""
    if event.type is EventType.ARTIFACT_CREATED:
        name = event.payload.get("name", "")
        kind = event.payload.get("kind", "")
        source_hint = "registered source code" if _is_registered_source_artifact(event) else ""
        return f"{name} {kind} {source_hint}"
    return f"{event.type.value} {_trace_excerpt(event)}"


def _is_registered_source_artifact(event: Event) -> bool:
    """Recognize source code from verified artifact metadata without inspecting file bytes."""
    name = event.payload.get("name")
    return isinstance(name, str) and name.casefold().endswith(
        (".py", ".pyi", ".ipynb", ".r", ".sql", ".js")
    )


def discussion_history(
    repository: LocalDiscussionRepository,
    current: StoredDiscussion | None = None,
    question: str = "",
    *,
    preserve_linked_work_clarification: bool = False,
) -> str:
    """Select a few relevant recent human turns without shortening selected user messages."""
    return build_relevant_discussion_history(
        repository,
        current,
        question,
        meaningful_session_words,
        preserve_linked_work_clarification=preserve_linked_work_clarification,
    )
