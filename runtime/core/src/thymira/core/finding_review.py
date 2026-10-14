"""Validate the exact MIRA evidence scope for a critical indeterminate finding review."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple, cast

from thymira.events import canonical_json, sha256_text, verify_events
from thymira.mira.checks import (
    AuditMode,
    AuditReport,
    ControlStatus,
    assess_audit_freshness,
)
from thymira.mira.checks.controls import CONTROLS
from thymira.mira.evidence import artifact_manifest_sha256
from thymira.policies import FindingReviewCandidate, FindingReviewContext
from thymira.schemas import ActivityProfile, ActorKind, EventType, Evidence, Run

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.core.graph.protocol import SubgraphDeps
    from thymira.schemas import AuditFinding, Event

_IN_FLIGHT_EXCLUDED_CONTROLS = frozenset({"A2", "A7", "A8"})
_SAFE_PROFILE_CAPABILITY = "profile_dataset"
_SAFE_AUDIT_CAPABILITY = "search_regulation"
_SAFE_SIDE_EFFECTS = frozenset({"read", "artifact_write"})
_SAFE_DATA_ACCESS = frozenset({"dataset"})
_SAFE_AUDIT_DATA_ACCESS = frozenset({"regulation_store"})
_PROHIBITED_RISK_TAGS = frozenset(
    {
        "model_training",
        "model_comparison",
        "hyperparameter_search",
        "prediction",
        "decision",
        "deploy",
    }
)
_MODEL_WORK_EVENTS = frozenset(
    {
        EventType.MODEL_TRAINED,
        EventType.EXPERIMENT_STARTED,
        EventType.EXPERIMENT_COMPLETED,
    }
)


class _AuditSnapshot(NamedTuple):
    events: tuple[Event, ...]
    raw_report: dict[str, Any]
    report: AuditReport
    completion: Event
    terminal: Event
    snapshot_events: tuple[Event, ...]
    revision: int
    run: Run
    assessments_well_typed: bool


class _ScopeEvidence(NamedTuple):
    profile_event: Event | None
    artifact_rows: list[dict[str, str]]
    artifact_digest: str | None
    manifest_ok: bool
    freshness_ok: bool
    freshness_detail: str


class _ActionEvidence(NamedTuple):
    events: tuple[Event, ...]
    capabilities: tuple[Any, ...]
    all_safe: bool
    model_work_present: bool


def finding_review_context(
    deps: SubgraphDeps, findings: Sequence[AuditFinding]
) -> FindingReviewContext | None:
    """Return a report-bound context, or ``None`` when the persisted audit cannot be trusted."""
    snapshot = _validated_audit_snapshot(deps, findings)
    if snapshot is None:
        return None
    raw_assessments = snapshot.raw_report.get("applicability_assessments", [])
    audit_complete = _audit_complete(snapshot.report)
    scope_evidence = _scope_evidence(deps, snapshot)
    action_evidence = _action_evidence(deps, snapshot.snapshot_events)
    run_work_kind = (
        snapshot.run.conversation.work_kind if snapshot.run.conversation is not None else "general"
    )
    scope_reasons = _scope_reasons(audit_complete, run_work_kind, scope_evidence, action_evidence)
    bounded_scope = (
        audit_complete
        and run_work_kind == "general"
        and scope_evidence.profile_event is not None
        and bool(action_evidence.events)
        and action_evidence.all_safe
        and scope_evidence.freshness_ok
        and not action_evidence.model_work_present
        and scope_evidence.manifest_ok
        and snapshot.assessments_well_typed
        and snapshot.report.policy_sha256 == deps.gate.engine.policy_sha256
    )
    candidates = _validated_candidates(
        raw_assessments,
        snapshot.report,
        findings,
        snapshot.snapshot_events,
        scope_evidence.profile_event,
        snapshot.terminal.seq,
        allow_supported=snapshot.assessments_well_typed,
    )
    eligible_ids = tuple(
        finding.id
        for finding in findings
        if bounded_scope
        and finding.severity.value == "CRITICAL"
        and any(
            candidate.finding_id == finding.id
            and candidate.control_id == finding.control_id
            and candidate.supported is None
            for candidate in candidates
        )
    )
    reason = (
        "registered profile_dataset executed locally with artifact-only effects"
        if bounded_scope
        else "; ".join(scope_reasons) or "audit metadata is incompatible with descriptive review"
    )
    return _build_review_context(
        snapshot,
        findings=findings,
        candidates=candidates,
        eligible_ids=eligible_ids,
        audit_complete=audit_complete,
        bounded_scope=bounded_scope,
        reason=reason,
        actions=action_evidence,
        scope=scope_evidence,
    )


def _validated_audit_snapshot(
    deps: SubgraphDeps, findings: Sequence[AuditFinding]
) -> _AuditSnapshot | None:
    """Validate the persisted report, its terminal snapshot and the Run identity."""
    events = tuple(deps.event_log.events())
    if not events or verify_events(events).valid is not True:
        return None
    parts = _validated_report_parts(deps, findings, events)
    if parts is None:
        return None
    raw_report, report, completion, revision, assessments_well_typed = parts
    terminal = next((event for event in events if event.hash == report.terminal_hash), None)
    if terminal is None or terminal.seq >= completion.seq:
        return None
    if any(
        event.type is EventType.AUDIT_STARTED and event.seq > completion.seq for event in events
    ):
        return None
    snapshot_events = tuple(event for event in events if event.seq <= terminal.seq)
    run = _run_from_snapshot(events, report)
    if run is None:
        return None
    return _AuditSnapshot(
        events,
        raw_report,
        report,
        completion,
        terminal,
        snapshot_events,
        revision,
        run,
        assessments_well_typed,
    )


def _validated_report_parts(
    deps: SubgraphDeps, findings: Sequence[AuditFinding], events: tuple[Event, ...]
) -> tuple[dict[str, Any], AuditReport, Event, int, bool] | None:
    """Load the latest well-formed completion and confirm its exact finding payload."""
    completion = next(
        (event for event in reversed(events) if event.type is EventType.AUDIT_COMPLETED), None
    )
    if completion is None or completion.run_id != deps.event_log.run_id:
        return None
    raw_report = completion.payload.get("audit_report")
    if not isinstance(raw_report, dict) or not isinstance(
        raw_report.get("applicability_assessments", []), list
    ):
        return None
    raw_assessments = raw_report.get("applicability_assessments", [])
    assessments_well_typed = all(
        isinstance(item, dict)
        and "supported" in item
        and (item.get("supported") is None or type(item.get("supported")) is bool)
        for item in raw_assessments
    )
    try:
        report = AuditReport.model_validate(raw_report)
    except (TypeError, ValueError):
        return None
    if not _report_findings_match(report, deps.event_log.run_id, findings):
        return None
    revision = _audit_revision(events, completion)
    if (
        revision is None
        or report.policy_sha256 != deps.gate.engine.policy_sha256
        or report.audit_mode is not AuditMode.IN_FLIGHT
    ):
        return None
    return raw_report, report, completion, revision, assessments_well_typed


def _report_findings_match(
    report: AuditReport, run_id: str, findings: Sequence[AuditFinding]
) -> bool:
    return (
        report.run_id == run_id
        and tuple(item.id for item in report.findings) == tuple(item.id for item in findings)
        and canonical_json([item.model_dump(mode="json") for item in report.findings])
        == canonical_json([item.model_dump(mode="json") for item in findings])
    )


def _audit_revision(events: tuple[Event, ...], completion: Event) -> int | None:
    starts = [event for event in events if event.type is EventType.AUDIT_STARTED]
    if not starts or starts[-1].seq >= completion.seq:
        return None
    position = events.index(completion)
    expected = sum(event.type is EventType.AUDIT_COMPLETED for event in events[: position + 1])
    revision = completion.payload.get("audit_revision")
    return revision if type(revision) is int and revision == expected else None


def _run_from_snapshot(events: tuple[Event, ...], report: AuditReport) -> Run | None:
    raw_run = next(
        (
            event.payload.get("run")
            for event in events
            if event.type is EventType.RUN_STARTED and event.run_id == report.run_id
        ),
        None,
    )
    if not isinstance(raw_run, dict):
        return None
    try:
        run = Run.model_validate_json(canonical_json(raw_run))
    except (TypeError, ValueError):
        return None
    return run if run.id == report.run_id else None


def _audit_complete(report: AuditReport) -> bool:
    control_ids = tuple(control.control_id for control in report.controls)
    required = {
        control.control_id
        for control in CONTROLS
        if control.control_id not in _IN_FLIGHT_EXCLUDED_CONTROLS
    }
    return (
        report.status != "incomplete"
        and len(control_ids) == len(set(control_ids))
        and required <= set(control_ids)
        and all(control.status is not ControlStatus.NOT_EVALUATED for control in report.controls)
    )


def _scope_evidence(deps: SubgraphDeps, snapshot: _AuditSnapshot) -> _ScopeEvidence:
    profile_event = _matching_profile_event(snapshot.snapshot_events, snapshot.report)
    artifacts, manifest_ok = _artifact_manifest(deps)
    artifact_digest = artifact_manifest_sha256(deps.artifact_store)
    manifest_ok = (
        manifest_ok
        and snapshot.report.manifest_sha256 is not None
        and artifact_digest == snapshot.report.manifest_sha256
    )
    freshness = assess_audit_freshness(
        snapshot.report,
        snapshot.events,
        store=deps.artifact_store,
        current_policy_sha256=deps.gate.engine.policy_sha256,
        current_activity_profile_id=(
            profile_event.payload.get("activity_profile", {}).get("id")
            if profile_event is not None
            else None
        ),
        current_activity_profile_version=(
            profile_event.payload.get("activity_profile", {}).get("version")
            if profile_event is not None
            else None
        ),
    )
    return _ScopeEvidence(
        profile_event,
        artifacts,
        artifact_digest,
        manifest_ok,
        freshness.status is ControlStatus.PASSED,
        freshness.detail,
    )


def _action_evidence(deps: SubgraphDeps, events: tuple[Event, ...]) -> _ActionEvidence:
    action_events = tuple(event for event in events if event.type is EventType.TOOL_COMPLETED)
    capabilities = {item.id: item for item in deps.tool_manager.capabilities()}
    audit_capabilities = {item.id: item for item in deps.audit_tool_capabilities}
    used: list[Any] = []
    all_safe = bool(action_events)
    has_profile = False
    for event in action_events:
        name = event.payload.get("tool")
        if not isinstance(name, str):
            all_safe = False
            continue
        if name == _SAFE_PROFILE_CAPABILITY:
            capability = capabilities.get(name)
            safe = _completed_capability_is_safe(event, name, capability)
            has_profile = has_profile or safe
        elif name == _SAFE_AUDIT_CAPABILITY:
            capability = audit_capabilities.get(name)
            safe = _completed_audit_capability_is_safe(event, capability, events)
        else:
            capability = None
            safe = False
        if not safe:
            all_safe = False
            continue
        used.append(capability)
    all_safe = all_safe and has_profile
    model_work = any(event.type in _MODEL_WORK_EVENTS for event in events)
    return _ActionEvidence(action_events, tuple(used), all_safe, model_work)


def _completed_capability_is_safe(event: Event, name: Any, capability: Any) -> bool:
    return (
        capability is not None
        and event.actor.id == name
        and event.actor.kind is ActorKind.TOOL
        and event.actor.authenticated is True
        and event.payload.get("status") == "COMPLETED"
        and _safe_profile_capability(capability)
    )


def _completed_audit_capability_is_safe(
    completed: Event, capability: Any, events: tuple[Event, ...]
) -> bool:
    """Accept only a paired, authenticated MIRA-owned read from its isolated registry."""
    name = completed.payload.get("tool")
    call_id = completed.payload.get("tool_call_id")
    task_id = completed.payload.get("task_id")
    if (
        name != _SAFE_AUDIT_CAPABILITY
        or not isinstance(call_id, str)
        or not call_id
        or not isinstance(task_id, str)
        or not _safe_audit_capability(capability)
        or not _authenticated_tool_actor(completed, name)
        or completed.payload.get("status") != "COMPLETED"
        or completed.subject_id != call_id
    ):
        return False

    starts = tuple(
        event
        for event in events
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool_call_id") == call_id
    )
    completions = tuple(
        event
        for event in events
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool_call_id") == call_id
    )
    if len(starts) != 1 or len(completions) != 1 or completions[0] != completed:
        return False
    started = starts[0]
    if (
        started.run_id != completed.run_id
        or started.subject_id != call_id
        or started.payload.get("tool") != name
        or started.payload.get("task_id") != task_id
        or not _authenticated_tool_actor(started, name)
        or not _same_authenticated_actor(started.actor, completed.actor)
        or started.seq >= completed.seq
    ):
        return False
    return _owned_by_mira_agent(started, completed, events, task_id)


def _owned_by_mira_agent(
    started_tool: Event,
    completed_tool: Event,
    events: tuple[Event, ...],
    task_id: str,
) -> bool:
    starts = tuple(
        event
        for event in events
        if event.type is EventType.AGENT_STARTED
        and event.producer == "thymira.mira"
        and event.payload.get("task_id") == task_id
        and event.payload.get("agent_id") == event.subject_id
        and event.run_id == started_tool.run_id
        and event.actor.authenticated is True
        and event.seq < started_tool.seq
    )
    owners = []
    for agent_started in starts:
        agent_id = agent_started.payload.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        completions = tuple(
            event
            for event in events
            if event.type is EventType.AGENT_COMPLETED
            and event.producer == "thymira.mira"
            and event.payload.get("agent_id") == agent_id
            and event.payload.get("task_id") == task_id
            and event.subject_id == agent_id
            and event.run_id == agent_started.run_id
            and event.payload.get("status") == "COMPLETED"
            and event.actor.authenticated is True
            and _same_authenticated_actor(agent_started.actor, event.actor)
            and completed_tool.seq < event.seq
        )
        if len(completions) == 1 and agent_started.seq < started_tool.seq:
            owners.append(agent_started)
    return len(owners) == 1


def _authenticated_tool_actor(event: Event, tool_name: str) -> bool:
    return (
        event.actor.kind is ActorKind.TOOL
        and event.actor.id == tool_name
        and event.actor.authenticated is True
    )


def _same_authenticated_actor(first: Any, second: Any) -> bool:
    return (
        first.authenticated is True
        and second.authenticated is True
        and first.id == second.id
        and first.kind is second.kind
    )


def _safe_audit_capability(capability: Any) -> bool:
    return (
        capability is not None
        and capability.id == _SAFE_AUDIT_CAPABILITY
        and set(capability.data_access) == _SAFE_AUDIT_DATA_ACCESS
        and not capability.side_effects
        and not capability.external_effects
        and not set(capability.risk_tags) & _PROHIBITED_RISK_TAGS
    )


def _scope_reasons(
    audit_complete: bool,
    run_work_kind: str,
    scope: _ScopeEvidence,
    actions: _ActionEvidence,
) -> list[str]:
    reasons = []
    if not audit_complete:
        reasons.append("audit report is incomplete or has unevaluated controls")
    if run_work_kind != "general":
        reasons.append(f"run work kind {run_work_kind!r} is outside descriptive scope")
    if scope.profile_event is None:
        reasons.append("the exact activity profile is not recorded in the audit snapshot")
    if not actions.events or not actions.all_safe:
        reasons.append("executed capabilities do not establish bounded local profiling")
    if not scope.freshness_ok:
        reasons.append(f"MIRA freshness control failed: {scope.freshness_detail}")
    if actions.model_work_present:
        reasons.append("training or experiment events exist in the audited Run snapshot")
    if not scope.manifest_ok:
        reasons.append("the active artifact manifest is incomplete or invalid")
    return reasons


def _build_review_context(
    snapshot: _AuditSnapshot,
    *,
    findings: Sequence[AuditFinding],
    candidates: tuple[FindingReviewCandidate, ...],
    eligible_ids: tuple[str, ...],
    audit_complete: bool,
    bounded_scope: bool,
    reason: str,
    actions: _ActionEvidence,
    scope: _ScopeEvidence,
) -> FindingReviewContext:
    report_sha = sha256_text(canonical_json(snapshot.raw_report))
    findings_sha = sha256_text(
        canonical_json([finding.model_dump(mode="json") for finding in findings])
    )
    run_sha = sha256_text(canonical_json(snapshot.run.model_dump(mode="json")))
    artifacts_sha = scope.artifact_digest or sha256_text(canonical_json(scope.artifact_rows))
    action_hashes = tuple(event.hash for event in actions.events)
    capability_json = [capability.model_dump(mode="json") for capability in actions.capabilities]
    scope_payload = {
        "run_sha256": run_sha,
        "work_kind": (
            snapshot.run.conversation.work_kind
            if snapshot.run.conversation is not None
            else "general"
        ),
        "profile_id": snapshot.report.activity_profile_id,
        "profile_version": snapshot.report.activity_profile_version,
        "report_sha256": report_sha,
        "audit_terminal_hash": snapshot.report.terminal_hash,
        "action_event_hashes": list(action_hashes),
        "capabilities": capability_json,
        "artifact_manifest_sha256": artifacts_sha,
        "audit_complete": audit_complete,
    }
    scope_sha = sha256_text(canonical_json(scope_payload))
    context_data: dict[str, Any] = {
        "audit_completed_seq": snapshot.completion.seq,
        "audit_completed_hash": snapshot.completion.hash,
        "audit_revision": snapshot.revision,
        "audit_terminal_hash": snapshot.report.terminal_hash,
        "audit_terminal_seq": snapshot.terminal.seq,
        "report_sha256": report_sha,
        "findings_sha256": findings_sha,
        "policy_sha256": snapshot.report.policy_sha256,
        "profile_id": snapshot.report.activity_profile_id,
        "profile_version": snapshot.report.activity_profile_version,
        "run_snapshot_sha256": run_sha,
        "artifact_manifest_sha256": artifacts_sha,
        "action_event_hashes": action_hashes,
        "capabilities": actions.capabilities,
        "finding_ids": tuple(finding.id for finding in findings),
        "candidates": candidates,
        "eligible_finding_ids": eligible_ids,
        "audit_complete": audit_complete,
        "bounded_local_descriptive_scope": bounded_scope,
        "scope_reason": reason,
        "scope_sha256": scope_sha,
        "context_sha256": "0" * 64,
    }
    context = FindingReviewContext.model_validate(context_data)
    unsigned = context.model_dump(mode="json")
    unsigned.pop("context_sha256")
    digest = sha256_text(canonical_json(unsigned))
    return context.model_copy(update={"context_sha256": digest})


def _matching_profile_event(events: Sequence[Event], report: AuditReport) -> Event | None:
    """Find the exact activity profile snapshot MIRA assessed."""
    if report.activity_profile_id is None or report.activity_profile_version is None:
        return None
    recorded = [event for event in events if event.type is EventType.ACTIVITY_PROFILE_RECORDED]
    if not recorded:
        return None
    current = recorded[-1]
    raw = current.payload.get("activity_profile")
    if not isinstance(raw, dict):
        return None
    try:
        profile = ActivityProfile.model_validate_json(canonical_json(raw))
    except (TypeError, ValueError):
        return None
    if (
        profile.id != report.activity_profile_id
        or profile.version != report.activity_profile_version
        or profile.run_id != report.run_id
        or type(raw.get("version")) is not int
    ):
        return None
    return current


def _artifact_manifest(deps: SubgraphDeps) -> tuple[list[dict[str, str]], bool]:
    """Bind current immutable artifact identities and reject invalid active revisions."""
    manifest = deps.artifact_store.manifest()
    rows = []
    valid = True
    for name, artifact in sorted(manifest.items()):
        if name.startswith(".history/") and name != artifact.name:
            continue
        if artifact.valid is not True:
            valid = False
            continue
        rows.append({"name": name, "artifact_id": artifact.id, "sha256": artifact.sha256})
    try:
        valid = valid and not deps.artifact_store.verify()
    except (OSError, TypeError, ValueError):
        valid = False
    return rows, valid


def _safe_profile_capability(capability: Any) -> bool:
    """Accept only the registered deterministic local profiler's declared effects."""
    return (
        capability.id == _SAFE_PROFILE_CAPABILITY
        and capability.side_effects
        and set(capability.side_effects) <= _SAFE_SIDE_EFFECTS
        and set(capability.data_access) == _SAFE_DATA_ACCESS
        and not capability.external_effects
        and not set(capability.risk_tags) & _PROHIBITED_RISK_TAGS
    )


def _validated_candidates(
    raw_assessments: list[object],
    report: AuditReport,
    findings: Sequence[AuditFinding],
    events: Sequence[Event],
    profile_event: Event | None,
    terminal_seq: int,
    *,
    allow_supported: bool,
) -> tuple[FindingReviewCandidate, ...]:
    """Keep only unique, strict, fully hash-grounded candidate assessments."""
    by_finding = {finding.id: finding for finding in findings}
    pair_counts, finding_counts = _assessment_counts(raw_assessments)
    events_by_seq = {event.seq: event for event in events}
    candidates = (
        _candidate_from_assessment(
            raw,
            findings=by_finding,
            pair_counts=pair_counts,
            finding_counts=finding_counts,
            events_by_seq=events_by_seq,
            events=events,
            report=report,
            profile_event=profile_event,
            terminal_seq=terminal_seq,
            allow_supported=allow_supported,
        )
        for raw in raw_assessments
    )
    return tuple(candidate for candidate in candidates if candidate is not None)


def _assessment_counts(
    assessments: list[object],
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    pairs: dict[tuple[str, str], int] = {}
    findings: dict[str, int] = {}
    for raw in assessments:
        if not isinstance(raw, dict):
            continue
        finding_id, control_id = raw.get("finding_id"), raw.get("control_id")
        if not isinstance(finding_id, str) or not isinstance(control_id, str):
            continue
        pair = (finding_id, control_id)
        pairs[pair] = pairs.get(pair, 0) + 1
        findings[finding_id] = findings.get(finding_id, 0) + 1
    return pairs, findings


def _candidate_from_assessment(
    raw: object,
    *,
    findings: dict[str, AuditFinding],
    pair_counts: dict[tuple[str, str], int],
    finding_counts: dict[str, int],
    events_by_seq: dict[int, Event],
    events: Sequence[Event],
    report: AuditReport,
    profile_event: Event | None,
    terminal_seq: int,
    allow_supported: bool,
) -> FindingReviewCandidate | None:
    if not isinstance(raw, dict):
        return None
    finding_id, control_id = raw.get("finding_id"), raw.get("control_id")
    finding = findings.get(finding_id) if isinstance(finding_id, str) else None
    if (
        not _candidate_identity_is_valid(
            raw, finding, control_id, pair_counts, finding_counts, report, allow_supported
        )
        or finding is None
        or profile_event is None
    ):
        return None
    applicability = raw.get("applicability")
    snapshot_seq = raw["snapshot_head_seq"]
    if not isinstance(applicability, dict) or not _candidate_evidence_is_grounded(
        raw,
        applicability,
        finding,
        events_by_seq,
        events,
        profile_event,
        terminal_seq,
    ):
        return None
    text_values = _candidate_text_values(raw, applicability)
    if text_values is None:
        return None
    try:
        return FindingReviewCandidate(
            finding_id=finding.id,
            control_id=finding.control_id,
            title=finding.title,
            finding=finding.finding,
            severity=finding.severity,
            evidence=finding.evidence,
            supported=raw["supported"],
            applicability_status=text_values[0],
            verification_rationale=text_values[1],
            applicability_rationale=text_values[2],
            concrete_action=text_values[3],
            activity_profile_id=report.activity_profile_id,
            activity_profile_version=report.activity_profile_version,
            snapshot_head_seq=snapshot_seq,
            snapshot_head_hash=raw["snapshot_head_hash"],
        )
    except (TypeError, ValueError):
        return None


def _candidate_identity_is_valid(
    raw: dict[str, Any],
    finding: AuditFinding | None,
    control_id: Any,
    pair_counts: dict[tuple[str, str], int],
    finding_counts: dict[str, int],
    report: AuditReport,
    allow_supported: bool,
) -> bool:
    finding_id, supported = raw.get("finding_id"), raw.get("supported")
    return (
        allow_supported
        and finding is not None
        and finding.agent_id is not None
        and supported is not False
        and type(supported) in {bool, type(None)}
        and control_id == finding.control_id
        and pair_counts.get((finding_id, control_id), 0) == 1
        and finding_counts.get(finding_id) == 1
        and raw.get("activity_profile_id") == report.activity_profile_id
        and type(raw.get("activity_profile_version")) is int
        and raw.get("activity_profile_version") == report.activity_profile_version
        and raw.get("audit_mode") == report.audit_mode.value
        and type(raw.get("snapshot_head_seq")) is int
        and isinstance(raw.get("snapshot_head_hash"), str)
    )


def _candidate_evidence_is_grounded(
    raw: dict[str, Any],
    applicability: dict[str, Any],
    finding: AuditFinding,
    events_by_seq: dict[int, Event],
    events: Sequence[Event],
    profile_event: Event,
    terminal_seq: int,
) -> bool:
    snapshot_seq = raw["snapshot_head_seq"]
    evidence_values = applicability.get("evidence")
    if not isinstance(evidence_values, list) or not evidence_values:
        return False
    snapshot_event = events_by_seq.get(snapshot_seq)
    if (
        snapshot_event is None
        or snapshot_event.hash != raw.get("snapshot_head_hash")
        or snapshot_event.seq > terminal_seq
    ):
        return False
    cited_hashes = _cited_hashes((*evidence_values, *finding.evidence), events_by_seq, snapshot_seq)
    return (
        cited_hashes is not None
        and profile_event.hash in cited_hashes
        and any(
            event.type is EventType.TOOL_COMPLETED and event.hash in cited_hashes
            for event in events
        )
    )


def _cited_hashes(
    evidence_values: Sequence[object], events_by_seq: dict[int, Event], snapshot_seq: int
) -> set[str] | None:
    referenced: set[str] = set()
    for item in evidence_values:
        try:
            evidence = Evidence.model_validate(item)
        except (TypeError, ValueError):
            return None
        if evidence.kind != "event" or not evidence.ref.startswith("seq:"):
            return None
        try:
            seq = int(evidence.ref.removeprefix("seq:"))
        except ValueError:
            return None
        cited = events_by_seq.get(seq)
        if (
            seq > snapshot_seq
            or cited is None
            or cited.hash is None
            or cited.hash != evidence.sha256
        ):
            return None
        referenced.add(cited.hash)
    return referenced


def _candidate_text_values(
    raw: dict[str, Any], applicability: dict[str, Any]
) -> tuple[Literal["applicable", "not_applicable", "undetermined"], str, str, str] | None:
    status = applicability.get("status")
    rationale = applicability.get("rationale")
    concrete_action = applicability.get("concrete_action")
    verification_rationale = raw.get("verification_rationale")
    if (
        not isinstance(status, str)
        or status not in {"applicable", "not_applicable", "undetermined"}
        or not isinstance(rationale, str)
        or not rationale.strip()
        or not isinstance(verification_rationale, str)
        or not verification_rationale.strip()
        or not isinstance(concrete_action, str)
        or not concrete_action.strip()
    ):
        return None
    applicability_status = cast("Literal['applicable', 'not_applicable', 'undetermined']", status)
    return applicability_status, verification_rationale, rationale, concrete_action


__all__ = ["finding_review_context"]
