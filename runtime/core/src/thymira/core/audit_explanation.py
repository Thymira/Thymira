"""Project exact MIRA finding-verification records for human explanations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from thymira.core.governance_binding import final_governance_binding
from thymira.events import canonical_json, scrub_credentials
from thymira.mira.checks import AuditReport
from thymira.policies import UnknownApprovalError, decision_from_events
from thymira.schemas import Actor, AuditFinding, Event, EventType

if TYPE_CHECKING:
    from collections.abc import Sequence

VerificationVerdict = Literal["supported", "refuted", "unavailable", "verdict_not_recorded"]


@dataclass(frozen=True, slots=True)
class FindingVerification:
    """One finding's recorded verification disposition, or its explicit absence."""

    finding: AuditFinding
    verdict: VerificationVerdict
    rationale: str | None
    retained_for_gate: bool


_RECORDS: tuple[tuple[str, str, VerificationVerdict], ...] = (
    ("verification_supported_findings", "verification_supported", "supported"),
    ("verification_unavailable_findings", "verification_unavailable", "unavailable"),
    ("verified_drops", "verified_dropped", "refuted"),
)


def audit_finding_verifications(  # noqa: PLR0912  # each exact metadata guard fails closed
    event: Event, report: AuditReport
) -> tuple[FindingVerification, ...]:
    """Return exact same-run verdict records and mark retained findings without one explicitly."""
    finding_ids = [finding.id for finding in report.findings]
    if (
        event.type is not EventType.AUDIT_COMPLETED
        or event.run_id != report.run_id
        or any(finding.run_id != event.run_id for finding in report.findings)
        or len(finding_ids) != len(set(finding_ids))
    ):
        return ()

    retained = {finding.id: finding for finding in report.findings}
    entries: dict[str, list[tuple[VerificationVerdict, AuditFinding, str]]] = {}
    invalid_ids: set[str] = set()

    for list_key, count_key, verdict in _RECORDS:
        raw_entries = event.payload.get(list_key, [])
        if not isinstance(raw_entries, list):
            continue
        count = event.payload.get(count_key)
        count_matches = (
            isinstance(count, int) and not isinstance(count, bool) and count == len(raw_entries)
        )
        for raw in raw_entries:
            raw_finding = raw.get("finding") if isinstance(raw, dict) else None
            raw_id = raw_finding.get("id") if isinstance(raw_finding, dict) else None
            if not isinstance(raw_id, str):
                continue
            if not count_matches or not isinstance(raw, dict):
                invalid_ids.add(raw_id)
                continue
            try:
                finding = AuditFinding.model_validate(raw_finding)
            except (TypeError, ValidationError):
                invalid_ids.add(raw_id)
                continue
            rationale = raw.get("reason")
            if (
                finding.run_id != event.run_id
                or finding.agent_id is None
                or not isinstance(rationale, str)
            ):
                invalid_ids.add(finding.id)
                continue
            expected = retained.get(finding.id)
            if verdict == "refuted":
                if expected is not None:
                    invalid_ids.add(finding.id)
                    continue
            elif expected is None or canonical_json(expected.model_dump(mode="json")) != (
                canonical_json(finding.model_dump(mode="json"))
            ):
                invalid_ids.add(finding.id)
                continue
            entries.setdefault(finding.id, []).append((verdict, finding, rationale))

    result: list[FindingVerification] = []
    for finding in report.findings:
        records = entries.get(finding.id, [])
        if finding.id not in invalid_ids and len(records) == 1:
            verdict, recorded_finding, rationale = records[0]
            if verdict != "refuted":
                result.append(
                    FindingVerification(
                        recorded_finding, verdict, rationale, retained_for_gate=True
                    )
                )
                continue
        result.append(FindingVerification(finding, "verdict_not_recorded", None, True))

    for finding_id, records in entries.items():
        if finding_id in retained or finding_id in invalid_ids or len(records) != 1:
            continue
        verdict, finding, rationale = records[0]
        if verdict == "refuted":
            result.append(FindingVerification(finding, verdict, rationale, retained_for_gate=False))
    return tuple(result)


def verification_record_payload(record: FindingVerification) -> dict[str, object]:
    """Render one bounded complete record without interpreting missing evidence as a verdict."""
    finding = record.finding
    truncated_fields: list[str] = []

    def bounded(field: str, value: str, limit: int) -> str:
        safe = scrub_credentials(value)
        if len(safe) > limit:
            truncated_fields.append(field)
            return safe[: limit - 1] + "…"
        return safe

    payload: dict[str, object] = {
        "finding_id": finding.id,
        "control_id": bounded("control_id", finding.control_id, 128),
        "title": bounded("title", finding.title, 200),
        "finding": bounded("finding", finding.finding, 240),
        "severity": finding.severity.value,
        "verdict": record.verdict,
        "retained_for_gate": record.retained_for_gate,
    }
    if record.rationale is not None:
        payload["reason"] = bounded("reason", record.rationale, 240)
        payload["reason_recorded"] = bool(record.rationale.strip())
    if truncated_fields:
        payload["truncated_fields"] = truncated_fields
    return payload


def report_from_audit_event(event: Event) -> AuditReport | None:  # noqa: PLR0911
    """Read a typed report from its exact audit event, rejecting malformed or foreign data."""
    if event.type is not EventType.AUDIT_COMPLETED:
        return None
    value = event.payload.get("audit_report")
    if not isinstance(value, dict):
        return None
    try:
        report = AuditReport.model_validate(value)
    except (TypeError, ValidationError):
        return None
    if report.run_id != event.run_id:
        return None
    finding_ids = [finding.id for finding in report.findings]
    if any(finding.run_id != event.run_id for finding in report.findings):
        return None
    if len(finding_ids) != len(set(finding_ids)):
        return None
    return report


def bound_explanation_audit(  # noqa: PLR0911,PLR0912
    events: Sequence[Event], run_id: str
) -> Event | None:
    """Select the audit report bound to this Run's current terminal or pending review."""
    if not events or any(event.run_id != run_id for event in events):
        return None
    transitions = [event for event in events if event.type is EventType.RUN_TRANSITIONED]
    if not transitions:
        return None
    transition = transitions[-1]
    if transition.actor != Actor.system() or transition.producer != "thymira.core":
        return None
    state = transition.payload.get("state")
    if not isinstance(state, dict):
        return None
    condition = state.get("condition")
    outcome = state.get("outcome")
    wait_reason = state.get("wait_reason")
    terminal = condition == "terminal" and outcome in {"blocked", "completed"}
    pending_review = condition == "waiting" and wait_reason == "approval"
    if not (terminal or pending_review):
        return None

    raw_decision = transition.payload.get("policy_decision")
    decision_id = transition.payload.get("policy_decision_id")
    if isinstance(raw_decision, dict):
        nested_id = raw_decision.get("id")
        if not isinstance(nested_id, str):
            return None
        if isinstance(decision_id, str) and decision_id != nested_id:
            return None
        decision_id = nested_id
    if not isinstance(decision_id, str):
        return None
    try:
        decision = decision_from_events(events, decision_id)
    except UnknownApprovalError:
        return None
    if decision.subject_kind != "findings":
        return None
    decision_event = next(
        (
            event
            for event in events
            if event.type is EventType.POLICY_DECISION and event.payload.get("id") == decision_id
        ),
        None,
    )
    if decision_event is None or decision_event.seq >= transition.seq:
        return None
    closing_events = tuple(event for event in events if event.seq <= decision_event.seq)
    try:
        binding = final_governance_binding(run_id, decision, closing_events)
    except (RuntimeError, ValueError):
        return None
    if terminal:
        if any(transition.payload.get(key) != value for key, value in binding.items()):
            return None
    elif raw_decision != decision.to_json_dict():
        return None
    seq = binding.get("audit_completed_seq")
    return next(
        (
            event
            for event in closing_events
            if event.type is EventType.AUDIT_COMPLETED and event.seq == seq
        ),
        None,
    )


__all__ = [
    "FindingVerification",
    "VerificationVerdict",
    "audit_finding_verifications",
    "bound_explanation_audit",
    "report_from_audit_event",
    "verification_record_payload",
]
