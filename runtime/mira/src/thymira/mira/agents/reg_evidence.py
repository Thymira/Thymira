"""Regulatory-evidence enrichment: resolvable citations, never fabricated ones.

The regulatory-evidence agent strengthens the assurance bundle's ``evidence_index`` by attaching,
to each finding another audit agent emitted, the knowledge-base source that grounds it. The model
proposes only a ``source_id`` -- :class:`_ProposedCitation` deliberately carries no digest, so a
model has no channel to assert a hash. The runtime resolves every proposed ``source_id`` against
the knowledge base itself through :class:`CitationIndex`, and stamps the authoritative location and
``sha256`` from the resolved, hash-verified chunk. A ``source_id`` the knowledge base does not
contain resolves to nothing and is dropped, not fabricated: a citation cannot enter the record by
being asserted, only by resolving. The optional run event or artifact a finding concerns is
likewise verified against the supplied :class:`~thymira.mira.audit_io.AuditInput` before it is
attached, so no reference the enricher adds is unverifiable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import Field
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from thymira.agents.llm.base import LLMStructuredOutputError
from thymira.agents.llm.routing import Role
from thymira.agents.model_binding import routed_model
from thymira.agents.prompt_framing import frame_overhead, frame_untrusted
from thymira.events import canonical_json, current_surface, redact_value, scrub_credentials_value
from thymira.mira.agents.runner import (
    build_audit_tools,
    runtime_skill_event_payload,
    runtime_skill_instructions,
    validate_audit_agent_context,
)
from thymira.mira.kb import verifies_sha256
from thymira.schemas import (
    AuditFinding,
    EventType,
    Evidence,
    TaskStatus,
    ThymiraModel,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from thymira.mira.agents.runner import AuditAgentContext
    from thymira.mira.agents.spec import AuditAgentSpec
    from thymira.mira.audit_io import AuditInput
    from thymira.mira.kb import RegulationChunk
    from thymira.schemas import Event

_MAX_PROJECTION_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class CitationIndex:
    """A ``source_id`` -> hash-verified :class:`RegulationChunk` index: the sole citation authority.

    A citation is authoritative only when it resolves here. :meth:`resolve` builds the external
    :class:`~thymira.schemas.Evidence` from the resolved chunk's own ``source_id``, ``location``
    and ``sha256`` -- never from anything a model supplied -- so the enricher cannot mint a digest,
    and an unknown ``source_id`` yields ``None``.
    """

    _by_source_id: dict[str, RegulationChunk]

    @classmethod
    def from_chunks(cls, chunks: Iterable[RegulationChunk]) -> CitationIndex:
        """Index chunks by ``source_id``, recomputing each digest before it is trusted.

        Raises:
            ValueError: If a chunk's ``sha256`` does not verify, or two chunks share a source_id.
        """
        index: dict[str, RegulationChunk] = {}
        for chunk in chunks:
            if not verifies_sha256(chunk):
                raise ValueError(
                    f"cannot index a citation whose sha256 does not verify: {chunk.source_id!r}"
                )
            if chunk.source_id in index:
                raise ValueError(f"duplicate citation source_id: {chunk.source_id!r}")
            index[chunk.source_id] = chunk
        return cls(index)

    def sources(self) -> tuple[RegulationChunk, ...]:
        """Return the indexed chunks in ``source_id`` order for a deterministic catalog."""
        return tuple(self._by_source_id[source_id] for source_id in sorted(self._by_source_id))

    def resolve(self, source_id: str) -> Evidence | None:
        """Return authoritative external evidence for ``source_id``, or ``None`` when unknown."""
        chunk = self._by_source_id.get(source_id)
        if chunk is None:
            return None
        return Evidence(
            kind="external",
            ref=f"{chunk.source_id}/{chunk.location}",
            sha256=chunk.sha256,
        )


@dataclass(frozen=True, slots=True)
class RegulatoryEvidenceResult:
    """The outcome of one enrichment pass over another agent's findings.

    ``findings`` is every input finding, each carrying its resolved external citations where a
    proposal resolved; ``enriched`` is the subset that gained at least one external citation; and
    ``dropped_citations`` is every proposed ``source_id`` the knowledge base could not resolve.
    """

    findings: tuple[AuditFinding, ...]
    enriched: tuple[AuditFinding, ...]
    dropped_citations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _RegulatoryProjection:
    """Complete candidates, sources and Run records actually visible to the enricher."""

    prompt: str | None
    visible_finding_ids: frozenset[str]
    visible_source_ids: frozenset[str]
    visible_event_seqs: frozenset[int]
    visible_artifact_refs: frozenset[str]
    visible_experiment_ids: frozenset[str]
    omitted_finding_count: int
    omitted_source_count: int
    omitted_event_count: int


class _ProposedCitation(ThymiraModel):
    """One citation a model proposes: a ``source_id`` and, at most, the run ref it concerns.

    It carries no ``sha256`` and no source text: a model can name a source to resolve, never mint
    one. The runtime supplies the authoritative digest from the resolved chunk. ``note`` is
    accepted only as an ignored compatibility field; model prose never enters evidence.
    """

    finding_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    concerns_kind: Literal["event", "artifact", "experiment"] | None = None
    concerns_ref: str | None = None
    note: str | None = None


class _CitationProposals(ThymiraModel):
    """The structured proposals returned by the regulatory-evidence agent."""

    citations: tuple[_ProposedCitation, ...] = ()


def enrich_findings(
    spec: AuditAgentSpec,
    audit_input: AuditInput,
    findings: Sequence[AuditFinding],
    index: CitationIndex,
    context: AuditAgentContext,
) -> RegulatoryEvidenceResult:
    """Enrich other agents' findings with resolvable citations, dropping unresolvable ones.

    The agent proposes citations by ``source_id`` only; every proposal is resolved against
    ``index`` and dropped when the knowledge base does not contain it. The external evidence
    attached to a finding always carries the resolved chunk's own location and ``sha256``.

    Raises:
        ValueError: If the context or the findings do not belong to the audited Run.
        LLMStructuredOutputError: If structured output cannot be validated within ``max_turns``.
    """
    validate_audit_agent_context(spec, audit_input, context)
    if any(finding.run_id != audit_input.run_id for finding in findings):
        raise ValueError("every finding to enrich must belong to the audited run")

    candidates = tuple(findings)
    projection = _build_proposal_projection(audit_input, candidates, index)
    if (
        projection.prompt is None
        or not projection.visible_finding_ids
        or not projection.visible_source_ids
    ):
        _record_skipped_projection(spec, context, projection)
        proposals: tuple[_ProposedCitation, ...] = ()
    else:
        proposals = _propose_citations(spec, projection, context)
    return _resolve_proposals(candidates, proposals, index, audit_input, projection)


def _propose_citations(
    spec: AuditAgentSpec,
    projection: _RegulatoryProjection,
    context: AuditAgentContext,
) -> tuple[_ProposedCitation, ...]:
    """Run the agent once and return its raw, still-unresolved citation proposals."""
    prompt = projection.prompt
    if prompt is None:
        raise RuntimeError("MIRA regulatory evidence projection is unavailable")
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
        output_schema=_CitationProposals,
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
    agent: PydanticAgent[None, _CitationProposals] = PydanticAgent(
        model=model,
        output_type=_CitationProposals,
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
            **_omission_payload(projection),
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
                **_omission_payload(projection),
            },
            subject_id=context.agent_id,
            producer="thymira.mira",
        )
        message = f"MIRA regulatory-evidence agent {spec.name!r} exhausted structured-output"
        raise LLMStructuredOutputError(message) from None

    context.event_log.append(
        EventType.AGENT_COMPLETED,
        context.actor,
        {
            "agent": spec.name,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "status": TaskStatus.COMPLETED.value,
            **_omission_payload(projection),
        },
        subject_id=context.agent_id,
        producer="thymira.mira",
    )
    return result.output.citations


def _resolve_proposals(
    findings: tuple[AuditFinding, ...],
    proposals: tuple[_ProposedCitation, ...],
    index: CitationIndex,
    audit_input: AuditInput,
    projection: _RegulatoryProjection,
) -> RegulatoryEvidenceResult:
    """Resolve every proposal against the knowledge base, dropping the citations it cannot back."""
    valid_ids = {finding.id for finding in findings}
    by_finding: defaultdict[str, list[_ProposedCitation]] = defaultdict(list)
    for citation in proposals:
        if citation.finding_id in valid_ids:
            by_finding[citation.finding_id].append(citation)

    all_findings: list[AuditFinding] = []
    enriched: list[AuditFinding] = []
    dropped: list[str] = []
    for finding in findings:
        added: list[Evidence] = []
        for citation in by_finding.get(finding.id, ()):
            if (
                finding.id not in projection.visible_finding_ids
                or citation.source_id not in projection.visible_source_ids
            ):
                dropped.append(citation.source_id)
                continue
            external = index.resolve(citation.source_id)
            if external is None:
                dropped.append(citation.source_id)
                continue
            # The model may carry a prose note for its own reasoning, but it cannot establish
            # evidence by quoting caller-controlled text. Keep only the index-owned reference and
            # digest; findings can cite the source without copying raw notes into the report.
            added.append(external)
            concerns = _concerns_evidence(citation, audit_input, projection)
            if concerns is not None:
                added.append(concerns)
        if added:
            merged = _deduplicate((*finding.evidence, *added))
            updated = finding.model_copy(update={"evidence": merged})
            all_findings.append(updated)
            enriched.append(updated)
        else:
            all_findings.append(finding)
    return RegulatoryEvidenceResult(
        findings=tuple(all_findings),
        enriched=tuple(enriched),
        dropped_citations=tuple(dropped),
    )


def _concerns_evidence(
    citation: _ProposedCitation,
    audit_input: AuditInput,
    projection: _RegulatoryProjection,
) -> Evidence | None:
    """Return only Run references whose complete identity record was visible to the model."""
    kind = citation.concerns_kind
    ref = citation.concerns_ref
    if kind is None or ref is None:
        return None
    if (
        kind == "event"
        and ref in {f"seq:{seq}" for seq in projection.visible_event_seqs}
        and ref
        in {
            f"seq:{event.seq}" for event in audit_input.events if event.run_id == audit_input.run_id
        }
    ):
        return Evidence(kind="event", ref=ref)
    if kind == "artifact" and ref in projection.visible_artifact_refs:
        return Evidence(kind="artifact", ref=ref)
    if kind == "experiment" and ref in projection.visible_experiment_ids:
        return Evidence(kind="experiment", ref=ref)
    return None


def _deduplicate(evidence: Iterable[Evidence]) -> tuple[Evidence, ...]:
    """Return distinct evidence in first-seen order, matching the assurance index's key."""
    unique: dict[tuple[str, str, str | None, str | None], Evidence] = {}
    for reference in evidence:
        key = (reference.kind, reference.ref, reference.sha256, reference.note)
        unique.setdefault(key, reference)
    return tuple(unique.values())


def _build_proposal_projection(
    audit_input: AuditInput,
    findings: tuple[AuditFinding, ...],
    index: CitationIndex,
) -> _RegulatoryProjection:
    """Fit complete candidate, source and event records under the exact frame budget."""
    projection: dict[str, object] = {
        "run_id": audit_input.run_id,
        "findings": [],
        "source_catalog": [],
        "events": [],
        "omitted_records": {"findings": 0, "sources": 0, "events": 0},
    }
    chunks = index.sources()
    run_events = tuple(event for event in audit_input.events if event.run_id == audit_input.run_id)
    foreign_event_count = len(audit_input.events) - len(run_events)
    if _render_projection(projection) is None:
        return _empty_projection(len(findings), len(chunks), len(audit_input.events))

    visible_findings: dict[str, dict[str, object]] = {}
    omitted_findings = 0
    for finding in findings:
        if not _identity_preserved(finding.id):
            omitted_findings += 1
            continue
        record: dict[str, object] = {
            "finding_id": finding.id,
            "run_id": finding.run_id,
            "agent_id": finding.agent_id,
            "framework": finding.framework.value,
            "control_id": finding.control_id,
            "title": finding.title,
            "finding": finding.finding,
            "severity": finding.severity.value,
            "confidence": finding.confidence,
            "recommendation": finding.recommendation,
            "evidence": [
                {"kind": item.kind, "ref": item.ref, "sha256": item.sha256}
                for item in finding.evidence
            ],
        }
        trial = {**visible_findings, finding.id: record}
        projection["findings"] = list(trial.values())
        projection["omitted_records"] = {
            "findings": omitted_findings,
            "sources": 0,
            "events": 0,
        }
        if _render_projection(projection) is None:
            omitted_findings += 1
            continue
        visible_findings = trial

    visible_sources: dict[str, dict[str, object]] = {}
    omitted_sources = 0
    projection["findings"] = list(visible_findings.values())
    for chunk in chunks:
        if not _identity_preserved(chunk.source_id):
            omitted_sources += 1
            continue
        record = {
            "source_id": chunk.source_id,
            "framework": chunk.framework.value,
            "version": chunk.version,
            "location": chunk.location,
            "sha256": chunk.sha256,
        }
        trial = {**visible_sources, chunk.source_id: record}
        projection["source_catalog"] = list(trial.values())
        projection["omitted_records"] = {
            "findings": omitted_findings,
            "sources": omitted_sources,
            "events": 0,
        }
        if _render_projection(projection) is None:
            omitted_sources += 1
            continue
        visible_sources = trial

    projection["source_catalog"] = list(visible_sources.values())
    visible_events, omitted_events, prompt = _fit_projected_events(
        projection,
        tuple(current_surface(run_events)),
        omitted_findings=omitted_findings,
        omitted_sources=omitted_sources,
        omitted_events=foreign_event_count,
    )
    if prompt is None:
        return _empty_projection(len(findings), len(chunks), len(audit_input.events))

    artifact_refs = frozenset(
        reference
        for event in visible_events.values()
        if event.type is EventType.ARTIFACT_CREATED
        for reference in _event_identifiers(event, ("name", "artifact_id", "id"))
        if _identity_preserved(reference)
    )
    experiment_ids = frozenset(
        reference
        for event in visible_events.values()
        if event.type in {EventType.EXPERIMENT_STARTED, EventType.EXPERIMENT_COMPLETED}
        for reference in _event_identifiers(event, ("experiment_id", "id"))
        if _identity_preserved(reference)
    )
    return _RegulatoryProjection(
        prompt=prompt,
        visible_finding_ids=frozenset(
            identifier for identifier in visible_findings if _identity_preserved(identifier)
        ),
        visible_source_ids=frozenset(
            identifier for identifier in visible_sources if _identity_preserved(identifier)
        ),
        visible_event_seqs=frozenset(visible_events),
        visible_artifact_refs=artifact_refs,
        visible_experiment_ids=experiment_ids,
        omitted_finding_count=omitted_findings,
        omitted_source_count=omitted_sources,
        omitted_event_count=omitted_events,
    )


def _fit_projected_events(
    projection: dict[str, object],
    events: tuple[Event, ...],
    *,
    omitted_findings: int,
    omitted_sources: int,
    omitted_events: int,
) -> tuple[dict[int, Event], int, str | None]:
    """Fit whole optional Run-event records and bounded omission counts into the frame."""
    visible_events: dict[int, Event] = {}
    for event in events:
        trial = {**visible_events, event.seq: event}
        projection["events"] = [_event_projection(item) for item in trial.values()]
        projection["omitted_records"] = {
            "findings": omitted_findings,
            "sources": omitted_sources,
            "events": omitted_events,
        }
        if _render_projection(projection) is None:
            omitted_events += 1
            continue
        visible_events = trial

    projection["events"] = [_event_projection(item) for item in visible_events.values()]
    projection["omitted_records"] = {
        "findings": omitted_findings,
        "sources": omitted_sources,
        "events": omitted_events,
    }
    prompt = _render_projection(projection)
    while prompt is None and visible_events:
        visible_events.pop(next(reversed(visible_events)))
        omitted_events += 1
        projection["events"] = [_event_projection(item) for item in visible_events.values()]
        projection["omitted_records"] = {
            "findings": omitted_findings,
            "sources": omitted_sources,
            "events": omitted_events,
        }
        prompt = _render_projection(projection)
    if prompt is None:
        return visible_events, omitted_events, None
    return visible_events, omitted_events, prompt


def _render_projection(projection: dict[str, object]) -> str | None:
    """Redact and size serialized JSON exactly, including the no-cap nonce frame."""
    rendered = canonical_json(redact_value(scrub_credentials_value(projection)))
    escaped = rendered.replace("<", r"\u003c").replace(">", r"\u003e")
    if len(frame_overhead("mira-regulatory-evidence")) + len(escaped) > _MAX_PROJECTION_CHARS:
        return None
    return frame_untrusted(rendered, label="mira-regulatory-evidence")


def _event_projection(event: Event) -> dict[str, object]:
    """Project the full event envelope and payload for an optional finding concern."""
    return event.model_dump(mode="json")


def _event_identifiers(event: Event, keys: tuple[str, ...]) -> tuple[str, ...]:
    """Return event-owned reference identifiers for visible artifact/experiment records."""
    payload = event.payload
    values = tuple(value for key in keys if isinstance(value := payload.get(key), str) and value)
    experiment = payload.get("experiment")
    if isinstance(experiment, dict):
        nested = experiment.get("id")
        if isinstance(nested, str) and nested:
            values = (*values, nested)
    subject = event.subject_id
    return (*values, *((subject,) if isinstance(subject, str) and subject else ()))


def _identity_preserved(identifier: str) -> bool:
    """Accept an identifier only when source-bound redaction leaves it unchanged."""
    return identifier == redact_value(scrub_credentials_value(identifier))


def _empty_projection(
    omitted_findings: int, omitted_sources: int, omitted_events: int
) -> _RegulatoryProjection:
    """Return a no-prompt projection with the bounded omission counts preserved."""
    return _RegulatoryProjection(
        prompt=None,
        visible_finding_ids=frozenset(),
        visible_source_ids=frozenset(),
        visible_event_seqs=frozenset(),
        visible_artifact_refs=frozenset(),
        visible_experiment_ids=frozenset(),
        omitted_finding_count=omitted_findings,
        omitted_source_count=omitted_sources,
        omitted_event_count=omitted_events,
    )


def _omission_payload(projection: _RegulatoryProjection) -> dict[str, int]:
    """Return compact omission counts for the agent lifecycle event."""
    return {
        "omitted_finding_count": projection.omitted_finding_count,
        "omitted_source_count": projection.omitted_source_count,
        "omitted_event_count": projection.omitted_event_count,
    }


def _record_skipped_projection(
    spec: AuditAgentSpec, context: AuditAgentContext, projection: _RegulatoryProjection
) -> None:
    """Record that no complete candidate/source pair fit before any model call."""
    payload = {
        "agent": spec.name,
        "agent_id": context.agent_id,
        "task_id": context.task_id,
        "framework": spec.framework.value,
        **_omission_payload(projection),
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
    "CitationIndex",
    "RegulatoryEvidenceResult",
    "enrich_findings",
]
