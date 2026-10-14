"""Terminal explanations expose operational failures only, never provisional model output."""

import pytest

from thymira.core.conversation_diagnostics import terminal_diagnostic
from thymira.core.governance_binding import final_governance_binding, policy_decision_binding
from thymira.events import InMemoryEventLog, verify_events
from thymira.mira.checks import AuditReport
from thymira.policies import Gate, PolicyEngine, load_policy_stack
from thymira.schemas import (
    Actor,
    AuditFinding,
    Decision,
    EventType,
    Framework,
    PolicyDecision,
    Run,
    RunCondition,
    RunOutcome,
    RunStage,
    RunState,
    Severity,
    new_id,
)


def _failed_history(error):
    run = Run(
        id=new_id("run"), project_id=new_id("project"), session_id=new_id("session"), prompt="Hi"
    )
    log = InMemoryEventLog(run.id)
    log.append(
        EventType.RUN_TRANSITIONED,
        Actor.system(),
        {
            "command": "fail",
            "outcome": "failed",
            "error": error,
        },
        producer="thymira.core",
    )
    state = RunState(
        stage=RunStage.EXECUTING, condition=RunCondition.TERMINAL, outcome=RunOutcome.FAILED
    )
    return run, state, log


def test_failure_diagnostic_is_scrubbed_and_bounded_at_read_time(monkeypatch):
    canary = new_id("artifact")
    run, state, log = _failed_history(f"Engine failed: {canary} " + "x" * 3000)
    # The credential was not known when the event was recorded; projection must scrub again.
    monkeypatch.setenv("OPENAI_API_KEY", canary)
    diagnostic = terminal_diagnostic(run, state, tuple(log.events()))
    assert diagnostic is not None
    assert len(diagnostic.summary) <= 1000
    assert canary not in diagnostic.model_dump_json()
    assert verify_events(log.events()).valid


@pytest.mark.parametrize("outcome", [RunOutcome.COMPLETED, RunOutcome.CANCELLED])
def test_historical_failure_is_not_displayed_as_current_after_another_terminal_outcome(outcome):
    run, state, log = _failed_history("Old failure")
    state = state.model_copy(update={"outcome": outcome})
    assert terminal_diagnostic(run, state, tuple(log.events())) is None


def test_missing_decision_does_not_attribute_unrelated_policy_or_model_text():
    run, _, log = _failed_history("Old failure")
    log.append(EventType.AGENT_MESSAGE, Actor.system(), {"content": "Unapproved conclusion"})
    log.append(
        EventType.RUN_TRANSITIONED,
        Actor.system(),
        {
            "command": "block",
            "outcome": "blocked",
            "policy_decision_id": new_id("decision"),
        },
        producer="thymira.core",
    )
    state = RunState(
        stage=RunStage.AUDITING, condition=RunCondition.TERMINAL, outcome=RunOutcome.BLOCKED
    )
    diagnostic = terminal_diagnostic(run, state, tuple(log.events()))
    assert diagnostic is not None
    assert diagnostic.summary == "Run blocked."
    assert diagnostic.policy_rule is None
    assert "Old failure" not in diagnostic.model_dump_json()
    assert "Unapproved conclusion" not in diagnostic.model_dump_json()


@pytest.mark.parametrize("oversized", [False, True])
def test_blocked_diagnostic_preserves_unavailable_finding_verdict_reason(oversized):
    """A bound critical finding stays explicitly unverified when MIRA could not judge it."""
    run = Run(
        id=new_id("run"), project_id=new_id("project"), session_id=new_id("session"), prompt="Hi"
    )
    log = InMemoryEventLog(run.id)
    log.append(EventType.RUN_STARTED, Actor.system(), subject_id=run.id, producer="thymira.core")
    log.append(
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {"kind": "conversation.response", "content": "Provisional analysis must stay hidden."},
        subject_id=run.id,
        producer="thymira.core",
    )
    finding = AuditFinding(
        id=new_id("finding"),
        run_id=run.id,
        agent_id=new_id("agent"),
        control_id="VERIFY-UNAVAILABLE",
        framework=Framework.INTERNAL,
        title=("Candidate requires review" + " title" * 100)
        if oversized
        else "Candidate requires review",
        finding=(
            "A recorded candidate has cited evidence that could not be fully judged."
            + " claim" * 600
            if oversized
            else "A recorded candidate has cited evidence that could not be fully judged."
        ),
        severity=Severity.CRITICAL,
        confidence=0.9,
        recommendation="Inspect the cited evidence before deciding what to do.",
    )
    engine = PolicyEngine(load_policy_stack())
    report = AuditReport(
        run_id=run.id,
        status="failed",
        controls=(),
        findings=(finding,),
        terminal_hash=log.events()[-1].hash,
        policy_sha256=engine.policy_sha256,
    )
    log.append(
        EventType.AUDIT_COMPLETED,
        Actor.system(),
        {
            "audit_report": report.model_dump(mode="json"),
            "audit_revision": 1,
            "verification_unavailable": 1,
            "verification_unavailable_findings": [
                {
                    "finding": finding.model_dump(mode="json"),
                    "reason": (
                        "The verifier returned an unavailable verdict for cited evidence."
                        + " rationale" * 100
                    )
                    if oversized
                    else "The verifier returned an unavailable verdict for cited evidence.",
                }
            ],
        },
        subject_id=run.id,
        producer="thymira.mira",
    )
    gate = Gate(engine, log)
    decision = gate.review_findings((finding,))
    assert decision.decision is Decision.BLOCK
    binding = final_governance_binding(run.id, decision, log.events(), (finding,))
    log.append(
        EventType.RUN_TRANSITIONED,
        Actor.system(),
        {"command": "block", "outcome": "blocked", **binding},
        subject_id=run.id,
        producer="thymira.core",
    )
    state = RunState(
        stage=RunStage.AUDITING, condition=RunCondition.TERMINAL, outcome=RunOutcome.BLOCKED
    )

    diagnostic = terminal_diagnostic(run, state, tuple(log.events()))

    assert diagnostic is not None
    rendered = diagnostic.model_dump_json()
    assert finding.id in rendered
    assert "Verification verdict: unavailable" in rendered
    assert "The verifier returned an unavailable verdict for cited evidence." in rendered
    if oversized:
        assert "Excerpted fields:" in rendered
        assert len(diagnostic.details[0]) <= 1000
    else:
        assert finding.title in rendered
        assert finding.finding in rendered
    assert "Provisional analysis must stay hidden." not in rendered
    assert verify_events(log.events()).valid


@pytest.mark.parametrize("defect", [None, "wrong-hash", "wrong-findings", "duplicate-decision"])
def test_run_level_block_requires_exact_unique_decision_binding(defect):
    run, _, log = _failed_history("Old failure")
    decision = PolicyDecision(
        id=new_id("decision"),
        run_id=run.id,
        subject_kind="run",
        subject_id="run",
        decision=Decision.BLOCK,
        rule_id="RISK-BLOCK",
        reason="The declared activity is prohibited.",
        policy_name="test",
        policy_sha256="a" * 64,
    )
    payload = decision.model_dump(mode="json")
    log.append(EventType.POLICY_DECISION, Actor.system(), payload)
    if defect == "duplicate-decision":
        log.append(EventType.POLICY_DECISION, Actor.system(), payload)
    binding = policy_decision_binding(decision)
    if defect == "wrong-hash":
        binding["policy_sha256"] = "b" * 64
    if defect == "wrong-findings":
        binding["finding_ids"] = [new_id("finding")]
    log.append(
        EventType.RUN_TRANSITIONED,
        Actor.system(),
        {
            "command": "block",
            "outcome": "blocked",
            **binding,
        },
        producer="thymira.core",
    )
    state = RunState(
        stage=RunStage.PLANNING, condition=RunCondition.TERMINAL, outcome=RunOutcome.BLOCKED
    )
    diagnostic = terminal_diagnostic(run, state, tuple(log.events()))
    assert diagnostic is not None
    assert diagnostic.policy_rule == (None if defect else "RISK-BLOCK")
    assert verify_events(log.events()).valid
