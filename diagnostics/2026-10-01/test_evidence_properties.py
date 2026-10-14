"""Diagnostic property probes, not production fixes.

Real: MIRA verifier projection, typed events and routing.
Fake: the model provider. No network, historical source or persisted Run is used.
The two long-input cases intentionally fail on the investigated source snapshot.
"""

from typing import Literal

import pytest

from thymira.agents.llm import ScriptedProvider
from thymira.events import InMemoryEventLog
from thymira.mira.agents.verification import build_finding_verifier
from thymira.mira.checks import AuditReport
from thymira.mira.verification import FindingVerdict
from thymira.schemas import (
    Actor,
    AuditFinding,
    EventType,
    Evidence,
    Framework,
    ModelRoutePolicy,
    Severity,
    new_id,
)

LARGE_SYNTHETIC_OUTPUT_CHARACTERS = 20_000
LARGE_EVIDENCE_REFERENCE_COUNT = 2_000


@pytest.mark.parametrize("shape", ["small", "large_event", "large_references"])
def test_finding_verifier_receives_the_complete_candidate(
    shape: Literal["small", "large_event", "large_references"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A size cap must preserve the proposition the judge is asked to assess."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "diagnostic-model")
    log = InMemoryEventLog(new_id("run"))
    payload = {"tool": "run_python", "status": "FAILED", "error": "synthetic failure"}
    if shape == "large_event":
        payload["synthetic_output"] = "x" * LARGE_SYNTHETIC_OUTPUT_CHARACTERS
    event = log.append(EventType.TOOL_COMPLETED, Actor.system(), payload)
    evidence = (
        tuple(
            Evidence(kind="event", ref=f"seq:{index}")
            for index in range(LARGE_EVIDENCE_REFERENCE_COUNT)
        )
        if shape == "large_references"
        else (Evidence(kind="event", ref=f"seq:{event.seq}", sha256=event.hash),)
    )
    finding = AuditFinding(
        id=new_id("finding"),
        run_id=log.run_id,
        agent_id=new_id("agent"),
        control_id="DIAGNOSTIC-CANDIDATE",
        framework=Framework.INTERNAL,
        title="Exact synthetic candidate to assess",
        finding="SYNTHETIC_PROPOSITION_MUST_REACH_THE_JUDGE",
        severity=Severity.CRITICAL,
        confidence=0.9,
        evidence=evidence,
    )
    provider = ScriptedProvider((FindingVerdict(supported=False),))
    verifier = build_finding_verifier(
        provider,
        route_policy=ModelRoutePolicy.from_routes(
            ("diagnostic-model",), authority="code-owned-diagnostic"
        ),
    )
    verifier(
        finding,
        tuple(log.events()),
        AuditReport(run_id=log.run_id, status="passed", controls=()),
    )
    visible_prompt = provider.calls[0]["prompt"]
    assert "SYNTHETIC_PROPOSITION_MUST_REACH_THE_JUDGE" in visible_prompt  # noqa: S101  # diagnostic property probe
    assert '"severity":"critical"' in visible_prompt.lower()  # noqa: S101  # diagnostic property probe
