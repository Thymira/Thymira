"""Adversarial release checks over hash-chained response, audit, Gate and completion evidence."""

from __future__ import annotations

import pytest

from thymira.core.conversation_response import approved_conversation_response
from thymira.core.governance_binding import final_governance_binding
from thymira.events import InMemoryEventLog, verify_events
from thymira.mira.checks import AuditReport
from thymira.policies import FindingRule, Gate, Policy, PolicyEngine
from thymira.schemas import (
    Actor,
    AuditFinding,
    Decision,
    EventType,
    Framework,
    Run,
    RunCondition,
    RunOutcome,
    RunStage,
    RunState,
    Severity,
    new_id,
)


def _release_history(*, verdict=Decision.PASS, defect=None):
    """Make independently verifiable evidence, allowing one deliberate authorization defect."""
    run = Run(
        id=new_id("run"), project_id=new_id("project"), session_id=new_id("session"), prompt="Hi"
    )
    log = InMemoryEventLog(run.id)
    engine = PolicyEngine(
        Policy(
            name="release",
            version="1",
            finding_rules=(FindingRule(id="TEST", decision=verdict, reason="Test findings"),),
        )
    )
    log.append(EventType.RUN_STARTED, Actor.system(), subject_id=run.id)
    snapshot = log.events()[-1]
    log.append(
        EventType.AGENT_MESSAGE,
        Actor.system() if defect != "wrong-producer" else Actor(kind="agent", id="agent"),
        {"kind": "conversation.response", "content": "Reviewed result"},
        subject_id=run.id,
        producer="thymira.core",
    )
    if defect != "after-snapshot":
        snapshot = log.events()[-1]
    if defect == "rework-without-response":
        log.append(EventType.REWORK_STARTED, Actor.system(), subject_id=run.id)
        snapshot = log.events()[-1]
    finding = AuditFinding(
        id=new_id("finding"),
        run_id=run.id,
        control_id="TEST",
        framework=Framework.INTERNAL,
        title="Review",
        finding="Review this result",
        severity=Severity.LOW,
        confidence=1.0,
    )
    report = AuditReport(
        run_id=run.id,
        status="passed",
        controls=(),
        findings=(finding,),
        terminal_hash=snapshot.hash,
        policy_sha256=engine.policy_sha256,
    )
    log.append(
        EventType.AUDIT_COMPLETED,
        Actor.system(),
        {"audit_report": report.model_dump(mode="json"), "audit_revision": 1},
        subject_id=run.id,
    )
    decision = Gate(engine, log).review_findings((finding,))
    assert decision.decision is verdict
    if verdict is Decision.REQUIRE_HUMAN_REVIEW and defect != "missing-approval":
        answers = (False, True) if defect == "rejected-then-approved" else (True,)
        for approved in answers:
            log.append(
                EventType.HUMAN_APPROVAL,
                Actor(kind="human", id="reviewer", authenticated=defect != "unauthenticated"),
                {
                    "decision_id": decision.id,
                    "approved": approved,
                    "automatic": defect == "automatic",
                },
                subject_id="wrong" if defect == "wrong-subject" else decision.subject_id,
            )
    binding = final_governance_binding(run.id, decision, log.events())
    if defect == "wrong-binding":
        binding["audit_terminal_hash"] = "0" * 64
    log.append(
        EventType.RUN_TRANSITIONED,
        Actor.system(),
        {"command": "complete", **({} if defect == "unbound-completion" else binding)},
        subject_id=run.id,
        producer="thymira.core",
    )
    state = RunState(
        stage=RunStage.REPORTING, condition=RunCondition.TERMINAL, outcome=RunOutcome.COMPLETED
    )
    assert verify_events(log.events()).valid
    return run, state, tuple(log.events())


@pytest.mark.parametrize(
    "verdict", [Decision.PASS, Decision.WARNING, Decision.REQUIRE_HUMAN_REVIEW]
)
def test_release_requires_audited_completion_and_exact_authorization(verdict):
    assert approved_conversation_response(*_release_history(verdict=verdict)) == "Reviewed result"


@pytest.mark.parametrize(
    "defect",
    [
        "after-snapshot",
        "wrong-producer",
        "rework-without-response",
        "wrong-binding",
        "unbound-completion",
    ],
)
def test_completed_flag_cannot_release_unbound_or_unaudited_response(defect):
    assert approved_conversation_response(*_release_history(defect=defect)) is None


@pytest.mark.parametrize(
    "defect",
    [
        "missing-approval",
        "automatic",
        "wrong-subject",
        "unauthenticated",
        "rejected-then-approved",
    ],
)
def test_completed_flag_cannot_release_an_unapproved_human_review(defect):
    assert (
        approved_conversation_response(
            *_release_history(verdict=Decision.REQUIRE_HUMAN_REVIEW, defect=defect)
        )
        is None
    )


def test_even_a_bound_completion_cannot_release_a_blocked_decision():
    assert approved_conversation_response(*_release_history(verdict=Decision.BLOCK)) is None
