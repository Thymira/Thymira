"""Project terminal conversation explanations from the exact closing event evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.core.audit_explanation import (
    audit_finding_verifications,
    report_from_audit_event,
    verification_record_payload,
)
from thymira.core.conversation_response import approved_conversation_response
from thymira.core.governance_binding import final_governance_binding, policy_decision_binding
from thymira.events import scrub_credentials
from thymira.policies import UnknownApprovalError, decision_from_events
from thymira.schemas import Actor, ConversationTurn, EventType, RunCondition, RunOutcome, Severity
from thymira.schemas.conversation import ConversationDiagnostic

if TYPE_CHECKING:
    from thymira.schemas import AuditFinding, Event, PolicyDecision, Run, RunState

_MAX_DETAILS = 8


def conversation_turn(run: Run, state: RunState, events: tuple[Event, ...]) -> ConversationTurn:
    """Build a turn after its owner and event chain have been verified by the workspace."""
    metadata = run.conversation
    if metadata is None:
        raise ValueError("a conversation turn requires conversation metadata")
    return ConversationTurn(
        id=run.id,
        ordinal=metadata.ordinal,
        user_message=metadata.message,
        assistant_message=approved_conversation_response(run, state, events),
        terminal_diagnostic=terminal_diagnostic(run, state, events),
        run=run,
        state=state,
    )


def terminal_diagnostic(  # noqa: PLR0911  # explicit evidence guards fail to a neutral explanation
    run: Run, state: RunState, events: tuple[Event, ...]
) -> ConversationDiagnostic | None:
    """Explain failed/blocked turns without releasing model output or altering persisted facts."""
    if state.condition is not RunCondition.TERMINAL or state.outcome not in (
        RunOutcome.BLOCKED,
        RunOutcome.FAILED,
    ):
        return None
    fallback = ConversationDiagnostic(
        summary="Run blocked." if state.outcome is RunOutcome.BLOCKED else "Run failed.",
        details=("Open the run to inspect its recorded status and evidence.",),
    )
    transitions = [event for event in events if event.type is EventType.RUN_TRANSITIONED]
    if not transitions:
        return fallback
    terminal = transitions[-1]
    if (
        terminal.run_id != run.id
        or terminal.actor != Actor.system()
        or terminal.producer != "thymira.core"
        or terminal.payload.get("outcome") != state.outcome.value
    ):
        return fallback
    if state.outcome is RunOutcome.FAILED:
        error = terminal.payload.get("error")
        return (
            ConversationDiagnostic(summary=_safe(error))
            if isinstance(error, str) and error
            else fallback
        )
    decision_id = terminal.payload.get("policy_decision_id")
    if not isinstance(decision_id, str):
        return fallback
    closing = tuple(event for event in events if event.seq < terminal.seq)
    try:
        decision = _bound_decision(run.id, terminal, closing, decision_id)
        details, next_steps = _decision_details(run.id, decision, terminal, closing)
    except (UnknownApprovalError, RuntimeError, ValueError):
        return fallback
    return ConversationDiagnostic(
        summary=_safe(decision.reason),
        details=details,
        next_steps=next_steps,
        policy_rule=_safe(decision.rule_id, limit=128),
    )


def _bound_decision(
    run_id: str,
    terminal: Event,
    events: tuple[Event, ...],
    decision_id: str,
) -> PolicyDecision:
    """Require a unique recorded decision and its exact identity on every blocked transition."""
    matching = [
        event
        for event in events
        if event.type is EventType.POLICY_DECISION and event.payload.get("id") == decision_id
    ]
    if len(matching) != 1 or matching[0].run_id != run_id:
        raise ValueError("terminal decision is not uniquely recorded in this Run")
    decision = decision_from_events(events, decision_id)
    binding = policy_decision_binding(decision)
    if (
        decision.run_id != run_id
        or terminal.payload.get("command") != "block"
        or any(terminal.payload.get(key) != value for key, value in binding.items())
    ):
        raise ValueError("terminal decision identity does not match its recorded binding")
    return decision


def _decision_details(  # noqa: PLR0912  # record binding and causal evidence require explicit guards
    run_id: str, decision: PolicyDecision, terminal: Event, events: tuple[Event, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Use only the finding snapshot bound to this terminal transition, critical items first."""
    if decision.subject_kind != "findings":
        return (), ()
    binding = final_governance_binding(run_id, decision, events)
    if any(terminal.payload.get(key) != value for key, value in binding.items()):
        raise ValueError("terminal decision has a mismatched audit binding")
    audit_event = next(event for event in events if event.seq == binding["audit_completed_seq"])
    report = report_from_audit_event(audit_event)
    if report is None:
        raise ValueError("bound audit report is malformed or belongs to another Run")
    verification = {
        item.finding.id: item for item in audit_finding_verifications(audit_event, report)
    }
    findings = sorted(
        report.findings, key=lambda finding: finding.severity is not Severity.CRITICAL
    )
    details: list[str] = []
    next_steps: list[str] = []
    for finding in findings:
        if finding.run_id != run_id:
            raise ValueError("diagnostic finding belongs to another Run")
        if finding.recommendation:
            recommendation = _safe(finding.recommendation)
            if recommendation not in next_steps:
                next_steps.append(recommendation)
        item = verification.get(finding.id)
        if item is None:
            raise ValueError("bound report finding has no verification projection")
        assessment = verification_record_payload(item)
        excerpts = dict(assessment)
        raw_truncated_fields = assessment.get("truncated_fields", ())
        truncated_fields = (
            {value for value in raw_truncated_fields if isinstance(value, str)}
            if isinstance(raw_truncated_fields, (list, tuple))
            else set()
        )
        for field, limit in (("control_id", 64), ("title", 120), ("finding", 180), ("reason", 180)):
            value = excerpts.get(field)
            if isinstance(value, str) and len(value) > limit:
                excerpts[field] = value[: limit - 1] + "…"
                truncated_fields.add(field)
        reason = excerpts.get("reason")
        reason_text = str(reason) if isinstance(reason, str) and reason else "no rationale recorded"
        detail = (
            f"Finding {finding.id} · {excerpts['control_id']} · {excerpts['title']}: "
            f"{excerpts['finding']} Verification verdict: {item.verdict}; "
            f"retained for Gate: {str(item.retained_for_gate).lower()}; reason: {reason_text}."
        )
        if truncated_fields:
            detail += f" Excerpted fields: {', '.join(sorted(truncated_fields))}."
        details.append(_safe(detail))
        for error in _tool_errors(finding, events):
            if error not in details:
                details.append(error)
        if len(details) >= _MAX_DETAILS:
            break
    return tuple(details[:_MAX_DETAILS]), tuple(next_steps[:_MAX_DETAILS])


def _tool_errors(finding: AuditFinding, events: tuple[Event, ...]) -> tuple[str, ...]:
    """Follow exact event/hash references instead of attributing unrelated historical errors."""
    errors: list[str] = []
    by_ref = {f"seq:{event.seq}": event for event in events}
    for evidence in finding.evidence:
        event = by_ref.get(evidence.ref) if evidence.kind == "event" else None
        if (
            event is None
            or event.run_id != finding.run_id
            or event.type is not EventType.TOOL_COMPLETED
            or (evidence.sha256 is not None and event.hash != evidence.sha256)
        ):
            continue
        error = event.payload.get("error")
        tool = event.payload.get("tool")
        if finding.control_id == "A19" and event.payload.get("sandbox_enforcement") == "partial":
            spec = event.payload.get("sandbox_spec")
            limits = spec.get("unenforced") if isinstance(spec, dict) else None
            if isinstance(limits, list) and all(isinstance(item, str) for item in limits):
                errors.append(_safe(f"{tool}: unenforced isolation limits: {', '.join(limits)}"))
            # A confinement finding does not assert that the program's computation failed.
            continue
        if isinstance(error, str) and error:
            errors.append(_safe(f"{tool}: {error}" if isinstance(tool, str) else error))
    return tuple(errors)


def _safe(value: str, *, limit: int = 1000) -> str:
    """Scrub before bounding so a truncated credential cannot evade source redaction."""
    safe = scrub_credentials(value)
    return safe if len(safe) <= limit else safe[: limit - 1] + "…"
