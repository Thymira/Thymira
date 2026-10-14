"""Bounded, model-visible evidence projection for MIRA audit agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.agents.prompt_framing import frame_overhead, frame_untrusted
from thymira.events import canonical_json, redact_value
from thymira.mira.evidence import EvidenceContentError, EvidenceLimitError
from thymira.schemas import ActivityProfile, EventType, approval_decision_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.mira.audit_io import AuditInput
    from thymira.mira.checks import AuditReport
    from thymira.mira.evidence import EvidenceReader
    from thymira.schemas import AuditFinding, Event, Evidence


@dataclass(frozen=True, slots=True)
class EvidenceProjectionLimits:
    """Independent character caps for MIRA's model-visible evidence projection."""

    total_chars: int = 12_000
    event_chars: int = 2_000
    agent_message_chars: int = 2_000
    excerpt_chars: int = 2_000
    report_chars: int = 4_000


@dataclass(frozen=True, slots=True)
class ProjectedEventRecord:
    """The exact event identity and payload fields represented in the model-visible frame."""

    seq: int
    event_type: EventType
    event_hash: str | None
    payload: dict[str, object] | None
    rendered: str


@dataclass(frozen=True, slots=True)
class EvidenceProjection:
    """The exact bounded prompt and Run evidence identities it exposes."""

    prompt: str
    visible_event_seqs: frozenset[int]
    visible_artifact_evidence: tuple[Evidence, ...] = ()
    visible_event_records: tuple[ProjectedEventRecord, ...] = ()
    visible_context_event_hashes: tuple[tuple[int, str], ...] = ()


_AUDIT_EVENT_TYPES = frozenset(
    {
        EventType.RUN_STARTED,
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_TRANSITIONED,
        EventType.ACTIVITY_PROFILE_RECORDED,
        EventType.HUMAN_APPROVAL,
        EventType.MODEL_SELECTED,
        EventType.MODEL_TRAINED,
        EventType.EXPERIMENT_STARTED,
        EventType.EXPERIMENT_COMPLETED,
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.TOOL_DENIED,
        EventType.POLICY_DECISION,
        EventType.HUMAN_APPROVAL_REQUESTED,
        EventType.ARTIFACT_CREATED,
    }
)

_MAX_AGENT_MESSAGES = 8
_MAX_ARTIFACT_EXCERPTS = 4
_PRIOR_FINDINGS_CHARS = 2_000


def _frame_safe_json_fragment(value: str) -> str:
    """Mirror frame delimiter escaping when checking exact visible substrings."""
    return value.replace("<", r"\u003c").replace(">", r"\u003e")


def build_evidence_projection(
    audit_input: AuditInput,
    evidence_reader: EvidenceReader | None,
    limits: EvidenceProjectionLimits,
) -> EvidenceProjection:
    """Return a redacted, deterministic projection limited to authorised MIRA evidence."""
    excerpt_candidates = _authorised_excerpts(
        audit_input.report, evidence_reader, limits.excerpt_chars
    )
    selected_events, agent_messages = _select_evidence_events(audit_input.events)
    audit_context = _audit_context_projection(
        audit_input,
        prior_findings_chars=min(_PRIOR_FINDINGS_CHARS, limits.total_chars // 4),
    )
    projection = _base_projection(
        audit_input, audit_context, selected_events, excerpt_candidates, agent_messages, limits
    )
    rendered_events, included_excerpts = _fit_event_section(
        projection, selected_events, audit_input, excerpt_candidates, limits
    )
    included_messages = _fit_optional_messages(
        projection, rendered_events, included_excerpts, agent_messages, limits
    )
    projection["omitted_records"] = {
        "event_count": len(selected_events) - len(rendered_events),
        "agent_message_count": len(agent_messages) - len(included_messages),
        "artifact_excerpt_count": len(excerpt_candidates) - len(included_excerpts),
    }
    return _frame_projection(
        projection,
        audit_input,
        audit_context,
        rendered_events,
        included_excerpts,
        included_messages,
        limits,
    )


def _base_projection(
    audit_input: AuditInput,
    audit_context: dict[str, object],
    events: Sequence[Event],
    excerpts: Sequence[dict[str, object]],
    messages: Sequence[Event],
    limits: EvidenceProjectionLimits,
) -> dict[str, object]:
    """Build the stable projection shell before any frame-budget allocation."""
    return {
        "audit_context": audit_context,
        "artifact_excerpts": [],
        "audit_report": _report_projection(audit_input.report, limits.report_chars),
        "events": [],
        "experiment_ids": (),
        "frameworks": tuple(framework.value for framework in audit_input.frameworks),
        "run_id": audit_input.run_id,
        "artifact_names": (),
        "agent_messages": [],
        "omitted_records": {
            "event_count": len(events),
            "agent_message_count": len(messages),
            "artifact_excerpt_count": len(excerpts),
        },
    }


def _fit_event_section(
    projection: dict[str, object],
    events: Sequence[Event],
    audit_input: AuditInput,
    excerpts: Sequence[dict[str, object]],
    limits: EvidenceProjectionLimits,
) -> tuple[dict[int, ProjectedEventRecord], list[dict[str, object]]]:
    """Fit current scope, latest action chain and cited excerpts before older typed records."""
    included: dict[int, ProjectedEventRecord] = {}
    priority = _required_evidence_events(events, audit_input)
    priority_seqs = {event.seq for event in priority}
    for event in priority:
        _fit_priority_event(projection, included, event, limits)
    included_excerpts: list[dict[str, object]] = []
    for excerpt in excerpts:
        included_excerpts.append(excerpt)
        _refresh_projection_sections(projection, included, included_excerpts, ())
        if not _projection_fits(projection, limits.total_chars):
            included_excerpts.pop()
    for event in reversed(events):
        if event.seq in priority_seqs or event.seq in included:
            continue
        rendered = _event_projection(event, limits.event_chars)
        if rendered is not None:
            _try_add_event(
                projection, included, event, rendered, limits.total_chars, included_excerpts
            )
    return included, included_excerpts


def _fit_priority_event(
    projection: dict[str, object],
    included: dict[int, ProjectedEventRecord],
    event: Event,
    limits: EvidenceProjectionLimits,
) -> None:
    """Retain a priority record, reducing only the report if its independent cap crowds it out."""
    rendered = _event_projection(event, limits.event_chars)
    if rendered is None or _try_add_event(
        projection, included, event, rendered, limits.total_chars
    ):
        return
    report = projection["audit_report"]
    if not isinstance(report, str):
        return
    cap = len(report)
    while cap > 0:
        cap = max(0, cap - max(100, cap // 4))
        projection["audit_report"] = _truncate(report, cap) if cap > 0 else None
        if _try_add_event(projection, included, event, rendered, limits.total_chars):
            return


def _try_add_event(
    projection: dict[str, object],
    included: dict[int, ProjectedEventRecord],
    event: Event,
    rendered: ProjectedEventRecord,
    max_chars: int,
    excerpts: Sequence[dict[str, object]] = (),
) -> bool:
    included[event.seq] = rendered
    _refresh_projection_sections(projection, included, excerpts, ())
    if _projection_fits(projection, max_chars):
        return True
    included.pop(event.seq, None)
    _refresh_projection_sections(projection, included, excerpts, ())
    return False


def _fit_optional_messages(
    projection: dict[str, object],
    events: dict[int, ProjectedEventRecord],
    excerpts: Sequence[dict[str, object]],
    messages: Sequence[Event],
    limits: EvidenceProjectionLimits,
) -> list[tuple[Event, ProjectedEventRecord]]:
    """Fit recent narrative messages into the bounded space left by stronger evidence."""
    included_messages: list[tuple[Event, ProjectedEventRecord]] = []
    for event in reversed(messages):
        rendered = _event_projection(event, limits.agent_message_chars)
        if rendered is None:
            continue
        included_messages.append((event, rendered))
        _refresh_projection_sections(projection, events, excerpts, included_messages)
        if not _projection_fits(projection, limits.total_chars):
            included_messages.pop()
    included_messages.reverse()
    _refresh_projection_sections(projection, events, excerpts, included_messages)
    return included_messages


def _frame_projection(
    projection: dict[str, object],
    audit_input: AuditInput,
    audit_context: dict[str, object],
    events: dict[int, ProjectedEventRecord],
    excerpts: Sequence[dict[str, object]],
    messages: Sequence[tuple[Event, ProjectedEventRecord]],
    limits: EvidenceProjectionLimits,
) -> EvidenceProjection:
    """Frame only a complete fitted JSON projection and index only records actually delivered."""
    if not _projection_fits(projection, limits.total_chars):
        return _omitted_projection(limits.total_chars)
    try:
        prompt = frame_untrusted(
            _serialize_projection(projection), label="mira-evidence", max_chars=limits.total_chars
        )
    except ValueError:
        return _omitted_projection(limits.total_chars)
    visible_seqs = set(events)
    visible_seqs.update(event.seq for event, _ in messages)
    visible_context_hashes: dict[int, str] = {}
    audit_context_text = _frame_safe_json_fragment(
        json.dumps(
            redact_value(audit_context),
            sort_keys=False,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    if audit_context_text in prompt:
        visible_audit_context = redact_value(audit_context)
        profile_context = (
            visible_audit_context.get("activity_profile_is_declared_context")
            if isinstance(visible_audit_context, dict)
            else None
        )
        explicit_seq_values: list[object] = []
        for key in ("recorded_human_approval_event_seqs", "terminal_event_seqs"):
            values = audit_context[key]
            if isinstance(values, tuple):
                explicit_seq_values.extend(values)
        if isinstance(profile_context, dict) and profile_context.get("event_seq") is not None:
            profile_seq = profile_context["event_seq"]
            explicit_seq_values.append(profile_seq)
            profile_hash = profile_context.get("event_hash")
            if isinstance(profile_seq, int) and isinstance(profile_hash, str):
                visible_context_hashes[profile_seq] = profile_hash
        visible_seqs.update(seq for seq in explicit_seq_values if isinstance(seq, int))
    visible_records = {
        record.seq: record for record in (*events.values(), *(record for _, record in messages))
    }
    visible_artifacts: tuple[Evidence, ...] = ()
    if audit_input.report is not None:
        visible_artifacts = tuple(
            evidence
            for evidence in _artifact_evidence(audit_input.report)[:_MAX_ARTIFACT_EXCERPTS]
            if any(
                excerpt.get("ref") == evidence.ref
                and excerpt.get("sha256") == evidence.sha256
                and isinstance(excerpt.get("text"), str)
                and _frame_safe_json_fragment(
                    json.dumps(
                        redact_value(excerpt),
                        sort_keys=False,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                )
                in prompt
                for excerpt in excerpts
            )
        )
    return EvidenceProjection(
        prompt=prompt,
        visible_event_seqs=frozenset(visible_seqs),
        visible_artifact_evidence=visible_artifacts,
        visible_event_records=tuple(visible_records[seq] for seq in sorted(visible_records)),
        visible_context_event_hashes=tuple(sorted(visible_context_hashes.items())),
    )


def _omitted_projection(max_chars: int) -> EvidenceProjection:
    """Return a bounded no-evidence response when fixed projection data cannot fit safely."""
    prompt = _truncate(
        "MIRA evidence omitted: the configured budget cannot fit a complete safety frame.",
        max_chars,
    )
    return EvidenceProjection(prompt=prompt, visible_event_seqs=frozenset())


def _audit_context_projection(
    audit_input: AuditInput, *, prior_findings_chars: int
) -> dict[str, object]:
    """Summarise declared scope and recorded approvals without asserting their legal effect."""
    profile = audit_input.activity_profile
    if profile is None:
        profile_event = next(
            (
                event
                for event in reversed(audit_input.events)
                if event.type is EventType.ACTIVITY_PROFILE_RECORDED
            ),
            None,
        )
        if profile_event is not None:
            try:
                profile = ActivityProfile.model_validate_json(
                    canonical_json(profile_event.payload["activity_profile"])
                )
            except (KeyError, TypeError, ValueError):
                profile = None
    if profile is not None and profile.run_id != audit_input.run_id:
        profile = None
    profile_event_seq = None
    profile_event_hash = None
    if profile is not None:
        for event in reversed(audit_input.events):
            value = event.payload.get("activity_profile")
            if (
                event.type is EventType.ACTIVITY_PROFILE_RECORDED
                and isinstance(value, dict)
                and value.get("id") == profile.id
            ):
                profile_event_seq = event.seq
                profile_event_hash = event.hash
                break
    profile_context: dict[str, object] | None = None
    if profile is not None:
        profile_context = {
            "event_seq": profile_event_seq,
            "event_hash": profile_event_hash,
            "id": profile.id,
            "version": profile.version,
            "purpose": _truncate(profile.purpose, 500),
            "affected_population": _truncate(profile.affected_population, 500),
            "decision_effect": _truncate(profile.decision_effect, 500),
            "autonomy": _truncate(profile.autonomy, 500),
            "human_oversight": _truncate(profile.human_oversight, 500),
            "jurisdiction": _truncate(profile.jurisdiction, 500),
            "data_categories": tuple(_truncate(value, 200) for value in profile.data_categories),
            "sensitive_attributes": tuple(
                _truncate(value, 200) for value in profile.sensitive_attributes
            ),
        }
    approval_events = tuple(
        event for event in audit_input.events if event.type is EventType.HUMAN_APPROVAL
    )
    terminal_events = tuple(
        event
        for event in audit_input.events
        if event.type in {EventType.RUN_COMPLETED, EventType.RUN_FAILED}
    )
    report_findings = audit_input.report.findings if audit_input.report is not None else ()
    prior_findings, omitted_prior_findings = _prior_candidate_findings(
        report_findings, max_chars=prior_findings_chars
    )
    return {
        "audit_mode": audit_input.audit_mode.value,
        "completion_or_failure_event_recorded": any(
            event.type in {EventType.RUN_COMPLETED, EventType.RUN_FAILED}
            for event in audit_input.events
        ),
        "terminal_event_seqs": tuple(event.seq for event in terminal_events[-32:]),
        "older_terminal_event_count_omitted": max(0, len(terminal_events) - 32),
        "activity_profile_is_declared_context": profile_context,
        "recorded_human_approval_event_seqs": tuple(event.seq for event in approval_events[-32:]),
        "older_approval_count_omitted": max(0, len(approval_events) - 32),
        "prior_candidate_findings_omitted": omitted_prior_findings,
        "prior_candidate_findings": prior_findings,
    }


def _prior_candidate_findings(
    findings: Sequence[AuditFinding],
    *,
    max_chars: int,
) -> tuple[tuple[dict[str, str], ...], int]:
    """Project recent prior finding summaries under a bounded character budget."""
    selected: list[dict[str, str]] = []
    used_chars = 2
    for finding in reversed(findings):
        summary = {
            "control_id": _truncate(finding.control_id, 60),
            "severity": finding.severity.value,
            "finding": _truncate(finding.finding, 120),
        }
        candidate = canonical_json(summary)
        additional = len(candidate) + (1 if selected else 0)
        if used_chars + additional > max_chars:
            break
        selected.append(summary)
        used_chars += additional
    selected.reverse()
    return tuple(selected), max(0, len(findings) - len(selected))


def _select_evidence_events(events: Sequence[Event]) -> tuple[tuple[Event, ...], tuple[Event, ...]]:
    """Select typed Run evidence and the latest bounded narrative messages."""
    messages = tuple(event for event in events if event.type is EventType.AGENT_MESSAGE)
    latest_messages = messages[-_MAX_AGENT_MESSAGES:]
    selected = tuple(event for event in events if event.type in _AUDIT_EVENT_TYPES)
    return selected, latest_messages


def _required_evidence_events(
    events: Sequence[Event], audit_input: AuditInput
) -> tuple[Event, ...]:
    """Return the current profile and recent completed actions with their available chain."""
    profile_id = _current_profile_id(audit_input)
    profile_event = next(
        (
            event
            for event in events
            if event.type is EventType.ACTIVITY_PROFILE_RECORDED
            and isinstance(event.payload.get("activity_profile"), dict)
            and event.payload["activity_profile"].get("id") == profile_id
        ),
        None,
    )
    required: list[Event] = [profile_event] if profile_event is not None else []
    latest_completed = tuple(
        sorted(
            (event for event in events if event.type is EventType.TOOL_COMPLETED),
            key=lambda event: event.seq,
            reverse=True,
        )[:4]
    )
    for completed in latest_completed:
        call_id = completed.payload.get("tool_call_id")
        start = next(
            (
                event
                for event in events
                if event.type is EventType.TOOL_STARTED
                and event.payload.get("tool_call_id") == call_id
            ),
            None,
        )
        decision_id = approval_decision_id(start.payload) if start is not None else None
        chain = [completed]
        if start is not None:
            chain.append(start)
        if isinstance(decision_id, str):
            chain.extend(
                event
                for event in events
                if (
                    event.type is EventType.POLICY_DECISION
                    and event.payload.get("id") == decision_id
                )
                or (
                    event.type is EventType.HUMAN_APPROVAL_REQUESTED
                    and approval_decision_id(event.payload) == decision_id
                )
                or (
                    event.type is EventType.HUMAN_APPROVAL
                    and approval_decision_id(event.payload) == decision_id
                )
            )
        required.extend(event for event in chain if event not in required)
    return tuple(required)


def _current_profile_id(audit_input: AuditInput) -> str | None:
    """Return the run-bound profile identity from the typed input or its recorded event."""
    if audit_input.activity_profile is not None:
        return audit_input.activity_profile.id
    for event in reversed(audit_input.events):
        value = event.payload.get("activity_profile")
        if event.type is EventType.ACTIVITY_PROFILE_RECORDED and isinstance(value, dict):
            profile_id = value.get("id")
            if isinstance(profile_id, str):
                return profile_id
    return None


def _event_projection(event: Event, max_chars: int) -> ProjectedEventRecord | None:
    """Render valid bounded JSON while retaining an event's identity when payload is large."""
    payload = redact_value(event.payload)
    complete = _projected_event_record(event, payload, False, max_chars)
    if complete is not None:
        return complete
    compact_payload = _compact_event_payload(event, payload)
    compact = _projected_event_record(event, compact_payload, True, max_chars)
    if compact is not None:
        return compact
    return _projected_event_record(event, None, True, max_chars)


def _projected_event_record(
    event: Event, payload: object, payload_truncated: bool, max_chars: int
) -> ProjectedEventRecord | None:
    """Build typed projection metadata from the same JSON object shown to the model."""
    rendered = canonical_json(
        {
            "seq": event.seq,
            "type": event.type.value,
            "hash": event.hash,
            "payload": payload,
            "payload_truncated": payload_truncated,
        }
    )
    if len(rendered) > max_chars:
        return None
    visible_payload = payload if isinstance(payload, dict) else None
    return ProjectedEventRecord(event.seq, event.type, event.hash, visible_payload, rendered)


def _compact_event_payload(event: Event, payload: object) -> object:
    """Keep action identity and decision linkage when an event body exceeds its local cap."""
    if not isinstance(payload, dict):
        return None
    if event.type is EventType.TOOL_COMPLETED:
        keys = (
            "tool_call_id",
            "task_id",
            "tool",
            "status",
            "exit_code",
            "result_code",
            "result_sha256",
            "artifact_ids",
            "decision_id",
        )
    elif event.type is EventType.TOOL_STARTED:
        keys = (
            "tool_call_id",
            "task_id",
            "tool",
            "decision_id",
            "approval_scope_digest",
            "delegation_depth",
        )
    elif event.type is EventType.HUMAN_APPROVAL:
        keys = (
            "decision_id",
            "policy_decision_id",
            "approved",
            "automatic",
            "authorization_context_sha256",
            "note",
        )
    elif event.type is EventType.HUMAN_APPROVAL_REQUESTED:
        keys = (
            "decision_id",
            "policy_decision_id",
            "summary",
            "question",
            "reason",
        )
    elif event.type is EventType.POLICY_DECISION:
        keys = ("id", "decision", "rule_id", "reason", "scope_sha256", "request_sha256")
    elif event.type is EventType.ACTIVITY_PROFILE_RECORDED:
        profile = payload.get("activity_profile")
        profile_id = profile.get("id") if isinstance(profile, dict) else None
        return {"activity_profile": {"id": profile_id}}
    elif event.type is EventType.ARTIFACT_CREATED:
        keys = ("artifact_id", "id", "name", "sha256")
    elif event.type in {EventType.EXPERIMENT_STARTED, EventType.EXPERIMENT_COMPLETED}:
        experiment_id = payload.get("experiment_id")
        if not isinstance(experiment_id, str):
            experiment = payload.get("experiment")
            experiment_id = experiment.get("id") if isinstance(experiment, dict) else None
        return {"experiment_id": experiment_id} if isinstance(experiment_id, str) else {}
    else:
        keys = (
            "id",
            "decision_id",
            "policy_decision_id",
            "tool_call_id",
            "tool",
            "decision",
            "rule_id",
            "reason",
            "summary",
        )
    return {key: payload[key] for key in keys if key in payload}


def _refresh_projection_sections(
    projection: dict[str, object],
    rendered_events: dict[int, ProjectedEventRecord],
    excerpts: Sequence[dict[str, object]],
    messages: Sequence[tuple[Event, ProjectedEventRecord]],
) -> None:
    records = tuple(rendered_events[seq] for seq in sorted(rendered_events))
    projection["events"] = [record.rendered for record in records]
    projection["artifact_names"] = _projected_artifact_names(records)
    projection["experiment_ids"] = _projected_experiment_ids(records)
    projection["artifact_excerpts"] = tuple(excerpts)
    projection["agent_messages"] = [record.rendered for _, record in messages]


def _projected_artifact_names(records: Sequence[ProjectedEventRecord]) -> tuple[str, ...]:
    """List artifact names only when their event payload delivered the exact name field."""
    return tuple(
        dict.fromkeys(
            name
            for record in records
            if record.event_type is EventType.ARTIFACT_CREATED
            and isinstance(record.payload, dict)
            and isinstance(name := record.payload.get("name"), str)
            and name
        )
    )


def _projected_experiment_ids(records: Sequence[ProjectedEventRecord]) -> tuple[str, ...]:
    """List experiment ids only when their projected lifecycle payload delivered them."""
    identifiers: list[str] = []
    for record in records:
        if record.event_type not in {
            EventType.EXPERIMENT_STARTED,
            EventType.EXPERIMENT_COMPLETED,
        } or not isinstance(record.payload, dict):
            continue
        identifier = record.payload.get("experiment_id")
        if not isinstance(identifier, str):
            experiment = record.payload.get("experiment")
            identifier = experiment.get("id") if isinstance(experiment, dict) else None
        if isinstance(identifier, str) and identifier:
            identifiers.append(identifier)
    return tuple(dict.fromkeys(identifiers))


def _serialize_projection(projection: dict[str, object]) -> str:
    return json.dumps(
        redact_value(projection),
        sort_keys=False,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _projection_fits(projection: dict[str, object], max_chars: int) -> bool:
    rendered = _serialize_projection(projection)
    escaped_size = len(_frame_safe_json_fragment(rendered))
    return len(frame_overhead("mira-evidence")) + escaped_size <= max_chars


def _report_projection(report: AuditReport | None, max_chars: int) -> str | None:
    """Render the deterministic report under its own cap, like every other bounded section.

    ``canonical_json`` sorts keys, so ``audit_report`` is serialized before ``events``. Left
    uncapped, one oversized report made the single tail truncation in
    :func:`_build_evidence_projection` delete the whole events section — the primary evidence the
    agent reasons over — while ``event_chars`` suggested each event was individually bounded.
    Capping the report here keeps every section of the projection genuinely independent.
    """
    if report is None:
        return None
    return _truncate(canonical_json(redact_value(report.model_dump(mode="json"))), max_chars)


def _authorised_excerpts(
    report: AuditReport | None,
    reader: EvidenceReader | None,
    max_chars: int,
) -> tuple[dict[str, object], ...]:
    """Read only report-cited artifacts whose expected and actual digests agree."""
    if report is None or reader is None:
        return ()
    excerpts: list[dict[str, object]] = []
    for evidence in _artifact_evidence(report)[:_MAX_ARTIFACT_EXCERPTS]:
        try:
            excerpt = reader.read_excerpt(evidence, max_chars=max_chars)
        except (EvidenceLimitError, EvidenceContentError) as exc:
            # A binary or non-UTF-8 cited artifact (a PDF, a joblib model) is bounded
            # unavailable evidence, exactly like an oversized one; reference and integrity
            # errors still fail closed. Before this, one cited PDF ended the whole Run as
            # FAILED instead of an audit finding.
            excerpts.append(
                {
                    "ref": evidence.ref,
                    "sha256": evidence.sha256,
                    "text": None,
                    "truncated": True,
                    "unavailable": str(exc),
                }
            )
            continue
        if excerpt.evidence != evidence:
            raise ValueError("evidence reader returned an excerpt for another evidence reference")
        if excerpt.expected_sha256 != evidence.sha256 or excerpt.actual_sha256 != evidence.sha256:
            raise ValueError("evidence excerpt sha256 does not match the report-pinned digest")
        text = _truncate(redact_value(excerpt.text), max_chars)
        excerpts.append(
            {
                "ref": evidence.ref,
                "sha256": evidence.sha256,
                "text": text,
                "truncated": excerpt.truncated or text != excerpt.text,
            }
        )
    return tuple(excerpts)


def _artifact_evidence(report: AuditReport) -> tuple[Evidence, ...]:
    """Return unique, hash-pinned artifact evidence cited by the deterministic report."""
    evidence = (
        *(reference for control in report.controls for reference in control.evidence),
        *(reference for finding in report.findings for reference in finding.evidence),
    )
    unique: dict[tuple[str, str, str], Evidence] = {}
    for reference in evidence:
        if reference.kind == "artifact" and reference.sha256 is not None:
            unique.setdefault((reference.kind, reference.ref, reference.sha256), reference)
    return tuple(unique.values())


def _truncate(text: str, max_chars: int) -> str:
    """Return a deterministic bounded string, marking every shortened value with an ellipsis."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return f"{text[: max_chars - 1]}…"


__all__ = ["EvidenceProjection", "EvidenceProjectionLimits", "build_evidence_projection"]
