"""Compaction fidelity audit agent (CMP-02): does the summary faithfully represent what it hid?

ADR-0006 makes compaction an *appended* record. A ``context.compacted`` event shadows the
model-visible events it hid (``shadowed_seqs``), records the summarising model's envelope, and
persists only the **safe summary projection** -- never the raw provider output, which satisfies
ADR-0004's chain-of-thought rule by construction. CMP-01's deterministic control (A27) guarantees
the *structure* of that record; ADR-0006 decision 5 defers the remaining question to an audit
agent: *does the persisted summary faithfully represent what it replaced?*

This agent answers exactly that. For each ``context.compacted`` event it recovers, by seq, the
events the compaction shadowed -- which :func:`thymira.events.current_surface` deliberately hides
from the model, so a bespoke projection is required rather than the generic runner's surface
projection -- and shows the model the persisted summary beside those recovered events. The model
proposes a fidelity gap when the summary omits or misrepresents a material shadowed fact (a
shadowed tool failure the summary drops, say, or reports as a success). It never sees raw provider
output, because none is persisted.

"LLM proposes, code authorizes" applies to the evidence as much as to the decision: the model may
only point at a compaction that exists and at seqs that compaction genuinely shadowed. The runtime
recovers the compaction's actual shadowed seqs from the log and builds the finding's Evidence from
those, dropping any seq the model invented -- so a fidelity finding always references real shadowed
events, and a proposal about a non-existent compaction is dropped rather than minted into a finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from thymira.agents.llm.base import LLMStructuredOutputError
from thymira.agents.llm.routing import Role
from thymira.agents.model_binding import routed_model
from thymira.agents.prompt_framing import frame_overhead, frame_untrusted
from thymira.events import canonical_json, redact_value, scrub_credentials_value, shadowed_seqs
from thymira.mira.agents.runner import (
    build_audit_tools,
    runtime_skill_event_payload,
    runtime_skill_instructions,
    validate_audit_agent_context,
)
from thymira.mira.finding_safety import safe_finding
from thymira.schemas import (
    AuditFinding,
    Event,
    EventType,
    Evidence,
    Severity,
    TaskStatus,
    ThymiraModel,
    new_id,
    utc_now,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.mira.agents.runner import AuditAgentContext
    from thymira.mira.agents.spec import AuditAgentSpec
    from thymira.mira.audit_io import AuditInput

#: Payload key of a ``context.compacted`` event carrying the persisted safe-summary projection.
#: CMP-01 defined the ``envelope`` and ``shadowed_seqs`` keys; this is the summary those hid.
COMPACTION_SUMMARY_KEY = "summary"

#: Stable control id every compaction-fidelity finding carries (an audit agent, not a CONTROLS id).
COMPACTION_FIDELITY_CONTROL_ID = "COMPACTION-FIDELITY"

_MAX_PROJECTION_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class CompactionFidelityResult:
    """The candidate findings one compaction-fidelity pass produced over a Run's compactions."""

    findings: tuple[AuditFinding, ...]


class _CompactionFidelityGap(ThymiraModel):
    """One fidelity gap a model proposes about a single ``context.compacted`` event.

    The model names ``compaction_seq`` (which must exist) and, in ``omitted_seqs``, the shadowed
    events whose material facts the summary drops or misrepresents. It never supplies the finding's
    identity or its evidence digests: the runtime resolves both.
    """

    compaction_seq: int
    omitted_seqs: tuple[int, ...] = ()
    title: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    recommendation: str | None = None


class _CompactionFidelityResponse(ThymiraModel):
    """The structured response returned by the compaction-fidelity agent."""

    gaps: tuple[_CompactionFidelityGap, ...] = ()


@dataclass(frozen=True, slots=True)
class _CompactionProjection:
    """Complete comparison subjects actually available to the provider."""

    prompt: str | None
    visible_compaction_seqs: frozenset[int]
    visible_shadowed_seqs: tuple[tuple[int, frozenset[int]], ...]
    omitted_compaction_count: int


def audit_compaction_fidelity(
    spec: AuditAgentSpec,
    audit_input: AuditInput,
    context: AuditAgentContext,
) -> CompactionFidelityResult:
    """Judge whether each compaction's summary faithfully represents the events it shadowed.

    The agent is a no-op when the Run recorded no compaction: there is nothing whose fidelity to
    check, so no model is called. Otherwise the model reasons over a bespoke projection that pairs
    each compaction's persisted summary with the events it shadowed (recovered by seq), and every
    proposed gap is resolved against the log before it becomes a finding.

    Raises:
        ValueError: If the context does not belong to the audited Run.
        LLMStructuredOutputError: If structured output cannot be validated within ``max_turns``.
    """
    validate_audit_agent_context(spec, audit_input, context)

    compactions = tuple(
        event
        for event in audit_input.events
        if event.run_id == audit_input.run_id and event.type is EventType.CONTEXT_COMPACTED
    )
    if not compactions:
        return CompactionFidelityResult(findings=())

    by_seq = {
        event.seq: event for event in audit_input.events if event.run_id == audit_input.run_id
    }
    projection = _build_projection(audit_input, compactions, by_seq)
    if not projection.visible_compaction_seqs:
        _record_unavailable_projection(spec, context, projection.omitted_compaction_count)
        return CompactionFidelityResult(findings=())
    gaps = _propose_gaps(spec, projection, context)
    return _resolve_gaps(spec, gaps, projection, compactions, audit_input, context)


def _propose_gaps(
    spec: AuditAgentSpec,
    projection: _CompactionProjection,
    context: AuditAgentContext,
) -> tuple[_CompactionFidelityGap, ...]:
    """Run the agent once over the compaction projection and return its raw proposals."""
    prompt = projection.prompt
    if prompt is None:
        raise RuntimeError("MIRA compaction projection is unavailable")
    instructions = runtime_skill_instructions(spec, context)
    runtime_evidence = runtime_skill_event_payload(context)
    task_kind = spec.task_kinds[0]
    model = routed_model(
        Role.MIRA,
        task_kind,
        context.event_log,
        requested_tier=spec.tier,
        provider=context.provider,
        actor=context.actor,
        output_schema=_CompactionFidelityResponse,
        runtime_skill_evidence=runtime_evidence,
        before_model_selection=context.before_model_selection,
        before_model_call=context.before_model_call,
        record_model_usage=context.record_model_usage,
        route_policy=context.route_policy,
        request_ledger=context.request_ledger,
        request_owner_id=(
            context.runtime_skill_dispatch_id or f"{context.task_id}:{context.agent_id}"
        ),
    )
    agent: PydanticAgent[None, _CompactionFidelityResponse] = PydanticAgent(
        model=model,
        output_type=_CompactionFidelityResponse,
        instructions=instructions,
        retries=max(spec.max_turns - 1, 0),
        tools=build_audit_tools(spec, context),
    )
    context.event_log.append(
        EventType.AGENT_STARTED,
        context.actor,
        {
            "agent": spec.name,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "framework": spec.framework.value,
            "omitted_compaction_count": projection.omitted_compaction_count,
        },
        subject_id=context.agent_id,
        producer="thymira.mira",
    )
    try:
        result = agent.run_sync(prompt, usage_limits=UsageLimits(request_limit=spec.max_turns))
    except (UnexpectedModelBehavior, UsageLimitExceeded):
        context.event_log.append(
            EventType.AGENT_COMPLETED,
            context.actor,
            {
                "agent": spec.name,
                "agent_id": context.agent_id,
                "task_id": context.task_id,
                "status": TaskStatus.FAILED.value,
                "max_turns": spec.max_turns,
                "omitted_compaction_count": projection.omitted_compaction_count,
            },
            subject_id=context.agent_id,
            producer="thymira.mira",
        )
        message = f"MIRA compaction-fidelity agent {spec.name!r} exhausted structured-output"
        raise LLMStructuredOutputError(message) from None

    context.event_log.append(
        EventType.AGENT_COMPLETED,
        context.actor,
        {
            "agent": spec.name,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "status": TaskStatus.COMPLETED.value,
            "omitted_compaction_count": projection.omitted_compaction_count,
        },
        subject_id=context.agent_id,
        producer="thymira.mira",
    )
    return result.output.gaps


def _resolve_gaps(
    spec: AuditAgentSpec,
    gaps: tuple[_CompactionFidelityGap, ...],
    projection: _CompactionProjection,
    compactions: tuple[Event, ...],
    audit_input: AuditInput,
    context: AuditAgentContext,
) -> CompactionFidelityResult:
    """Resolve only compactions and shadowed records included in the provider projection."""
    visible_shadowed = dict(projection.visible_shadowed_seqs)
    compaction_by_seq = {
        event.seq: event for event in compactions if event.seq in projection.visible_compaction_seqs
    }
    findings: list[AuditFinding] = []
    for gap in gaps:
        compaction = compaction_by_seq.get(gap.compaction_seq)
        if compaction is None:
            # A proposal about a compaction that does not exist cannot be minted into a finding.
            continue
        actual = tuple(sorted(visible_shadowed.get(compaction.seq, frozenset())))
        omitted = tuple(seq for seq in gap.omitted_seqs if seq in actual)
        referenced = omitted or actual
        findings.append(_finding(spec, gap, compaction, referenced, audit_input, context))
    return CompactionFidelityResult(findings=tuple(findings))


def _recovered_shadowed_seqs(compaction: Event, by_seq: dict[int, Event]) -> tuple[int, ...]:
    """Return the seqs this compaction actually shadowed: named, recorded, and preceding it."""
    return tuple(seq for seq in shadowed_seqs(compaction) if seq < compaction.seq and seq in by_seq)


def _finding(
    spec: AuditAgentSpec,
    gap: _CompactionFidelityGap,
    compaction: Event,
    referenced_seqs: tuple[int, ...],
    audit_input: AuditInput,
    context: AuditAgentContext,
) -> AuditFinding:
    """Build one fidelity finding, stamping identity and evidence in code, not from the model."""
    evidence = _deduplicate(
        (
            Evidence(kind="event", ref=f"seq:{compaction.seq}"),
            *(Evidence(kind="event", ref=f"seq:{seq}") for seq in referenced_seqs),
        )
    )
    return safe_finding(
        AuditFinding(
            id=new_id("finding"),
            run_id=audit_input.run_id,
            agent_id=context.agent_id,
            control_id=COMPACTION_FIDELITY_CONTROL_ID,
            framework=spec.framework,
            title=gap.title,
            finding=gap.finding,
            severity=gap.severity,
            confidence=gap.confidence,
            evidence=evidence,
            recommendation=gap.recommendation,
            created_at=utc_now(),
        )
    )


def _deduplicate(evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Return distinct evidence pointers in first-seen order."""
    unique: dict[tuple[str, str], Evidence] = {}
    for reference in evidence:
        unique.setdefault((reference.kind, reference.ref), reference)
    return tuple(unique.values())


def _build_projection(
    audit_input: AuditInput,
    compactions: tuple[Event, ...],
    by_seq: dict[int, Event],
) -> _CompactionProjection:
    """Fit whole summary/shadowed-event comparisons under the exact framed-message budget."""
    projection: dict[str, object] = {
        "run_id": audit_input.run_id,
        "compactions": [],
        "omitted_records": {"compactions": 0},
    }
    if _render_projection(projection) is None:
        return _CompactionProjection(None, frozenset(), (), len(compactions))

    visible: dict[int, tuple[dict[str, object], frozenset[int]]] = {}
    omitted_count = 0
    for compaction in compactions:
        declared_seqs = shadowed_seqs(compaction)
        actual_seqs = _recovered_shadowed_seqs(compaction, by_seq)
        summary = compaction.payload.get(COMPACTION_SUMMARY_KEY)
        if (
            len(declared_seqs) != len(actual_seqs)
            or not actual_seqs
            or not isinstance(summary, str)
            or not summary.strip()
            or compaction.hash is None
            or any(by_seq[seq].hash is None for seq in actual_seqs)
        ):
            omitted_count += 1
            continue
        comparison = {
            "compaction": _event_projection(compaction),
            "summary": summary,
            "shadowed_events": [_event_projection(by_seq[seq]) for seq in actual_seqs],
        }
        trial = {**visible, compaction.seq: (comparison, frozenset(actual_seqs))}
        projection["compactions"] = [item[0] for item in trial.values()]
        if _render_projection(projection) is None:
            omitted_count += 1
            continue
        visible = trial

    projection["compactions"] = [item[0] for item in visible.values()]
    projection["omitted_records"] = {"compactions": omitted_count}
    prompt = _render_projection(projection)
    while prompt is None and visible:
        seq = next(reversed(visible))
        visible.pop(seq)
        omitted_count += 1
        projection["compactions"] = [item[0] for item in visible.values()]
        projection["omitted_records"] = {"compactions": omitted_count}
        prompt = _render_projection(projection)
    return _CompactionProjection(
        prompt=prompt,
        visible_compaction_seqs=frozenset(visible),
        visible_shadowed_seqs=tuple(
            (seq, shadowed) for seq, (_record, shadowed) in visible.items()
        ),
        omitted_compaction_count=omitted_count,
    )


def _render_projection(projection: dict[str, object]) -> str | None:
    """Redact and size serialized JSON exactly, including the no-cap nonce frame."""
    rendered = canonical_json(redact_value(scrub_credentials_value(projection)))
    escaped = rendered.replace("<", r"\u003c").replace(">", r"\u003e")
    if len(frame_overhead("mira-compaction-evidence")) + len(escaped) > _MAX_PROJECTION_CHARS:
        return None
    return frame_untrusted(rendered, label="mira-compaction-evidence")


def _event_projection(event: Event) -> dict[str, object]:
    """Project the complete hash-pinned event envelope and payload."""
    return event.model_dump(mode="json")


def _record_unavailable_projection(
    spec: AuditAgentSpec, context: AuditAgentContext, omitted_count: int
) -> None:
    """Record that all comparison subjects were omitted before any model call."""
    payload = {
        "agent": spec.name,
        "agent_id": context.agent_id,
        "task_id": context.task_id,
        "framework": spec.framework.value,
        "omitted_compaction_count": omitted_count,
        "model_call_skipped": True,
    }
    context.event_log.append(
        EventType.AGENT_STARTED,
        context.actor,
        payload,
        subject_id=context.agent_id,
        producer="thymira.mira",
    )
    context.event_log.append(
        EventType.AGENT_COMPLETED,
        context.actor,
        {**payload, "status": TaskStatus.COMPLETED.value},
        subject_id=context.agent_id,
        producer="thymira.mira",
    )


__all__ = [
    "COMPACTION_FIDELITY_CONTROL_ID",
    "COMPACTION_SUMMARY_KEY",
    "CompactionFidelityResult",
    "audit_compaction_fidelity",
]
