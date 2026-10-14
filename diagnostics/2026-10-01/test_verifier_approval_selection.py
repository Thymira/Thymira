"""Keep a cited Gate approval visible in an untruncated verifier request.

Real: Gate, default PolicyEngine, hash-chained event log and public verifier builder.
Fake: scripted provider and a synthetic authenticated human, with no real Run or authority.
The desired evidence-visibility assertion deliberately fails on the investigated source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.agents.llm import ScriptedProvider
from thymira.events import InMemoryEventLog, canonical_json
from thymira.mira.agents.verification import build_finding_verifier
from thymira.mira.checks import AuditReport
from thymira.mira.verification import FindingVerdict
from thymira.policies import (
    Gate,
    GateMode,
    PolicyEngine,
    RiskProfile,
    ToolCapability,
    load_policy_stack,
)
from thymira.schemas import (
    Actor,
    ActorKind,
    AuditFinding,
    Decision,
    EventType,
    Evidence,
    Framework,
    ModelRoutePolicy,
    Severity,
    new_id,
)

if TYPE_CHECKING:
    import pytest

    from thymira.policies import ApprovalRequest


def approve_synthetic_request(_request: ApprovalRequest) -> bool:
    """Return the synthetic human's answer within this in-memory diagnostic."""
    return True


def test_verifier_keeps_a_cited_gate_approval_without_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting verifier events must preserve an authoritative cited approval."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "independent-review-model")
    log = InMemoryEventLog(new_id("run"))
    gate = Gate(
        PolicyEngine(load_policy_stack()),
        log,
        approver=approve_synthetic_request,
        human=Actor(kind=ActorKind.HUMAN, id="synthetic-reviewer", authenticated=True),
        mode=GateMode.SYNCHRONOUS,
    )
    decision = gate.check_capability(
        subject_id="synthetic-call",
        capability=ToolCapability(id="read_file", external_effects=()),
        risk=RiskProfile(risk_level="limited", activity_category="analysis", confidence=0.1),
    )
    assert decision.decision is Decision.REQUIRE_HUMAN_REVIEW  # noqa: S101  # Offline diagnostic.
    approval = next(event for event in log.events() if event.type is EventType.HUMAN_APPROVAL)
    candidate = AuditFinding(
        id=new_id("finding"),
        run_id=log.run_id,
        agent_id=new_id("agent"),
        control_id="REVIEW-APPROVAL",
        framework=Framework.INTERNAL,
        title="Synthetic approval claim",
        finding="SYNTHETIC_APPROVAL_PROPOSITION",
        severity=Severity.CRITICAL,
        confidence=0.9,
        evidence=(Evidence(kind="event", ref=f"seq:{approval.seq}", sha256=approval.hash),),
    )
    provider = ScriptedProvider((FindingVerdict(supported=False),))
    verify = build_finding_verifier(
        provider,
        route_policy=ModelRoutePolicy.from_routes(
            ("independent-review-model",), authority="code-owned-review"
        ),
    )
    verify(
        candidate,
        tuple(log.events()),
        AuditReport(run_id=log.run_id, status="passed", controls=()),
    )
    prompt = provider.calls[0]["prompt"]
    assert log.verify().valid  # noqa: S101  # Offline diagnostic.
    assert "SYNTHETIC_APPROVAL_PROPOSITION" in prompt  # noqa: S101  # Offline diagnostic.
    assert "[truncated]" not in prompt  # noqa: S101  # Offline diagnostic.
    required_evidence = canonical_json(
        {"seq": approval.seq, "type": approval.type.value, "payload": approval.payload}
    )
    assert required_evidence in prompt  # noqa: S101  # Offline diagnostic.
