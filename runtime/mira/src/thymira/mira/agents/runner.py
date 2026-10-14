"""Bounded execution of one MIRA audit-agent declaration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic import Field
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import ModelSettings, RunContext
from pydantic_ai import Tool as PydanticTool
from pydantic_ai.exceptions import ModelRetry, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.usage import UsageLimits

from thymira.agents.llm.base import LLMProvider, LLMStructuredOutputError
from thymira.agents.llm.routing import ModelChoice, Role
from thymira.agents.model_binding import routed_model
from thymira.agents.prompt_framing import frame_untrusted
from thymira.agents.tool_bridge import render_tool_output
from thymira.agents.usage import RunUsage
from thymira.mira.agents.evidence_projection import (
    EvidenceProjection,
    EvidenceProjectionLimits,
)
from thymira.mira.agents.evidence_projection import (
    build_evidence_projection as _build_evidence_projection,
)
from thymira.mira.agents.required_tools import (
    latest_applicable_regulation_search,
    required_tool_failures,
    unattempted_required_tools,
)
from thymira.mira.audit_io import AuditAgentOutput, AuditInput
from thymira.mira.checks import AuditMode
from thymira.mira.evidence import EvidenceExcerpt, EvidenceReader
from thymira.mira.finding_safety import safe_finding
from thymira.mira.grounding import (
    RunEvidenceIndex,
    ground_findings,
    unbacked_summary,
)
from thymira.schemas import (
    Actor,
    AuditFinding,
    Event,
    EventType,
    Evidence,
    Framework,
    Severity,
    TaskStatus,
    ThymiraModel,
    id_kind,
    new_id,
    utc_now,
)
from thymira.tools import ToolContext, ToolManager, ToolRegistry, input_schema

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.agents.llm.base import LLMResponse
    from thymira.agents.request_ledger import RequestLedger
    from thymira.agents.runtime_catalog import RuntimeSkillCatalog
    from thymira.events import EventLog
    from thymira.mira.agents.spec import AuditAgentSpec
    from thymira.schemas import ModelRoutePolicy
    from thymira.tools import Tool as ToolContract

    type AuditToolContextFactory = Callable[[str, str], ToolContext]


@dataclass(frozen=True, slots=True)
class AuditAgentContext:
    """The in-process dependencies and identity of one audit-agent invocation."""

    event_log: EventLog
    actor: Actor
    agent_id: str
    task_id: str
    runtime_skill_catalog: RuntimeSkillCatalog | None = None
    runtime_skill_budget: int = 32_000
    runtime_skill_names: tuple[str, ...] = ()
    runtime_skill_request_id: str | None = None
    runtime_skill_dispatch_id: str | None = None
    request_ledger: RequestLedger | None = None
    provider: LLMProvider | None = None
    tool_registry: ToolRegistry | None = None
    tool_context: ToolContext | None = None
    evidence_reader: EvidenceReader | None = None
    projection_limits: EvidenceProjectionLimits = field(default_factory=EvidenceProjectionLimits)
    before_model_selection: Callable[[], None] | None = None
    before_model_call: Callable[[ModelChoice], None] | None = None
    record_model_usage: Callable[[LLMResponse], None] | None = None
    route_policy: ModelRoutePolicy | None = None


class _FindingContent(ThymiraModel):
    """The finding content a model may propose without authoritative identity fields."""

    control_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: tuple[Evidence, ...] = ()
    recommendation: str | None = None


class _AuditAgentResponse(ThymiraModel):
    """The complete structured response returned by one audit agent."""

    findings: tuple[_FindingContent, ...] = ()


_MODEL_CONTROL_ID_PREFIX = "model:"
"""Namespace for control ids proposed by an audit model."""


def run_audit_agent(
    spec: AuditAgentSpec,
    audit_input: AuditInput,
    context: AuditAgentContext,
) -> AuditAgentOutput:
    """Run one audit agent and return only its normalised candidate findings.

    Raises:
        ValueError: If the context, evidence projection, or hash-pinned excerpt is invalid.
        LLMStructuredOutputError: If structured output cannot be validated within ``max_turns``
            or a required tool call does not complete successfully.
    """
    validate_audit_agent_context(spec, audit_input, context)
    projection = _build_evidence_projection(
        audit_input, context.evidence_reader, context.projection_limits
    )
    prompt = projection.prompt
    instructions = runtime_skill_instructions(spec, context, audit_input)
    runtime_evidence = runtime_skill_event_payload(context)
    event_start = len(context.event_log.events())
    task_kind = spec.task_kinds[0]
    step_usage = RunUsage()
    model = routed_model(
        Role.MIRA,
        task_kind,
        context.event_log,
        requested_tier=spec.tier,
        provider=context.provider,
        actor=context.actor,
        output_schema=_AuditAgentResponse,
        runtime_skill_evidence=runtime_evidence,
        before_model_selection=context.before_model_selection,
        before_model_call=context.before_model_call,
        record_model_usage=_step_usage_recorder(step_usage, context.record_model_usage),
        route_policy=context.route_policy,
        request_ledger=context.request_ledger,
        request_owner_id=(
            context.runtime_skill_dispatch_id or f"{context.task_id}:{context.agent_id}"
        ),
    )
    tools = build_audit_tools(spec, context)
    agent: PydanticAgent[None, _AuditAgentResponse] = PydanticAgent(
        model=model,
        output_type=_AuditAgentResponse,
        instructions=instructions,
        retries=max(spec.max_turns - 1, 0),
        tools=tools,
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
    required_tool_retry_sent = False

    @agent.output_validator
    def _require_declared_audit_tools(
        run_context: RunContext[None], output: _AuditAgentResponse
    ) -> _AuditAgentResponse:
        """Give one bounded correction when the model skips a required audit tool."""
        nonlocal required_tool_retry_sent
        cycle_events = context.event_log.events()[event_start:]
        unattempted = unattempted_required_tools(
            cycle_events,
            context.task_id,
            spec.required_tool_calls,
        )
        correction_fits = run_context.usage.requests + 2 <= spec.max_turns
        if unattempted and not required_tool_retry_sent and correction_fits:
            required_tool_retry_sent = True
            rendered = ", ".join(unattempted)
            message = (
                "Required audit evidence is missing. Before returning final_result, call each "
                f"of these required audit tools and use its returned evidence: {rendered}."
            )
            raise ModelRetry(message)
        return output

    context.event_log.append(
        EventType.AGENT_STARTED,
        context.actor,
        {
            "agent": spec.name,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "framework": spec.framework.value,
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
                **_usage_payload(step_usage),
            },
            subject_id=context.agent_id,
            producer="thymira.mira",
        )
        message = f"MIRA audit agent {spec.name!r} exhausted structured-output validation"
        raise LLMStructuredOutputError(message) from None

    cycle_events = context.event_log.events()[event_start:]
    tool_failures = required_tool_failures(
        cycle_events,
        context.task_id,
        spec.required_tool_calls,
        audit_framework=spec.framework,
    )
    if tool_failures:
        reason = "; ".join(f"{tool}: {error}" if error else tool for tool, error in tool_failures)
        context.event_log.append(
            EventType.AGENT_COMPLETED,
            context.actor,
            {
                "agent": spec.name,
                "agent_id": context.agent_id,
                "task_id": context.task_id,
                "status": TaskStatus.FAILED.value,
                "reason": reason,
                **_usage_payload(step_usage),
            },
            subject_id=context.agent_id,
            producer="thymira.mira",
        )
        message = f"MIRA audit agent {spec.name!r} required audit tool did not complete: {reason}"
        raise LLMStructuredOutputError(message)

    proposed = tuple(
        _normalise_finding(content, spec, audit_input, context)
        for content in result.output.findings
    )
    # A Run event can back a citation only when this invocation received its bounded projection.
    # External chunks are narrower: only this task's latest resolved, canonical and
    # framework-applicable regulation search may back them.
    regulation_event = latest_applicable_regulation_search(
        cycle_events,
        context.task_id,
        spec.framework,
    )
    regulation_events = () if regulation_event is None else (regulation_event,)
    findings, unbacked = ground_findings(
        proposed,
        _visible_evidence_index(audit_input, projection, regulation_events),
    )
    model_choice = _last_model_choice(cycle_events)
    completion: dict[str, Any] = {
        "agent": spec.name,
        "agent_id": context.agent_id,
        "task_id": context.task_id,
        "status": TaskStatus.COMPLETED.value,
        **_usage_payload(step_usage),
    }
    if unbacked:
        completion["unbacked_evidence"] = unbacked_summary(unbacked)
    context.event_log.append(
        EventType.AGENT_COMPLETED,
        context.actor,
        completion,
        subject_id=context.agent_id,
        producer="thymira.mira",
    )
    return AuditAgentOutput.from_spec(spec, findings=findings, model_choice=model_choice)


def runtime_skill_instructions(
    spec: AuditAgentSpec,
    context: AuditAgentContext,
    audit_input: AuditInput | None = None,
) -> str:
    """Return MIRA instructions with selected runtime skill bodies loaded on demand."""
    catalog = context.runtime_skill_catalog
    parts = [spec.system_prompt]
    if catalog is not None:
        selected = context.runtime_skill_names or spec.runtime_skill_names
        selection = catalog.select(
            selected,
            max_bytes=context.runtime_skill_budget,
            request_id=context.runtime_skill_request_id,
            dispatch_id=(
                context.runtime_skill_dispatch_id or f"{context.task_id}:{context.agent_id}"
            ),
        )
        parts.append(catalog.index_text())
        if selection.rendered:
            parts.append(selection.rendered)
    mode = audit_input.audit_mode if audit_input is not None else AuditMode.FINAL
    boundary_instruction = (
        "This is an in-flight audit before Gate/closeout. Missing run.completed or run.failed "
        "evidence at this boundary is expected and is not itself a finding. Do not claim the "
        "Run is complete."
        if mode is AuditMode.IN_FLIGHT
        else "This is a final audit. Assess closeout only from visible terminal-event evidence; "
        "do not assume closure when no such evidence is present."
    )
    parts.append(
        "Audit evidence rules (code-owned):\n"
        "- Propose adverse, unmet, or unresolved concerns only. A positive compliance or "
        "assurance statement is not a finding; return no finding for a control that the visible "
        "evidence supports as satisfied. Do not create a LOW or MEDIUM finding just to express "
        "a positive statement.\n"
        "- Every cited event must be visible in the supplied evidence projection and relevant "
        "to the specific claim. Event existence alone does not prove the claim. A schema field "
        "or dataset attribute alone does not prove that a value was used as a model feature.\n"
        "- Treat any activity profile as a declared scope statement, not an authoritative legal "
        "applicability decision or proof of an exemption. Do not infer legal applicability, "
        "exemption, deployment, or direct effect on affected people from a dataset name or EDA "
        "alone. Do not infer that recorded tool approvals establish all consequential human "
        "oversight. Preserve a genuine evidence gap as a candidate for policy review; do not "
        "change severity or policy based on model-selected confidence.\n"
        "- Findings listed in audit_context.prior_candidate_findings are prior proposals, not "
        "verified conclusions. Do not repeat or paraphrase them unless new evidence materially "
        "changes the concern's scope or consequence; then describe only that material change "
        "and cite the distinguishing evidence. The summary may report omitted older proposals.\n"
        f"- Audit boundary: {mode.value}. {boundary_instruction}"
    )
    return "\n\n".join(parts)


def runtime_skill_event_payload(context: AuditAgentContext) -> dict[str, object]:
    """Return canonical evidence for the selected MIRA runtime projection."""
    catalog = context.runtime_skill_catalog
    if catalog is None:
        return {}
    return catalog.selection_event_payload()


def _visible_evidence_index(
    audit_input: AuditInput,
    projection: EvidenceProjection,
    regulation_events: Sequence[Event],
) -> RunEvidenceIndex:
    """Ground against visible Run events and hash-verified excerpts shown in the prompt."""
    projected_records = {record.seq: record for record in projection.visible_event_records}
    context_hashes = dict(projection.visible_context_event_hashes)
    visible_events = []
    for event in audit_input.events:
        if event.seq not in projection.visible_event_seqs:
            continue
        record = projected_records.get(event.seq)
        if record is not None and (
            record.event_type is not event.type or record.event_hash != event.hash
        ):
            continue
        visible_events.append(
            event.model_copy(
                update={
                    "payload": dict(record.payload) if record and record.payload else {},
                    "subject_id": None,
                    "hash": (record.event_hash if record else context_hashes.get(event.seq)),
                }
            )
        )
    projected_index = RunEvidenceIndex.from_events(
        visible_events,
        regulation_events=regulation_events,
    )
    complete_index = RunEvidenceIndex.from_events(audit_input.events)
    visible_index = _intersect_visible_evidence(projected_index, complete_index)
    artifact_digests = dict(visible_index.artifact_digests)
    for evidence in projection.visible_artifact_evidence:
        if (
            evidence.sha256 is not None
            and complete_index.artifact_digests.get(evidence.ref) == evidence.sha256
        ):
            artifact_digests[evidence.ref] = evidence.sha256
    return replace(visible_index, artifact_digests=artifact_digests)


def _intersect_visible_evidence(
    projected: RunEvidenceIndex, authoritative: RunEvidenceIndex
) -> RunEvidenceIndex:
    """Keep only delivered references confirmed by the authoritative Run history."""
    event_hashes = {
        reference: digest
        for reference, digest in projected.event_hashes.items()
        if reference in authoritative.event_hashes
        and (digest is None or authoritative.event_hashes[reference] == digest)
    }
    artifact_digests = {
        reference: (
            digest
            if digest is not None and authoritative.artifact_digests.get(reference) == digest
            else None
        )
        for reference, digest in projected.artifact_digests.items()
        if reference in authoritative.artifact_digests
    }
    return replace(
        projected,
        event_hashes=event_hashes,
        artifact_digests=artifact_digests,
        experiment_ids=projected.experiment_ids & authoritative.experiment_ids,
        tool_call_ids=projected.tool_call_ids & authoritative.tool_call_ids,
    )


def validate_audit_agent_context(
    spec: AuditAgentSpec, audit_input: AuditInput, context: AuditAgentContext
) -> None:
    """Reject invalid execution dependencies before any lifecycle event is appended."""
    _require_id_kind(context.agent_id, field_name="agent_id", expected="agent")
    _require_id_kind(context.task_id, field_name="task_id", expected="task")
    if audit_input.run_id != context.event_log.run_id:
        raise ValueError("AuditInput.run_id must match the runner event log run_id")
    if any(event.run_id != audit_input.run_id for event in audit_input.events):
        raise ValueError("AuditInput events must all belong to AuditInput.run_id")
    if not spec.task_kinds:
        raise ValueError("AuditAgentSpec.task_kinds must not be empty")
    limits = context.projection_limits
    if any(
        limit <= 0
        for limit in (
            limits.total_chars,
            limits.event_chars,
            limits.agent_message_chars,
            limits.excerpt_chars,
            limits.report_chars,
        )
    ):
        raise ValueError("evidence projection limits must be positive")
    registry = context.tool_registry
    tool_context = context.tool_context
    if (registry is None) is not (tool_context is None):
        raise ValueError("ToolRegistry and ToolContext must be provided together")
    if tool_context is None:
        return
    if tool_context.run_id != audit_input.run_id:
        raise ValueError("tool context run_id must match the runner input")
    if tool_context.agent_id != context.agent_id:
        raise ValueError("tool context agent_id must match the runner context")
    if tool_context.task_id != context.task_id:
        raise ValueError("tool context task_id must match the runner context")
    if tool_context.event_log is not context.event_log:
        raise ValueError("tool context event_log must match the runner event log")


def _require_id_kind(value: str, *, field_name: str, expected: str) -> None:
    """Reject a malformed or wrongly prefixed execution identity."""
    article = "an" if expected[0] in "aeiou" else "a"
    message = f"{field_name} must be {article} {expected} id"
    try:
        actual = id_kind(value)
    except ValueError as exc:
        raise ValueError(message) from exc
    if actual != expected:
        raise ValueError(message)


def build_audit_tools(
    spec: AuditAgentSpec, context: AuditAgentContext
) -> tuple[PydanticTool[None], ...]:
    """Expose the MIRA registry through the manager with the spec allowlist as a hard guard."""
    if context.tool_registry is None or context.tool_context is None:
        return ()
    manager = ToolManager(context.tool_registry)
    tool_context = replace(
        context.tool_context,
        allowed_tools=frozenset(spec.tool_allowlist),
    )
    required_regulation_framework = (
        spec.framework if "search_regulation" in spec.required_tool_calls else None
    )
    return tuple(
        _build_audit_tool(
            manager,
            tool_context,
            tool,
            required_regulation_framework=required_regulation_framework,
        )
        for tool in context.tool_registry
    )


def _build_audit_tool(
    manager: ToolManager,
    context: ToolContext,
    tool: ToolContract,
    *,
    required_regulation_framework: Framework | None,
) -> PydanticTool[None]:
    """Adapt one registered tool without recreating its authorisation or execution logic."""
    tool_name = tool.name

    def call(**arguments: Any) -> str:
        """Delegate one model tool request to the Tool Manager."""
        effective_arguments = arguments
        if (
            tool_name == "search_regulation"
            and required_regulation_framework is not None
            and arguments.get("framework") is None
        ):
            effective_arguments = {
                **arguments,
                "framework": required_regulation_framework,
            }
        execution = manager.execute(context, tool_name, effective_arguments)
        return frame_untrusted(render_tool_output(execution), label="mira-tool-output")

    return PydanticTool.from_schema(
        call,
        name=tool_name,
        description=tool.description,
        json_schema=input_schema(tool),
    )


def _normalise_finding(
    content: _FindingContent,
    spec: AuditAgentSpec,
    audit_input: AuditInput,
    context: AuditAgentContext,
) -> AuditFinding:
    """Assign all authority-owned finding identity fields in code rather than from model output."""
    return safe_finding(
        AuditFinding(
            id=new_id("finding"),
            run_id=audit_input.run_id,
            agent_id=context.agent_id,
            control_id=f"{_MODEL_CONTROL_ID_PREFIX}{content.control_id}",
            framework=spec.framework,
            title=content.title,
            finding=content.finding,
            severity=content.severity,
            confidence=content.confidence,
            evidence=content.evidence,
            recommendation=content.recommendation,
            created_at=utc_now(),
        )
    )


def _step_usage_recorder(
    step_usage: RunUsage, delegate: Callable[[LLMResponse], None] | None
) -> Callable[[LLMResponse], None]:
    """Charge one invocation's own accumulator, then the caller's real ledger.

    `AuditAgentContext` carries no `RunUsage` of its own (unlike `AgentContext`), so nothing
    before this diffed a per-step cost the way `runner.py`'s `AGENT_COMPLETED` does for THY --
    MIRA's own `AGENT_COMPLETED` always recorded `cost_usd`-shaped evidence as absent, even though
    the real cost was already being charged into the run-wide `UsageLedger` via
    `context.record_model_usage` (`_model_usage_recorder`, unaffected here: `delegate` is still
    called with the exact same `response`, so budget enforcement is unchanged). `step_usage` only
    has one job: hold the numbers long enough for `_usage_payload` to attach them to this
    invocation's own completion/failure events.
    """

    def record(response: LLMResponse) -> None:
        step_usage.charge(response)
        if delegate is not None:
            delegate(response)

    return record


def _usage_payload(step_usage: RunUsage) -> dict[str, Any]:
    """Return this invocation's own token/cost evidence for an `AGENT_COMPLETED` payload.

    Mirrors the shape `runner.py` (`thymira.agents`) already attaches for THY's own agents
    (`input_tokens`/`output_tokens`/`cost_usd`), so the two producers of that event carry the same
    fields. `cost_usd` stays `None` exactly when `RunUsage.charge` already degraded it to unknown
    (no price for the routed model) -- never coerced to `0.0`, for the same reason `RunUsage`'s own
    docstring gives: an unpriced call must not look like a free one.
    """
    return {
        "input_tokens": step_usage.input_tokens,
        "output_tokens": step_usage.output_tokens,
        "cost_usd": step_usage.cost_usd,
    }


def _last_model_choice(events: Sequence[Event]) -> ModelChoice | None:
    """Return the last router selection emitted in this execution cycle, if any."""
    selected = [event for event in events if event.type is EventType.MODEL_SELECTED]
    if not selected:
        return None
    payload = selected[-1].payload
    return ModelChoice.model_validate(
        {field: payload[field] for field in ModelChoice.model_fields if field in payload}
    )


__all__ = [
    "AuditAgentContext",
    "EvidenceExcerpt",
    "EvidenceProjectionLimits",
    "EvidenceReader",
    "build_audit_tools",
    "run_audit_agent",
    "runtime_skill_instructions",
    "validate_audit_agent_context",
]
