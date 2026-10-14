"""Results of deterministic audit controls and the report that bundles them."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer

from thymira.schemas import AuditFinding, Evidence, Severity, ThymiraModel


class ControlStatus(StrEnum):
    """Outcome of one control."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class AuditMode(StrEnum):
    """When the evidence snapshot is audited relative to Run closeout."""

    IN_FLIGHT = "IN_FLIGHT"
    FINAL = "FINAL"


class ControlResult(ThymiraModel):
    """One control's verdict with the evidence it looked at."""

    control_id: str
    title: str
    status: ControlStatus
    severity: Severity
    detail: str = ""
    evidence: tuple[Evidence, ...] = ()
    failure_code: str | None = None


AuditStatus = Literal["passed", "passed_with_warnings", "failed", "incomplete"]
ApplicabilityStatus = Literal["applicable", "not_applicable", "undetermined"]


class ApplicabilityJudgment(ThymiraModel):
    """A verifier's separate, evidence-grounded view of one candidate's scope."""

    status: ApplicabilityStatus = "undetermined"
    rationale: str = "No applicability judgment was supplied."
    concrete_action: str = ""
    evidence: tuple[Evidence, ...] = ()


class FindingApplicabilityAssessment(ThymiraModel):
    """Persisted scope judgment tied to one candidate and the exact audit snapshot."""

    finding_id: str
    control_id: str
    applicability: ApplicabilityJudgment = Field(default_factory=ApplicabilityJudgment)
    supported: bool | None = None
    verification_rationale: str = ""
    activity_profile_id: str | None = None
    activity_profile_version: int | None = Field(default=None, ge=1)
    audit_mode: AuditMode = AuditMode.FINAL
    snapshot_head_seq: int | None = Field(default=None, ge=0)
    snapshot_head_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AuditReport(ThymiraModel):
    """What MIRA's deterministic layer concludes about a run.

    The report never contains a decision: findings go to the Policy Engine, which decides.
    """

    run_id: str
    control_set: str = "thymira-mira-checks@0.1"
    status: AuditStatus
    controls: tuple[ControlResult, ...]
    findings: tuple[AuditFinding, ...] = ()
    terminal_hash: str | None = None
    audit_mode: AuditMode = AuditMode.FINAL
    manifest_sha256: str | None = None
    graph_definition_hash: str | None = None
    policy_sha256: str | None = None
    activity_profile_id: str | None = None
    activity_profile_version: int | None = None
    applicability_assessments: tuple[FindingApplicabilityAssessment, ...] = ()
    summary: dict[str, int] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _preserve_legacy_default(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """Keep legacy FINAL report JSON byte-compatible when the field was not supplied."""
        serialized = handler(self)
        if "audit_mode" not in self.model_fields_set:
            serialized.pop("audit_mode", None)
        if not self.applicability_assessments:
            serialized.pop("applicability_assessments", None)
        return serialized

    def to_markdown(self) -> str:
        """Human-readable assurance summary (English)."""
        lines = [
            f"# Audit report — run `{self.run_id}`",
            "",
            f"- Control set: `{self.control_set}`",
            f"- Status: **{self.status}**",
            f"- Terminal event hash: `{self.terminal_hash or 'n/a'}`",
            "",
            "| Control | Status | Severity | Detail |",
            "|---|---|---|---|",
        ]
        lines.extend(
            f"| {c.control_id} {c.title} | {c.status} | {c.severity} | {c.detail or '-'} |"
            for c in self.controls
        )
        lines += [
            "",
            (
                "*This report states what the deterministic controls verified. It does not "
                "assert that a human, legal or business review took place unless such evidence "
                "is recorded.*"
            ),
        ]
        return "\n".join(lines) + "\n"
