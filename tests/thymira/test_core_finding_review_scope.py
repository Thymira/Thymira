"""MIRA action attribution for bounded findings reviews."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from thymira.agents.llm import LLMToolCall, ScriptedProvider
from thymira.core.finding_review import _action_evidence
from thymira.core.graph.protocol import SubgraphDeps
from thymira.core.usage import UsageLedger
from thymira.events import InMemoryEventLog
from thymira.mira import AuditInput
from thymira.mira.agents.runner import AuditAgentContext, run_audit_agent
from thymira.mira.agents.spec import AuditAgentSpec
from thymira.mira.checks import AuditReport
from thymira.mira.kb import LocalRegulationStore, RegulationChunk
from thymira.mira.kb.ingest import write_jsonl
from thymira.mira.tools import build_mira_tool_registry
from thymira.policies import Gate, PolicyEngine, RiskProfile, ToolCapability, load_policy_stack
from thymira.schemas import Actor, EventType, Framework, ModelRoutePolicy, Run, new_id
from thymira.state import LocalArtifactStore, LocalRecordRepository
from thymira.tools.builtins.run_python import builtins_registry
from thymira.tools.datasets import register_dataset
from thymira.tools.manager import ToolManager
from thymira.tools.models import ToolContext

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from thymira.schemas import Event
    from thymira.tools.registry import ToolRegistry


class _ScopeFixture(NamedTuple):
    deps: SubgraphDeps
    events: tuple[Event, ...]
    lookup: Event
    agent_id: str


def _scope_fixture(tmp_path: Path, route_policy: ModelRoutePolicy) -> _ScopeFixture:
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Profile the registered dataset and audit its evidence.",
        model_route_policy=route_policy,
    )
    log = InMemoryEventLog(run.id)
    gate = Gate(PolicyEngine(load_policy_stack()), log)
    artifact_store = LocalArtifactStore(tmp_path / "artifacts", run.id)
    regulation_chunk = RegulationChunk.from_text(
        source_id="internal-methodology",
        version="1",
        framework=Framework.EU_AI_ACT,
        location="Article 14",
        text="Human oversight must enable intervention and override.",
    )
    regulation_path = tmp_path / "regulation.jsonl"
    write_jsonl((regulation_chunk,), regulation_path)
    audit_registry = build_mira_tool_registry(LocalRegulationStore(regulation_path))
    tool_manager = ToolManager(builtins_registry())
    deps = SubgraphDeps(
        event_log=log,
        gate=gate,
        artifact_store=artifact_store,
        tool_manager=tool_manager,
        record_repository=LocalRecordRepository(tmp_path / "records"),
        usage_ledger=UsageLedger(),
        audit_tool_capabilities=audit_registry.capabilities(),
    )
    _record_profile_action(tmp_path, run.id, log, gate, artifact_store, tool_manager)
    agent_id = _run_mira_lookup(
        tmp_path, run, route_policy, log, gate, artifact_store, audit_registry
    )
    events = tuple(log.events())
    lookup = next(
        event
        for event in events
        if event.type is EventType.TOOL_COMPLETED
        and event.payload.get("tool") == "search_regulation"
    )
    return _ScopeFixture(deps, events, lookup, agent_id)


def _record_profile_action(
    tmp_path: Path,
    run_id: str,
    log: InMemoryEventLog,
    gate: Gate,
    artifact_store: LocalArtifactStore,
    tool_manager: ToolManager,
) -> None:
    dataset_path = tmp_path / "sample.csv"
    dataset_path.write_text(
        "applicant,score\na,0.1\nb,0.5\nc,0.9\n", encoding="utf-8", newline="\n"
    )
    agent_id = new_id("agent")
    register_dataset(artifact_store, dataset_path, "sample", produced_by=agent_id)
    tool_manager.execute(
        ToolContext(
            run_id=run_id,
            agent_id=agent_id,
            task_id=new_id("task"),
            workspace=tmp_path,
            event_log=log,
            gate=gate,
            artifact_store=artifact_store,
            risk_profile=RiskProfile(
                risk_level="limited",
                activity_category="data_analysis",
                confidence=1.0,
            ),
        ),
        "profile_dataset",
        {"dataset": "sample", "description": "Describe local correlations"},
    )


def _run_mira_lookup(
    tmp_path: Path,
    run: Run,
    route_policy: ModelRoutePolicy,
    log: InMemoryEventLog,
    gate: Gate,
    artifact_store: LocalArtifactStore,
    audit_registry: ToolRegistry,
) -> str:
    agent_id = new_id("agent")
    task_id = new_id("task")
    context = AuditAgentContext(
        route_policy=route_policy,
        event_log=log,
        actor=Actor.system(),
        agent_id=agent_id,
        task_id=task_id,
        provider=ScriptedProvider(
            [
                LLMToolCall(
                    id="call-search",
                    name="search_regulation",
                    arguments={"query": "human oversight", "framework": "EU_AI_ACT", "k": 1},
                ),
                LLMToolCall(
                    id="call-output",
                    name="final_result",
                    arguments={"findings": []},
                ),
            ]
        ),
        tool_registry=audit_registry,
        tool_context=ToolContext(
            run_id=run.id,
            agent_id=agent_id,
            task_id=task_id,
            workspace=tmp_path,
            event_log=log,
            gate=gate,
            artifact_store=artifact_store,
            risk_profile=RiskProfile(
                risk_level="limited",
                activity_category="audit",
                confidence=1.0,
            ),
        ),
    )
    spec = AuditAgentSpec(
        name="scope-audit",
        framework=Framework.EU_AI_ACT,
        task_kinds=("audit_judgement",),
        tool_allowlist=("search_regulation",),
        max_turns=2,
        system_prompt="Look up one regulation and return no findings.",
    )
    report = AuditReport(run_id=run.id, status="passed", controls=(), findings=())
    run_audit_agent(spec, AuditInput.from_run(tuple(log.events()), report), context)
    return agent_id


def _assert_scope_guards(fixture: _ScopeFixture) -> None:
    deps, events, lookup, agent_id = fixture
    assert not _action_evidence(replace(deps, audit_tool_capabilities=()), events).all_safe
    assert not _action_evidence(
        deps,
        tuple(
            event
            for event in events
            if not (
                event.type is EventType.AGENT_STARTED and event.payload.get("agent_id") == agent_id
            )
        ),
    ).all_safe
    assert not _action_evidence(
        deps,
        tuple(
            event
            for event in events
            if not (
                event.type is EventType.TOOL_STARTED
                and event.payload.get("tool_call_id") == lookup.payload["tool_call_id"]
            )
        ),
    ).all_safe
    assert not _action_evidence(
        deps,
        tuple(
            event
            for event in events
            if not (
                event.type is EventType.TOOL_COMPLETED
                and event.payload.get("tool") == "profile_dataset"
            )
        ),
    ).all_safe
    unsafe_capability = ToolCapability(
        id="search_regulation",
        data_access=("regulation_store",),
        side_effects=("workspace_write",),
    )
    assert not _action_evidence(
        replace(deps, audit_tool_capabilities=(unsafe_capability,)), events
    ).all_safe
    wrong_actor = lookup.model_copy(update={"actor": Actor.system()})
    wrong_actor_events = tuple(wrong_actor if event == lookup else event for event in events)
    assert not _action_evidence(deps, wrong_actor_events).all_safe
    wrong_task_payload = {**lookup.payload, "task_id": new_id("task")}
    wrong_task = lookup.model_copy(update={"payload": wrong_task_payload})
    wrong_task_events = tuple(wrong_task if event == lookup else event for event in events)
    assert not _action_evidence(deps, wrong_task_events).all_safe
    model_work = events[0].model_copy(update={"type": EventType.MODEL_TRAINED})
    assert _action_evidence(deps, (*events, model_work)).model_work_present


def test_action_evidence_accepts_authenticated_mira_regulation_lookup_with_profile_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain safe-profile scope when real MIRA emits an authenticated read-only lookup."""
    for variable in (
        "THYMIRA_MODEL_FRONTIER",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FAST",
    ):
        monkeypatch.setenv(variable, "test-model")
    route_policy = ModelRoutePolicy.from_routes(("test-model",), authority="scope-test")
    fixture = _scope_fixture(tmp_path, route_policy)
    deps, events, lookup, agent_id = fixture

    assert lookup.actor.authenticated is True
    assert lookup.actor.id == "search_regulation"
    assert lookup.payload["status"] == "COMPLETED"
    assert any(
        event.type is EventType.AGENT_STARTED
        and event.producer == "thymira.mira"
        and event.payload.get("agent_id") == agent_id
        for event in events
    )
    assert any(
        event.type is EventType.AGENT_COMPLETED
        and event.producer == "thymira.mira"
        and event.payload.get("agent_id") == agent_id
        and event.payload.get("status") == "COMPLETED"
        for event in events
    )
    evidence = _action_evidence(deps, events)
    assert len(evidence.events) == 2
    assert evidence.all_safe is True
    assert {capability.id for capability in evidence.capabilities} == {
        "profile_dataset",
        "search_regulation",
    }
    lookup_capability = next(
        capability for capability in evidence.capabilities if capability.id == "search_regulation"
    )
    assert lookup_capability.data_access == ("regulation_store",)
    assert lookup_capability.side_effects == ()
    assert lookup_capability.external_effects == ()
    assert {event.hash for event in evidence.events} == {
        event.hash for event in events if event.type is EventType.TOOL_COMPLETED
    }
    _assert_scope_guards(fixture)
