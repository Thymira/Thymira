"""Reproduce historical filenames being promoted to new-run output obligations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from thymira.agents.llm.scripted import ScriptedProvider
from thymira.events import InMemoryEventLog
from thymira.policies import ActionRule, Gate, Policy, PolicyEngine, auto_reject
from thymira.schemas import (
    ArtifactKind,
    ConversationInput,
    Decision,
    EventType,
    ModelRoutePolicy,
    Run,
    new_id,
)
from thymira.state import LocalArtifactStore
from thymira.thy.agents import full_agent_catalog
from thymira.thy.agents.data import DataProfile
from thymira.thy.graph import run_thy
from thymira.thy.models import (
    AgentTask,
    ArtifactRequirement,
    PlanOutput,
    ThyAgentKind,
    ThyInput,
    ThyPhase,
    ThyState,
)
from thymira.thy.nodes.plan import plan_node

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("agent_kind", "current_request", "historical_path", "artifact_kind"),
    [
        (
            ThyAgentKind.DATA,
            "Can you do more with the dataset?",
            "profile/old.json",
            ArtifactKind.REPORT,
        ),
        (ThyAgentKind.CODING, "Can you do more with the dataset?", "old.py", ArtifactKind.CODE),
        (
            ThyAgentKind.STATISTICS,
            "Can you do more with the dataset?",
            "old_stats.json",
            ArtifactKind.REPORT,
        ),
        (
            ThyAgentKind.DATA_QUALITY,
            "Can you do more with the dataset?",
            "old_quality.json",
            ArtifactKind.REPORT,
        ),
        (
            ThyAgentKind.VISUALIZATION,
            "Can you do more with the dataset?",
            "old_plot.png",
            ArtifactKind.PLOT,
        ),
        (
            ThyAgentKind.EXPERIMENT,
            "Train a new model on the dataset.",
            "old_model.joblib",
            ArtifactKind.MODEL,
        ),
        (
            ThyAgentKind.ML,
            "Tune the model with a hyperparameter search.",
            "old_model.joblib",
            ArtifactKind.MODEL,
        ),
    ],
)
def test_plan_does_not_accept_historical_output_as_a_new_obligation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent_kind: ThyAgentKind,
    current_request: str,
    historical_path: str,
    artifact_kind: ArtifactKind,
) -> None:
    """A plausible contaminated model proposal must be rejected or lose its old-only output."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    previous_run_id = new_id("run")
    previous_store = LocalArtifactStore(tmp_path / "artifacts" / previous_run_id, previous_run_id)
    previous_store.save_bytes(
        historical_path,
        b"published by the earlier run",
        produced_by=new_id("agent"),
        kind=artifact_kind,
    )
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=(
            "Previous conversation context:\n"
            f"Run ID: {previous_run_id}\nAssistant summary: Published {historical_path}.\n\n"
            f"Current user request:\n{current_request}"
        ),
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="historical-output-regression",
            message=current_request,
            context_run_ids=(previous_run_id,),
        ),
    )
    current_store = LocalArtifactStore(tmp_path / "artifacts" / run.id, run.id)
    proposed = AgentTask(
        id="follow-up",
        agent=agent_kind,
        phase=ThyPhase.EXECUTE,
        instruction="Perform the current requested analysis.",
        required_artifacts=(ArtifactRequirement(name=historical_path, kind=artifact_kind),),
    )
    log = InMemoryEventLog(run.id)
    policy = Policy(
        name="test-plan-pass",
        version="1.0",
        action_rules=(
            ActionRule(
                id="TEST-PLAN",
                action_types=("plan.proposed",),
                decision=Decision.PASS,
                reason="Allow the plan seam under test.",
            ),
        ),
    )
    node = plan_node(
        full_agent_catalog(),
        Gate(PolicyEngine(policy), log, approver=auto_reject),
        log,
        provider=ScriptedProvider(
            [
                PlanOutput(tasks=(proposed,)),
                {
                    "tasks": [
                        {
                            "id": proposed.id,
                            "agent": agent_kind.value,
                            "phase": ThyPhase.EXECUTE.value,
                            "instruction": proposed.instruction,
                            "outputs_to_publish": [],
                            "artifact_references": [
                                {"name": historical_path, "source_run_id": previous_run_id}
                            ],
                        }
                    ]
                },
            ]
        ),
        route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests"),
    )

    result = ThyState.model_validate(node(ThyState(run=run, phase=ThyPhase.PLAN)))

    assert result.error is None
    assert all(
        requirement.name != historical_path
        for task in result.plan
        for requirement in task.required_artifacts
    ), "A filename mentioned only in prior-run context became a mandatory new output"
    assert len(result.plan) == 1
    assert historical_path in result.plan[0].instruction
    assert previous_run_id in result.plan[0].instruction
    assert "references only" in result.plan[0].instruction
    assert previous_store.get(historical_path) is not None
    assert previous_store.verify() == []
    assert current_store.list_active() == []
    assert current_store.verify() == []


def test_plan_keeps_a_same_named_output_when_the_current_request_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Historical equality does not erase a deliverable explicitly requested again."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    previous_run_id = new_id("run")
    path = "reports/old_summary.md"
    previous_store = LocalArtifactStore(tmp_path / "artifacts" / previous_run_id, previous_run_id)
    previous_store.save_bytes(path, b"earlier result", produced_by=new_id("agent"))
    current_message = f"Please create {path} again, using the previous report only as context."
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=f"Earlier assistant summary: published {path}.\nCurrent request: {current_message}",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="same-name-output",
            message=current_message,
            context_run_ids=(previous_run_id,),
        ),
    )
    log = InMemoryEventLog(run.id)
    plan = {
        "tasks": [
            {
                "id": "report",
                "agent": ThyAgentKind.CODING.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": "Create the requested report.",
                "outputs_to_publish": [
                    {
                        "name": path,
                        "kind": ArtifactKind.REPORT.value,
                    }
                ],
                "artifact_references": [],
            }
        ]
    }
    result = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(_plan_policy()), log, approver=auto_reject),
            log,
            provider=ScriptedProvider([plan]),
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )

    assert result.error is None
    assert tuple(item.name for item in result.plan[0].required_artifacts) == (path,)
    assert previous_store.get(path) is not None


def test_contextual_retry_compiles_a_planner_chosen_output_without_self_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrected structural declaration becomes a strict runtime output requirement."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    current_message = "Make a fresh plot of the data."
    previous_run_id = new_id("run")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=f"Earlier summary: published old_plot.png.\nCurrent request: {current_message}",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="task-derived-output",
            message=current_message,
            context_run_ids=(previous_run_id,),
        ),
    )
    invalid = {
        "tasks": [
            {
                "id": "plot",
                "agent": ThyAgentKind.VISUALIZATION.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": "Create a plot of the data.",
                "required_artifacts": [
                    {
                        "name": "plots/fresh.png",
                        "kind": ArtifactKind.PLOT.value,
                        "basis": "current_request",
                        "evidence": current_message,
                    }
                ],
                "artifact_references": [],
            }
        ]
    }
    corrected = {
        "tasks": [
            {
                "id": "plot",
                "agent": ThyAgentKind.VISUALIZATION.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": "Create and publish the chart at plots/fresh.png.",
                "outputs_to_publish": [
                    {
                        "name": "plots/fresh.png",
                        "kind": ArtifactKind.PLOT.value,
                    }
                ],
                "artifact_references": [],
            }
        ]
    }
    log = InMemoryEventLog(run.id)
    provider = ScriptedProvider([invalid, corrected])
    result = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(_plan_policy()), log, approver=auto_reject),
            log,
            provider=provider,
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )

    assert result.error is None
    assert tuple(item.name for item in result.plan[0].required_artifacts) == ("plots/fresh.png",)
    assert len(provider.calls) == 2


def test_contextual_plan_prompt_explains_profile_outputs_and_conditional_correlation_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A follow-up plan knows the real generic profile outputs before proposing a path."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    current_message = "Can you do more with the dataset?"
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=f"Earlier run published profile/credit.json. Current request: {current_message}",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="profile-path-guidance",
            message=current_message,
            context_run_ids=(new_id("run"),),
        ),
    )
    task = AgentTask(
        id="profile",
        agent=ThyAgentKind.DATA,
        phase=ThyPhase.EXECUTE,
        instruction="Profile the registered dataset.",
    )
    log = InMemoryEventLog(run.id)
    provider = ScriptedProvider([PlanOutput(tasks=(task,))])
    plan_node(
        full_agent_catalog(),
        Gate(PolicyEngine(_plan_policy()), log, approver=auto_reject),
        log,
        provider=provider,
        route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests"),
    )(ThyState(run=run, phase=ThyPhase.PLAN))

    instructions = provider.calls[0]["system"]
    assert "profile/{dataset}.json" in instructions
    assert "profile/{dataset}-distributions.png" in instructions
    assert "profile/{dataset}-correlation.png" in instructions
    assert "at least 2 numeric columns" in instructions
    assert "outputs_to_publish" in instructions
    assert "required_artifacts" in instructions
    assert "task_derived" not in instructions
    assert "historical artifacts" in instructions.lower()
    assert "artifact_references" in instructions


def test_contextual_plan_retry_repeats_rejected_output_and_actionable_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one retry gets enough detail to repair provenance without dropping the output."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    current_message = "Can you do more with the dataset?"
    previous_run_id = new_id("run")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=f"Earlier run published profile/credit.json. Current request: {current_message}",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="self-contained-plan-retry",
            message=current_message,
            context_run_ids=(previous_run_id,),
        ),
    )
    invalid = {
        "tasks": [
            {
                "id": "profile",
                "agent": ThyAgentKind.DATA.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": "Call profile_dataset(include_plots=true).",
                "required_artifacts": [
                    {
                        "name": "profile/credit.png",
                        "kind": ArtifactKind.PLOT.value,
                        "basis": "current_request",
                        "evidence": current_message,
                    }
                ],
                "artifact_references": [
                    {"name": "profile/credit.json", "source_run_id": previous_run_id}
                ],
            }
        ]
    }
    corrected_instruction = (
        "Call profile_dataset(include_plots=true) and publish the profile at profile/credit.json."
    )
    corrected = {
        "tasks": [
            {
                "id": "profile",
                "agent": ThyAgentKind.DATA.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": corrected_instruction,
                "outputs_to_publish": [
                    {
                        "name": "profile/credit.json",
                        "kind": ArtifactKind.REPORT.value,
                    }
                ],
                "artifact_references": [
                    {"name": "profile/credit.json", "source_run_id": previous_run_id}
                ],
            }
        ]
    }
    log = InMemoryEventLog(run.id)
    provider = ScriptedProvider([invalid, corrected])
    result = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(_plan_policy()), log, approver=auto_reject),
            log,
            provider=provider,
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )

    assert result.error is None
    assert tuple(item.name for item in result.plan[0].required_artifacts) == (
        "profile/credit.json",
    )
    assert previous_run_id in result.plan[0].instruction
    retry = provider.calls[1]["prompt"]
    for rejected_detail in (
        "profile",
        "Call profile_dataset(include_plots=true).",
        "profile/credit.png",
        "current_request",
        current_message,
    ):
        assert rejected_detail in retry
    assert "outputs_to_publish" in retry
    assert "required_artifacts is runtime-owned" in retry
    assert "preserve" in retry.lower()


def test_plan_accepts_a_path_quoted_in_a_clarification_with_windows_separators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clarification evidence stays exact while path matching is host-independent."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    clarification = r"Please publish reports\fresh.md in this run."
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Historical context: a prior run wrote reports/fresh.md.\nCurrent: yes, proceed.",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="clarified-output",
            message="Yes, proceed.",
            context_run_ids=(new_id("run"),),
            clarification_context=(clarification,),
        ),
    )
    path = "reports/fresh.md"
    proposal = {
        "tasks": [
            {
                "id": "report",
                "agent": ThyAgentKind.CODING.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": "Create the clarified report.",
                "outputs_to_publish": [
                    {
                        "name": path,
                        "kind": ArtifactKind.REPORT.value,
                    }
                ],
                "artifact_references": [],
            }
        ]
    }
    log = InMemoryEventLog(run.id)
    result = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(_plan_policy()), log, approver=auto_reject),
            log,
            provider=ScriptedProvider([proposal]),
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )

    assert result.error is None
    assert tuple(item.name for item in result.plan[0].required_artifacts) == (path,)


def test_plan_stops_before_gate_when_contextual_output_origin_stays_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One correction is bounded; a second origin failure leaves no executable plan."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="History says old.md was published. Current request: make a fresh report.",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="persistent-invalid-origin",
            message="Make a fresh report.",
            context_run_ids=(new_id("run"),),
        ),
    )
    invalid = {
        "tasks": [
            {
                "id": "report",
                "agent": ThyAgentKind.CODING.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": "Create the report.",
                "required_artifacts": [
                    {"name": "reports/fresh.md", "kind": ArtifactKind.REPORT.value}
                ],
                "artifact_references": [],
            }
        ]
    }
    log = InMemoryEventLog(run.id)
    provider = ScriptedProvider([invalid, invalid])
    result = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(_plan_policy()), log, approver=auto_reject),
            log,
            provider=provider,
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )

    assert result.error == (
        "plan rejected: contextual outputs or references remained invalid after one correction"
    )
    assert result.plan == ()
    assert len(provider.calls) == 2
    assert not any(event.type is EventType.POLICY_DECISION for event in log.events())


def test_task_derived_output_still_fails_when_execute_does_not_publish_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning provenance never weakens Execute's normal required-file check."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")
    monkeypatch.setenv("THYMIRA_MODEL_STANDARD", "test-model")
    current_message = "Make a fresh report."
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=f"Earlier run published old.md. Current request: {current_message}",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="task-derived-output-remains-required",
            message=current_message,
            context_run_ids=(new_id("run"),),
        ),
    )
    path = "reports/fresh.md"
    instruction = f"Create and publish the report at {path}."
    proposal = {
        "tasks": [
            {
                "id": "report",
                "agent": ThyAgentKind.DATA.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": instruction,
                "outputs_to_publish": [
                    {
                        "name": path,
                        "kind": ArtifactKind.REPORT.value,
                    }
                ],
                "artifact_references": [],
            }
        ]
    }
    log = InMemoryEventLog(run.id)
    planned = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(_plan_policy()), log, approver=auto_reject),
            log,
            provider=ScriptedProvider([proposal]),
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )
    output = run_thy(
        ThyInput(run=run),
        full_agent_catalog(),
        log,
        provider=ScriptedProvider(
            [DataProfile(columns=("value",), dtypes={"value": "Int64"}, missing={"value": 0})]
        ),
        route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests"),
        initial_state=ThyState(run=run, plan=planned.plan),
    )

    assert planned.error is None
    assert tuple(item.name for item in planned.plan[0].required_artifacts) == (path,)
    assert output.error is not None
    assert "required artifact 'reports/fresh.md' is missing or invalid" in output.error
    assert output.missing_artifacts == (path,)


def _plan_policy() -> Policy:
    return Policy(
        name="test-plan-pass",
        version="1.0",
        action_rules=(
            ActionRule(
                id="TEST-PLAN",
                action_types=("plan.proposed",),
                decision=Decision.PASS,
                reason="Allow the plan seam under test.",
            ),
        ),
    )
