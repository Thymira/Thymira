"""Exact MIRA verification records are projected conservatively for human explanations."""

from __future__ import annotations

import pytest

from thymira.core.audit_explanation import (
    audit_finding_verifications,
    report_from_audit_event,
    verification_record_payload,
)
from thymira.events import InMemoryEventLog
from thymira.mira.checks import AuditReport
from thymira.schemas import Actor, AuditFinding, EventType, Framework, Severity, new_id


def _finding(run_id: str, *, agent_id: str | None = None, finding_id: str | None = None):
    return AuditFinding(
        id=finding_id or new_id("finding"),
        run_id=run_id,
        agent_id=agent_id if agent_id is not None else new_id("agent"),
        control_id="TEST-VERIFY",
        framework=Framework.INTERNAL,
        title="Recorded candidate",
        finding="The candidate has a specific recorded claim.",
        severity=Severity.HIGH,
        confidence=0.8,
    )


def _event(report: AuditReport, **payload):
    log = InMemoryEventLog(report.run_id)
    return log.append(
        EventType.AUDIT_COMPLETED,
        Actor.system(),
        {"audit_report": report.model_dump(mode="json"), **payload},
        subject_id=report.run_id,
        producer="thymira.mira",
    )


def test_explicit_supported_refuted_and_unavailable_records_keep_distinct_meanings():
    run_id = new_id("run")
    supported = _finding(run_id)
    unavailable = _finding(run_id)
    dropped = _finding(run_id)
    report = AuditReport(
        run_id=run_id,
        status="failed",
        controls=(),
        findings=(supported, unavailable),
    )
    event = _event(
        report,
        verification_supported=1,
        verification_supported_findings=[
            {"finding": supported.model_dump(mode="json"), "reason": ""}
        ],
        verification_unavailable=1,
        verification_unavailable_findings=[
            {"finding": unavailable.model_dump(mode="json"), "reason": "Evidence was incomplete."}
        ],
        verified_dropped=1,
        verified_drops=[
            {"finding": dropped.model_dump(mode="json"), "reason": "Evidence contradicted it."}
        ],
    )

    records = {
        item.finding.id: verification_record_payload(item)
        for item in audit_finding_verifications(event, report)
    }

    assert records[supported.id]["verdict"] == "supported"
    assert records[supported.id]["retained_for_gate"] is True
    assert records[supported.id]["reason_recorded"] is False
    assert records[unavailable.id]["verdict"] == "unavailable"
    assert records[unavailable.id]["retained_for_gate"] is True
    assert records[unavailable.id]["reason"] == "Evidence was incomplete."
    assert records[dropped.id]["verdict"] == "refuted"
    assert records[dropped.id]["retained_for_gate"] is False
    assert records[dropped.id]["reason"] == "Evidence contradicted it."


def test_legacy_retained_candidate_is_never_inferred_supported():
    run_id = new_id("run")
    finding = _finding(run_id)
    report = AuditReport(run_id=run_id, status="failed", controls=(), findings=(finding,))
    event = _event(report)

    [record] = audit_finding_verifications(event, report)

    assert record.verdict == "verdict_not_recorded"
    assert verification_record_payload(record)["retained_for_gate"] is True


@pytest.mark.parametrize("defect", ["foreign-run", "mutated-same-id", "contradictory", "bad-count"])
def test_malformed_or_conflicting_verdict_metadata_fails_closed(defect):
    run_id = new_id("run")
    finding = _finding(run_id)
    report = AuditReport(run_id=run_id, status="failed", controls=(), findings=(finding,))
    raw = finding.model_dump(mode="json")
    if defect == "foreign-run":
        raw["run_id"] = new_id("run")
    elif defect == "mutated-same-id":
        raw["finding"] = "A different claim now has the same ID."
    record = {"finding": raw, "reason": "Recorded verifier rationale."}
    payload = {
        "verification_supported": 1,
        "verification_supported_findings": [record],
    }
    if defect == "contradictory":
        payload.update(
            verification_unavailable=1,
            verification_unavailable_findings=[record],
        )
    elif defect == "bad-count":
        payload["verification_supported"] = 2
    event = _event(report, **payload)

    [projected] = audit_finding_verifications(event, report)

    assert projected.finding.id == finding.id
    assert projected.verdict == "verdict_not_recorded"
    assert projected.rationale is None


def test_forged_verdict_cannot_be_attached_to_deterministic_finding():
    run_id = new_id("run")
    finding = _finding(run_id).model_copy(update={"agent_id": None})
    report = AuditReport(run_id=run_id, status="failed", controls=(), findings=(finding,))
    event = _event(
        report,
        verification_supported=1,
        verification_supported_findings=[
            {"finding": finding.model_dump(mode="json"), "reason": "Forged support."}
        ],
    )

    [projected] = audit_finding_verifications(event, report)

    assert projected.verdict == "verdict_not_recorded"
    assert projected.rationale is None


@pytest.mark.parametrize("defect", ["foreign-retained", "duplicate-id"])
def test_malformed_report_is_not_projected(defect):
    run_id = new_id("run")
    finding = _finding(run_id)
    if defect == "foreign-retained":
        other_run_finding = finding.model_copy(update={"run_id": new_id("run")})
        findings = (other_run_finding,)
    else:
        duplicate = finding.model_copy(update={"title": "Duplicate identity"})
        findings = (finding, duplicate)
    report = AuditReport(run_id=run_id, status="failed", controls=(), findings=findings)
    event = _event(report)

    assert report_from_audit_event(event) is None
    assert audit_finding_verifications(event, report) == ()
