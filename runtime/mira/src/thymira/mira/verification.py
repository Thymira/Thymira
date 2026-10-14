"""Adversarial finding verification and the discovery-loop limit (MIRA-03).

MIRA-01 established a fixed verification/aggregation boundary: every candidate finding flows into
one ``_deduplicate_findings`` call before Core's ``Gate.review_findings`` boundary.
MIRA-03 turns that boundary from a pass-through into an adversarial one without moving it, so no
upstream node and no Gate contract changes.

Two MIRA-owned values live here, deliberately free of any ``thymira.agents`` / ``thymira.tools``
import so ``thymira.mira.flow`` keeps its injection boundary:

- :class:`FindingVerdict` and the :data:`FindingVerifier` callable type -- the second-pass judge
  the flow receives by injection (its concrete, provider-backed implementation is
  ``thymira.mira.agents.verification.build_finding_verifier``, which may import P2); and
- :class:`DiscoveryLimits` plus :func:`finding_dedup_key`, which bound and drive the discovery
  loop that re-runs audit agents until a pass yields no new finding.

**Only agent-authored candidates are subject to verification.** A code-authored deterministic or
preflight finding (``agent_id is None``) is never dropped by an LLM judge -- "LLM proposes, code
authorizes" applies to suppression exactly as it applies to authorization. The judge may only drop
findings a *first* LLM proposed; the Policy Engine still decides over whatever survives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field

from thymira.mira.checks.models import ApplicabilityJudgment
from thymira.mira.finding_safety import safe_finding
from thymira.schemas import AuditFinding, Severity, ThymiraModel

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.mira.checks import AuditReport
    from thymira.schemas import Event


class FindingVerdict(ThymiraModel):
    """A second-pass judge's ruling on one candidate finding."""

    supported: bool | None
    rationale: str = Field(default="")
    applicability: ApplicabilityJudgment = Field(default_factory=ApplicabilityJudgment)


#: A second-pass judge: given a candidate finding and the immutable audit evidence, decide whether
#: the recorded evidence supports it. Injected into ``thymira.mira.flow`` so the flow never
#: imports the provider-backed implementation.
type FindingVerifier = Callable[[AuditFinding, "tuple[Event, ...]", "AuditReport"], FindingVerdict]


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    """Bound the discovery loop: how many times audit agents may re-run seeking new findings."""

    max_rounds: int = 4

    def __post_init__(self) -> None:
        """Reject a non-positive round budget."""
        if self.max_rounds < 1:
            raise ValueError("DiscoveryLimits.max_rounds must be at least 1")


@dataclass(frozen=True, slots=True)
class IndependentAuditEvidence:
    """The deep-copied Run evidence shared by MIRA producers and its critic.

    The flow captures this value before invoking any audit agent. Producers may append their own
    lifecycle or tool events to the live log for grounding, but neither those events nor another
    producer's finding can enter this input. Keeping a copied tuple here also prevents a mutable
    event payload supplied by a caller from changing after the critic boundary was established.
    """

    events: tuple[Event, ...]

    @classmethod
    def capture(cls, events: Sequence[Event]) -> IndependentAuditEvidence:
        """Capture an immutable evidence snapshot before MIRA model work begins."""
        return cls(events=tuple(event.model_copy(deep=True) for event in events))


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


@dataclass(frozen=True, slots=True)
class VerificationDrop:
    """One agent finding rejected by the second-pass judge and the judge's rationale."""

    finding: AuditFinding
    rationale: str


@dataclass(frozen=True, slots=True)
class VerificationUnavailable:
    """One candidate kept because the verifier could not make a grounded ruling."""

    finding: AuditFinding
    rationale: str


@dataclass(frozen=True, slots=True)
class VerificationSupported:
    """One retained candidate with an explicit positive verifier ruling."""

    finding: AuditFinding
    rationale: str


@dataclass(frozen=True, slots=True)
class FindingVerificationRecord:
    """The full separate verdicts for one agent candidate, even when it is dropped."""

    finding: AuditFinding
    verdict: FindingVerdict


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """The candidate findings a verification pass kept and the agent findings it dropped."""

    kept: tuple[AuditFinding, ...]
    dropped: tuple[AuditFinding, ...]
    drop_records: tuple[VerificationDrop, ...] = ()
    unavailable_records: tuple[VerificationUnavailable, ...] = ()
    supported_records: tuple[VerificationSupported, ...] = ()
    judgments: tuple[FindingVerificationRecord, ...] = ()


def finding_dedup_key(finding: AuditFinding) -> tuple[str, str, tuple[str, ...]]:
    """Return the identity a finding deduplicates on -- identical to the orchestrator's key.

    The discovery loop calls a finding "new" when this key has not been seen before, and the Gate
    node's ``_deduplicate_findings`` collapses on the same key, so the two never disagree on what
    counts as the same underlying observation.
    """
    return (
        finding.control_id,
        finding.finding,
        tuple(f"{evidence.kind}:{evidence.ref}" for evidence in finding.evidence),
    )


def _prefer_finding(existing: AuditFinding, candidate: AuditFinding) -> AuditFinding:
    """Return the more severe finding, preserving the first record on a severity tie."""
    if _SEVERITY_RANK[candidate.severity] > _SEVERITY_RANK[existing.severity]:
        return candidate
    return existing


def verify_candidate_findings(
    candidates: Sequence[AuditFinding],
    verify: FindingVerifier | None,
    events: tuple[Event, ...],
    report: AuditReport,
) -> VerificationOutcome:
    """Keep agent-authored candidates according to the verifier's recorded tri-state result.

    With no verifier the outcome is a pass-through (the MVP identity), so no positive verdict is
    recorded. Otherwise each candidate whose ``agent_id`` is set is judged: ``False`` drops the
    finding, ``None`` retains it with verification unavailable, and ``True`` retains it as
    supported. Findings with no ``agent_id`` -- deterministic and preflight controls -- are always
    kept without an LLM verdict, because a judge may not suppress or validate a code-authored
    finding.
    """
    if verify is None:
        return VerificationOutcome(
            kept=tuple(candidates),
            dropped=(),
            judgments=tuple(
                FindingVerificationRecord(
                    finding=candidate,
                    verdict=FindingVerdict(
                        supported=None,
                        rationale="No finding verifier is configured for this candidate.",
                    ),
                )
                for candidate in candidates
                if candidate.agent_id is not None
            ),
        )
    kept: list[AuditFinding] = []
    dropped: list[AuditFinding] = []
    drop_records: list[VerificationDrop] = []
    unavailable_records: list[VerificationUnavailable] = []
    supported_records: list[VerificationSupported] = []
    judgments: list[FindingVerificationRecord] = []
    for candidate in candidates:
        finding = safe_finding(candidate)
        if finding.agent_id is None:
            kept.append(finding)
            continue
        verdict = verify(finding, events, report)
        judgments.append(FindingVerificationRecord(finding=finding, verdict=verdict))
        if verdict.supported is None:
            kept.append(finding)
            unavailable_records.append(
                VerificationUnavailable(finding=finding, rationale=verdict.rationale)
            )
        elif verdict.supported:
            kept.append(finding)
            supported_records.append(
                VerificationSupported(finding=finding, rationale=verdict.rationale)
            )
        else:
            dropped.append(finding)
            drop_records.append(VerificationDrop(finding=finding, rationale=verdict.rationale))
    return VerificationOutcome(
        kept=tuple(kept),
        dropped=tuple(dropped),
        drop_records=tuple(drop_records),
        unavailable_records=tuple(unavailable_records),
        supported_records=tuple(supported_records),
        judgments=tuple(judgments),
    )


__all__ = [
    "DiscoveryLimits",
    "FindingVerdict",
    "FindingVerificationRecord",
    "FindingVerifier",
    "IndependentAuditEvidence",
    "VerificationDrop",
    "VerificationOutcome",
    "VerificationSupported",
    "VerificationUnavailable",
    "finding_dedup_key",
    "verify_candidate_findings",
]
