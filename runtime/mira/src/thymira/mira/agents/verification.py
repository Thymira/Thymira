"""The provider-backed adversarial finding verifier (MIRA-03).

``thymira.mira.flow`` receives a :data:`~thymira.mira.verification.FindingVerifier` by injection
and never imports this module; this is its concrete, second-pass-judge implementation, which may
import ``thymira.agents`` (P2). It re-judges one candidate finding against a bounded, redacted
projection of the Run's audit evidence through the provider's structured-output path and
returns a :class:`~thymira.mira.verification.FindingVerdict`.

The verifier records no event and authorizes nothing: dropping an unsupported finding is a
conservative narrowing, and the flow only ever submits *agent-authored* candidates to it -- a
code-authored deterministic or preflight finding is never verified or dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.agents.llm.base import LLMStructuredOutputError
from thymira.agents.llm.routing import ModelChoice, Role, choose
from thymira.agents.prompt_framing import frame_overhead, frame_untrusted
from thymira.agents.request_ledger import RequestLedger, instrument_provider
from thymira.agents.route_policy import enforce_model_route
from thymira.events import (
    canonical_json,
    current_surface,
    redact_value,
    scrub_credentials,
    scrub_credentials_value,
)
from thymira.mira.checks.models import ApplicabilityJudgment
from thymira.mira.grounding import RunEvidenceIndex, regulation_search_result
from thymira.mira.verification import FindingVerdict
from thymira.schemas import ActivityProfile, Actor, EventType, approval_decision_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.agents.llm.base import LLMProvider, LLMResponse
    from thymira.events import EventLog
    from thymira.mira.checks import AuditReport
    from thymira.mira.verification import FindingVerifier
    from thymira.schemas import AuditFinding, Event, Evidence, ModelRoutePolicy

_DEFAULT_INSTRUCTIONS = (
    "You are MIRA's adversarial finding verifier. You are given one candidate audit finding and "
    "a bounded, redacted projection of the run's audit evidence. Judge whether the visible "
    "recorded evidence supports the finding. Set supported=true only when visible evidence "
    "substantiates it; set supported=false only when the visible evidence is sufficient to refute "
    "it. Set supported=null when a material record or underlying artifact content is unavailable. "
    "Never treat omitted records as proof that an event did not happen, and never invent evidence. "
    "Event metadata does not expose artifact contents; use supported=null when content is needed "
    "to decide the claim but is not present in the projection. Applicability is a separate "
    "structured judgment about whether this finding's control concerns the recorded activity. "
    "Use the exact current activity profile and the cited events; do not infer applicability from "
    "severity, sensitive data, an offline label, or a pack-presence control. Distinguish "
    "descriptive "
    "EDA/correlation, training, privacy or integrity work from an evidenced deployment, "
    "recommendation, ranking, or credit decision. Do not treat a tool approval as universal "
    "oversight; it proves only the linked action. During IN_FLIGHT, absent run.completed or "
    "run.failed is expected and does not prove absent oversight; assess terminal closeout only "
    "for FINAL. Return undetermined when scope or action is unclear. For a known applicability "
    "status, provide a rationale, the concrete evidenced action, and event Evidence references "
    "whose seq and hash are visible in this projection, including the exact current profile event. "
    "When a completed tool action is visible, name that executed action and cite its "
    "tool.completed event as well as the current profile. A profile declaration alone does not "
    "show what ran. Reviewing this candidate or hypothetical documentation is not an executed "
    "action of the audited Run. "
    "Never let applicability change supported."
)

_MAX_EVENTS = 40
"""How many curated audit events to show the judge, at most."""

_MAX_PROJECTION_CHARS = 12_000
"""Maximum size of the complete redacted projection, including its untrusted frame."""

_AUDIT_EVENT_TYPES = frozenset(
    {
        EventType.RUN_STARTED,
        EventType.RUN_COMPLETED,
        EventType.RUN_FAILED,
        EventType.RUN_TRANSITIONED,
        EventType.TOOL_STARTED,
        EventType.TOOL_COMPLETED,
        EventType.TOOL_DENIED,
        EventType.POLICY_DECISION,
        EventType.HUMAN_APPROVAL_REQUESTED,
        EventType.HUMAN_APPROVAL,
        EventType.MODEL_TRAINED,
        EventType.ACTIVITY_PROFILE_RECORDED,
        EventType.AUDIT_STARTED,
        EventType.RISK_ASSESSMENT_RECORDED,
        EventType.PACK_BINDING_RECORDED,
        EventType.CONTROL_EVALUATION_RECORDED,
        EventType.MIRA_CONTEXT_CREATED,
        EventType.ARTIFACT_CREATED,
        EventType.EXPERIMENT_STARTED,
        EventType.EXPERIMENT_COMPLETED,
    }
)
"""Typed audit events that MIRA may inspect even when they are LOG_ONLY globally."""

_VERIFICATION_FRAME = "mira-verification"


@dataclass(frozen=True, slots=True)
class _VerificationProjection:
    """One complete candidate projection, or a safe reason the candidate cannot be judged."""

    prompt: str | None
    unavailable_rationale: str | None = None
    visible_event_hashes: frozenset[tuple[str, str]] = frozenset()
    current_profile_evidence: tuple[str, str] | None = None
    model_effect_evidence_complete: bool = True


def build_finding_verifier(
    provider: LLMProvider,
    *,
    instructions: str = _DEFAULT_INSTRUCTIONS,
    event_log: EventLog | None = None,
    before_model_selection: Callable[[], None] | None = None,
    before_model_call: Callable[[ModelChoice], None] | None = None,
    record_model_usage: Callable[[LLMResponse], None] | None = None,
    request_ledger: RequestLedger | None = None,
    request_owner_id: str = "mira-finding-verifier",
    route_policy: ModelRoutePolicy | None = None,
) -> FindingVerifier:
    """Return a :data:`FindingVerifier` that judges each candidate through ``provider``.

    Args:
        provider: The structured-output LLM provider (a ``ScriptedProvider`` in tests).
        instructions: The system prompt handed to the judge.
        event_log: Optional log for the routed ``model.selected`` evidence.
        before_model_selection: Optional budget hook before routing.
        before_model_call: Optional Gate hook after routing and before invocation.
        record_model_usage: Optional hook for the measured provider response.
        request_ledger: Optional durable ledger for the effective provider request and response.
        request_owner_id: Durable owner of the model-visible verifier facts.
        route_policy: Immutable session allowlist checked before the structured provider call.

    Returns:
        A callable that maps a candidate finding and the run evidence to a
        :class:`~thymira.mira.verification.FindingVerdict`.
    """

    def verify(
        finding: AuditFinding,
        events: tuple[Event, ...],
        report: AuditReport,
    ) -> FindingVerdict:
        """Judge one candidate finding against curated audit evidence."""
        projection = _verification_prompt(finding, events, report)
        if projection.prompt is None:
            return FindingVerdict(
                supported=None,
                rationale=projection.unavailable_rationale
                or "The verifier could not assemble complete required evidence.",
                applicability=ApplicabilityJudgment(
                    status="undetermined",
                    rationale=(
                        "Applicability is undetermined because required "
                        "audit context is unavailable."
                    ),
                ),
            )
        if before_model_selection is not None:
            before_model_selection()
        choice = choose(Role.MIRA, "audit_judgement")
        if event_log is not None:
            event_log.append(EventType.MODEL_SELECTED, Actor.system(), choice.event_payload())
        enforce_model_route(
            choice,
            route_policy,
            event_log=event_log,
            actor=Actor.system(),
        )
        if before_model_call is not None:
            before_model_call(choice)
        # This is a direct provider call, outside RoutedModel's final-message guard.  Scrub at
        # the source boundary so a caller-controlled instruction or evidence value can never
        # carry a known credential to the provider.
        prompt = projection.prompt
        request_provider = provider
        detach = getattr(request_provider, "without_request_ledger", None)
        if callable(detach):
            request_provider = detach()
        resolved_ledger = request_ledger
        if (
            resolved_ledger is None
            and event_log is not None
            and callable(getattr(request_provider, "attach_request_ledger", None))
        ):
            resolved_ledger = RequestLedger(event_log)
        active_provider = (
            instrument_provider(request_provider, resolved_ledger, owner_id=request_owner_id)
            if resolved_ledger is not None
            else request_provider
        )
        try:
            verdict, response = active_provider.complete_structured(
                prompt, schema=FindingVerdict, system=scrub_credentials(instructions)
            )
        except LLMStructuredOutputError as exc:
            if exc.response is not None and record_model_usage is not None:
                record_model_usage(exc.response)
            raise
        if record_model_usage is not None:
            record_model_usage(response)
        if verdict.supported is None and not verdict.rationale:
            verdict = verdict.model_copy(
                update={
                    "rationale": (
                        "The provider could not make a grounded ruling from visible evidence."
                    )
                }
            )
        return _ground_applicability(verdict, projection)

    return verify


def _verification_prompt(
    finding: AuditFinding, events: tuple[Event, ...], report: AuditReport
) -> _VerificationProjection:
    """Render complete candidate and evidence records under the exact framed-message budget."""
    input_problem = (
        "The verifier candidate has no agent identity."
        if finding.agent_id is None
        else "The verifier snapshot contains an event from another Run."
        if any(event.run_id != finding.run_id for event in events)
        else "The verifier report belongs to another Run."
        if report.run_id != finding.run_id
        else None
    )
    if input_problem is not None:
        return _unavailable(input_problem)

    (
        audit_context,
        scope_events,
        priority_context_events,
        scope_evidence,
        scope_reason,
    ) = _audit_context(report, events, finding.run_id)
    if scope_reason is not None:
        return _unavailable(scope_reason)

    by_seq = {f"seq:{event.seq}": event for event in events}
    required, reason = _cited_evidence_events(finding, events, by_seq)
    if reason is not None:
        return _unavailable(reason)
    related_approval_events, reason = _linked_approval_events(tuple(required.values()), events)
    if reason is not None:
        return _unavailable(reason)
    required.update((event.seq, event) for event in related_approval_events)
    required.update((event.seq, event) for event in scope_events)

    required_problem = (
        "Required cited records exceed the verifier event-record limit."
        if len(required) > _MAX_EVENTS
        else "A required cited event has no authoritative event hash."
        if any(not event.hash for event in required.values())
        else None
    )
    if required_problem is not None:
        return _unavailable(required_problem)

    candidate = {
        "id": finding.id,
        "run_id": finding.run_id,
        "agent_id": finding.agent_id,
        "control_id": finding.control_id,
        "framework": finding.framework.value,
        "title": finding.title,
        "finding": finding.finding,
        "severity": finding.severity.value,
        "confidence": finding.confidence,
        "recommendation": finding.recommendation,
        "evidence": [
            {"kind": item.kind, "ref": item.ref, "sha256": item.sha256} for item in finding.evidence
        ],
    }
    current_seqs = {event.seq for event in current_surface(events)}
    eligible = {
        event.seq: event
        for event in events
        if event.seq in current_seqs or event.type in _AUDIT_EVENT_TYPES
    }
    eligible.update(required)
    latest_model_effect = next(
        (
            event
            for event in reversed(events)
            if event.seq in eligible and event.type is EventType.MODEL_TRAINED
        ),
        None,
    )
    model_effect_events = (latest_model_effect,) if latest_model_effect is not None else ()
    return _fit_verification_projection(
        candidate,
        events,
        required,
        eligible,
        audit_context=audit_context,
        priority_context_events=priority_context_events,
        model_effect_events=model_effect_events,
        current_profile_evidence=scope_evidence,
    )


def _audit_context(
    report: AuditReport, events: tuple[Event, ...], run_id: str
) -> tuple[
    dict[str, object],
    tuple[Event, ...],
    tuple[Event, ...],
    tuple[str, str] | None,
    str | None,
]:
    """Bind the prompt to the exact current profile and select compact governance context."""
    profile_event, profile, source, reason = _current_activity_profile(report, events, run_id)
    if reason is not None:
        return {}, (), (), None, reason
    scope_events = (profile_event,) if profile_event is not None else ()
    priority_context_events = _governance_context_events(profile, source, events)
    current_profile_event = profile_event if profile is not None else None
    context = {
        "audit_mode": report.audit_mode.value,
        "report_activity_profile_id": report.activity_profile_id,
        "report_activity_profile_version": report.activity_profile_version,
        "activity_profile_is_declared_context": _profile_context_record(
            current_profile_event, profile, source
        ),
        "relevant_context_event_seqs": tuple(
            event.seq for event in (*scope_events, *priority_context_events)
        ),
    }
    evidence = (
        (f"seq:{current_profile_event.seq}", current_profile_event.hash)
        if current_profile_event is not None and current_profile_event.hash
        else None
    )
    return context, scope_events, priority_context_events, evidence, None


def _current_activity_profile(
    report: AuditReport, events: tuple[Event, ...], run_id: str
) -> tuple[Event | None, ActivityProfile | None, str, str | None]:
    """Select the latest valid profile declaration and reject stale or conflicting bindings."""
    profile_binding_claimed = (
        report.activity_profile_id is not None or report.activity_profile_version is not None
    )
    recorded = tuple(event for event in events if event.type is EventType.ACTIVITY_PROFILE_RECORDED)
    source = "ACTIVITY_PROFILE_RECORDED" if recorded else "AUDIT_STARTED"
    profile_event = (
        recorded[-1]
        if recorded
        else next((event for event in reversed(events) if _audit_start_declares_scope(event)), None)
    )
    profile = _profile_from_event(profile_event, run_id) if profile_event is not None else None

    if recorded and profile_event is not None:
        later_scope = next(
            (
                event
                for event in reversed(events)
                if event.seq > profile_event.seq and _audit_start_declares_scope(event)
            ),
            None,
        )
        started_profile = (
            _profile_from_event(later_scope, run_id) if later_scope is not None else profile
        )
        if later_scope is not None and (profile is None or started_profile != profile):
            return (
                None,
                None,
                source,
                "The latest audit-start scope conflicts with the recorded activity profile.",
            )

    binding_matches = (
        profile is not None
        and profile_event is not None
        and report.activity_profile_id == profile.id
        and report.activity_profile_version == profile.version
    )
    if profile_binding_claimed and not binding_matches:
        return None, None, source, "The report's activity-profile binding is absent or mismatched."
    if profile_binding_claimed and (
        report.activity_profile_id is None or report.activity_profile_version is None
    ):
        return None, None, source, "The report's activity-profile binding is incomplete."

    if profile is not None:
        for event in events:
            matching_identity = event.type in {
                EventType.ACTIVITY_PROFILE_RECORDED,
                EventType.AUDIT_STARTED,
            } and _event_profile_matches(event, profile.id, profile.version)
            if matching_identity and _profile_from_event(event, run_id) != profile:
                message = (
                    "The Run contains conflicting activity profiles with the same identity."
                    if event.type is EventType.ACTIVITY_PROFILE_RECORDED
                    else "An audit-start record conflicts with the exact activity-profile version."
                )
                return None, None, source, message
    if profile is None:
        profile_event = None
    return profile_event, profile, source, None


def _governance_context_events(
    profile: ActivityProfile | None, source: str, events: tuple[Event, ...]
) -> tuple[Event, ...]:
    """Select only the latest whole governance records linked to one exact profile version."""
    if profile is None:
        return ()
    profile_id, profile_version = profile.id, profile.version
    optional: dict[tuple[object, ...], Event] = {}
    if source == "ACTIVITY_PROFILE_RECORDED":
        audit_start = next(
            (
                event
                for event in reversed(events)
                if event.type is EventType.AUDIT_STARTED
                and _event_profile_matches(event, profile_id, profile_version)
                and event.hash
            ),
            None,
        )
        if audit_start is not None:
            optional[("audit_started",)] = audit_start
    risk = next(
        (
            event
            for event in reversed(events)
            if event.type is EventType.RISK_ASSESSMENT_RECORDED
            and _scope_value_matches(
                event.payload.get("risk_assessment"), profile_id, profile_version
            )
            and event.hash
        ),
        None,
    )
    if risk is not None:
        optional[("risk",)] = risk

    bindings: dict[object, Event] = {}
    binding_ids: set[str] = set()
    for event in reversed(events):
        binding = event.payload.get("pack_binding")
        if (
            event.type is not EventType.PACK_BINDING_RECORDED
            or not event.hash
            or not _scope_value_matches(binding, profile_id, profile_version)
        ):
            continue
        if not isinstance(binding, dict):
            continue
        pack_id = binding.get("pack_id")
        if pack_id in bindings:
            continue
        bindings[pack_id] = event
        binding_id = binding.get("id")
        if isinstance(binding_id, str):
            binding_ids.add(binding_id)
        optional[("pack", pack_id)] = event

    evaluations: dict[tuple[object, object], Event] = {}
    for event in reversed(events):
        evaluation = event.payload.get("control_evaluation")
        if (
            event.type is not EventType.CONTROL_EVALUATION_RECORDED
            or not event.hash
            or not isinstance(evaluation, dict)
            or evaluation.get("pack_binding_id") not in binding_ids
        ):
            continue
        identity = (evaluation.get("pack_binding_id"), evaluation.get("control_id"))
        evaluations.setdefault(identity, event)
    optional.update({("control_evaluation", *key): event for key, event in evaluations.items()})
    return tuple(sorted(optional.values(), key=lambda event: event.seq))


def _profile_context_record(
    event: Event | None, profile: ActivityProfile | None, source: str
) -> dict[str, object] | None:
    """Return the compact identifier for the exact current profile source event."""
    if event is None or profile is None:
        return None
    return {
        "id": profile.id,
        "version": profile.version,
        "event_seq": event.seq,
        "event_hash": event.hash,
        "source": source,
    }


def _profile_from_event(event: Event, run_id: str) -> ActivityProfile | None:
    """Validate a complete ActivityProfile from an authoritative same-Run event."""
    value = event.payload.get("activity_profile")
    if not isinstance(value, dict) or event.run_id != run_id or not event.hash:
        return None
    try:
        profile = ActivityProfile.model_validate_json(canonical_json(value))
    except (TypeError, ValueError):
        return None
    return profile if profile.run_id == run_id else None


def _audit_start_declares_scope(event: Event) -> bool:
    """Return whether an audit-start record makes an explicit activity-scope claim."""
    return event.type is EventType.AUDIT_STARTED and (
        isinstance(event.payload.get("activity_profile"), dict)
        or "activity_profile_id" in event.payload
        or "activity_profile_version" in event.payload
    )


def _scope_value_matches(value: object, profile_id: object, profile_version: object) -> bool:
    """Match an immutable scope-bearing model dump to the current profile exactly."""
    return (
        isinstance(value, dict)
        and value.get("activity_profile_id") == profile_id
        and value.get("activity_profile_version") == profile_version
    )


def _event_profile_matches(event: Event, profile_id: object, profile_version: object) -> bool:
    """Match profile identity in either an audit-start envelope or nested profile record."""
    nested = event.payload.get("activity_profile")
    nested_match = (
        isinstance(nested, dict)
        and nested.get("id") == profile_id
        and nested.get("version") == profile_version
    )
    return nested_match or (
        event.payload.get("activity_profile_id") == profile_id
        and event.payload.get("activity_profile_version") == profile_version
    )


def _cited_evidence_events(
    finding: AuditFinding, events: tuple[Event, ...], by_seq: dict[str, Event]
) -> tuple[dict[int, Event], str | None]:
    """Resolve every candidate reference to complete, same-Run source records."""
    evidence_index = RunEvidenceIndex.from_events(events)
    required: dict[int, Event] = {}
    for evidence in finding.evidence:
        if not evidence_index.backs(evidence):
            return {}, "A candidate evidence reference is unresolved or has a hash mismatch."
        if evidence.kind == "artifact":
            return {}, (
                "Artifact content is not available to the verifier; an artifact registration "
                "and digest alone cannot support a verdict."
            )
        sources = _events_for_evidence(evidence, events, by_seq)
        if not sources:
            return {}, "A candidate evidence reference has no complete source event."
        required.update((event.seq, event) for event in sources)
    return required, None


def _linked_approval_events(
    source_events: tuple[Event, ...], events: tuple[Event, ...]
) -> tuple[tuple[Event, ...], str | None]:
    """Select decision/request/resolution records linked to cited Gate approvals."""
    decision_ids = {
        decision_id for event in source_events if (decision_id := _decision_id(event)) is not None
    }
    related = tuple(
        event
        for event in events
        if event.type
        in {
            EventType.POLICY_DECISION,
            EventType.HUMAN_APPROVAL_REQUESTED,
            EventType.HUMAN_APPROVAL,
        }
        and (decision_id := _decision_id(event)) is not None
        and decision_id in decision_ids
    )
    for event in source_events:
        if event.type is EventType.HUMAN_APPROVAL:
            decision_id = approval_decision_id(event.payload)
            linked = tuple(
                candidate for candidate in related if _decision_id(candidate) == decision_id
            )
            if decision_id is None or not _has_gate_request_and_decision(linked):
                return (), "The cited approval is missing its linked policy decision or request."
    return related, None


def _has_gate_request_and_decision(events: tuple[Event, ...]) -> bool:
    """Return whether a cited approval has its linked request and policy decision."""
    return any(event.type is EventType.POLICY_DECISION for event in events) and any(
        event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in events
    )


def _fit_verification_projection(
    candidate: dict[str, object],
    events: tuple[Event, ...],
    required: dict[int, Event],
    eligible: dict[int, Event],
    *,
    audit_context: dict[str, object],
    priority_context_events: tuple[Event, ...],
    model_effect_events: tuple[Event, ...],
    current_profile_evidence: tuple[str, str] | None,
) -> _VerificationProjection:
    """Fit whole event records around an indispensable candidate within the exact budget."""
    projection: dict[str, object] = {
        "candidate_finding": candidate,
        "audit_context": audit_context,
        "audit_evidence_events": [],
        "omitted_records": {"audit_evidence_events": 0, "audit_context_events": 0},
        "artifact_content_visible": False,
    }
    if _render_projection(projection) is None:
        return _unavailable(
            "Candidate identity, proposition, and evidence references exceed the message budget."
        )

    visible: dict[int, Event] = {}
    for event in sorted(required.values(), key=lambda item: item.seq):
        visible[event.seq] = event
        projection["audit_evidence_events"] = [
            _event_projection(item)
            for item in sorted(visible.values(), key=lambda entry: entry.seq)
        ]
        if _render_projection(projection) is None:
            return _unavailable("A required cited record cannot fit as a complete JSON record.")

    model_effect_by_seq = {event.seq: event for event in model_effect_events}
    priority_by_seq = {event.seq: event for event in priority_context_events}
    optional = [
        *sorted(model_effect_by_seq.values(), key=lambda item: item.seq),
        *sorted(priority_by_seq.values(), key=lambda item: item.seq),
        *sorted(
            (
                event
                for seq, event in eligible.items()
                if seq not in visible
                and seq not in priority_by_seq
                and seq not in model_effect_by_seq
            ),
            key=lambda item: item.seq,
            reverse=True,
        ),
    ]
    for event in optional:
        if len(visible) >= _MAX_EVENTS:
            break
        trial = {**visible, event.seq: event}
        projection["audit_evidence_events"] = [
            _event_projection(item) for item in sorted(trial.values(), key=lambda entry: entry.seq)
        ]
        if _render_projection(projection) is not None:
            visible = trial

    _set_omitted_records(projection, events, visible, priority_by_seq)
    projection["audit_evidence_events"] = [
        _event_projection(item) for item in sorted(visible.values(), key=lambda entry: entry.seq)
    ]
    prompt = _render_projection(projection)
    while prompt is None and visible.keys() != required.keys():
        removable = next(
            (seq for seq in sorted(visible, reverse=True) if seq not in required), None
        )
        if removable is None:
            break
        visible.pop(removable)
        projection["audit_evidence_events"] = [
            _event_projection(item)
            for item in sorted(visible.values(), key=lambda entry: entry.seq)
        ]
        _set_omitted_records(projection, events, visible, priority_by_seq)
        prompt = _render_projection(projection)
    if prompt is None:
        return _unavailable("The complete redacted evidence frame cannot fit the message budget.")
    return _VerificationProjection(
        prompt=prompt,
        visible_event_hashes=frozenset(
            (f"seq:{event.seq}", event.hash) for event in visible.values() if event.hash
        ),
        current_profile_evidence=current_profile_evidence,
        model_effect_evidence_complete=model_effect_by_seq.keys() <= visible.keys(),
    )


def _set_omitted_records(
    projection: dict[str, object],
    events: tuple[Event, ...],
    visible: dict[int, Event],
    priority_context_events: dict[int, Event],
) -> None:
    """Report honest total and governance-context omission counts after whole-record fitting."""
    projection["omitted_records"] = {
        "audit_evidence_events": len(events) - len(visible),
        "audit_context_events": sum(seq not in visible for seq in priority_context_events),
    }


def _ground_applicability(
    verdict: FindingVerdict, projection: _VerificationProjection
) -> FindingVerdict:
    """Accept known scope judgments only when their complete event grounding was visible."""
    judgment = verdict.applicability
    if judgment.status == "undetermined":
        if judgment.rationale.strip():
            return verdict
        return verdict.model_copy(
            update={
                "applicability": ApplicabilityJudgment(
                    status="undetermined",
                    rationale="No grounded applicability judgment was supplied.",
                )
            }
        )

    reason: str | None = None
    if projection.current_profile_evidence is None:
        reason = "the exact current activity profile is not available"
    elif judgment.status == "not_applicable" and not projection.model_effect_evidence_complete:
        reason = "one or more typed model-training effects were omitted from the evidence window"
    elif not judgment.rationale.strip():
        reason = "the judgment has no rationale"
    elif not judgment.concrete_action.strip():
        reason = "the judgment does not name a concrete evidenced action"
    elif not judgment.evidence:
        reason = "the judgment has no event evidence"
    else:
        refs: set[tuple[str, str]] = set()
        for evidence in judgment.evidence:
            key = (evidence.ref, evidence.sha256 or "")
            if evidence.kind != "event" or key not in projection.visible_event_hashes:
                reason = "the judgment cites an absent, stale, or non-event reference"
                break
            refs.add(key)
        if reason is None and projection.current_profile_evidence not in refs:
            reason = "the judgment is not grounded in the exact current activity-profile event"
    if reason is None:
        return verdict
    return verdict.model_copy(
        update={
            "applicability": ApplicabilityJudgment(
                status="undetermined",
                rationale=f"Applicability was left undetermined because {reason}.",
            )
        }
    )


def _unavailable(reason: str) -> _VerificationProjection:
    """Return a code-owned unavailable result without constructing a partial prompt."""
    return _VerificationProjection(prompt=None, unavailable_rationale=reason)


def _events_for_evidence(
    evidence: Evidence, events: tuple[Event, ...], by_seq: dict[str, Event]
) -> tuple[Event, ...]:
    """Return the complete source event records needed to interpret one backed reference."""
    if evidence.kind == "event":
        event = by_seq.get(evidence.ref)
        return () if event is None else (event,)
    if evidence.kind == "artifact":
        return tuple(
            event
            for event in events
            if event.type is EventType.ARTIFACT_CREATED
            and evidence.ref
            in {
                event.payload.get("name"),
                event.payload.get("artifact_id"),
                event.payload.get("id"),
                event.subject_id,
            }
        )
    if evidence.kind == "experiment":
        return tuple(
            event
            for event in events
            if event.type in {EventType.EXPERIMENT_STARTED, EventType.EXPERIMENT_COMPLETED}
            and evidence.ref in _experiment_event_ids(event)
        )
    if evidence.kind == "tool_call":
        return tuple(
            event
            for event in events
            if event.type
            in {EventType.TOOL_STARTED, EventType.TOOL_COMPLETED, EventType.TOOL_DENIED}
            and evidence.ref
            in {
                event.payload.get("tool_call_id"),
                event.payload.get("id"),
                event.subject_id,
            }
        )
    if evidence.kind == "external":
        matches: list[Event] = []
        for event in events:
            result, _reason = regulation_search_result(event)
            if result is not None and any(
                f"{match.source_id}/{match.location}" == evidence.ref
                and match.sha256 == evidence.sha256
                for match in result.matches
            ):
                matches.append(event)
        return tuple(matches)
    return ()


def _decision_id(event: Event) -> str | None:
    """Return the exact policy-decision identity linked to a policy, Gate, or tool event."""
    if event.type is EventType.POLICY_DECISION:
        decision = event.payload.get("decision")
        nested = decision.get("id") if isinstance(decision, dict) else None
        return _first_identifier(
            event.payload.get("id"), nested, approval_decision_id(event.payload)
        )
    if event.type in {EventType.HUMAN_APPROVAL_REQUESTED, EventType.HUMAN_APPROVAL}:
        return approval_decision_id(event.payload)
    if event.type not in {
        EventType.TOOL_STARTED,
        EventType.TOOL_DENIED,
        EventType.TOOL_COMPLETED,
    }:
        return None
    tool_call = event.payload.get("tool_call")
    nested = tool_call.get("policy_decision_id") if isinstance(tool_call, dict) else None
    return _first_identifier(
        event.payload.get("decision_id"), event.payload.get("policy_decision_id"), nested
    )


def _first_identifier(*values: object) -> str | None:
    """Return the first non-empty string from linked decision identity fields."""
    return next((value for value in values if isinstance(value, str) and value), None)


def _event_projection(event: Event) -> dict[str, object]:
    """Project the complete, hash-pinned event envelope and payload."""
    return event.model_dump(mode="json")


def _experiment_event_ids(event: Event) -> frozenset[str]:
    """Return direct, nested and envelope-owned experiment identifiers for one event."""
    identifiers = {
        value
        for value in (
            event.payload.get("experiment_id"),
            event.payload.get("id"),
            event.subject_id,
        )
        if isinstance(value, str) and value
    }
    experiment = event.payload.get("experiment")
    if isinstance(experiment, dict):
        nested = experiment.get("id")
        if isinstance(nested, str) and nested:
            identifiers.add(nested)
    return frozenset(identifiers)


def _render_projection(projection: dict[str, object]) -> str | None:
    """Redact, scrub and size exact serialized JSON before applying the no-cap frame."""
    rendered = canonical_json(redact_value(scrub_credentials_value(projection)))
    escaped = rendered.replace("<", r"\u003c").replace(">", r"\u003e")
    if len(frame_overhead(_VERIFICATION_FRAME)) + len(escaped) > _MAX_PROJECTION_CHARS:
        return None
    return frame_untrusted(rendered, label=_VERIFICATION_FRAME)


__all__ = ["build_finding_verifier"]
