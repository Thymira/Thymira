"""Contextual plans name promised outputs separately from historical inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from thymira.agents.llm.scripted import ScriptedProvider
from thymira.events import InMemoryEventLog
from thymira.policies import ActionRule, Gate, Policy, PolicyEngine, auto_reject
from thymira.schemas import (
    ConversationInput,
    Decision,
    EventType,
    ModelRoutePolicy,
    Run,
    new_id,
)
from thymira.thy.agents import full_agent_catalog
from thymira.thy.models import ThyPhase, ThyState
from thymira.thy.nodes.plan import _catalog_plan_output_type, plan_node
from thymira.thy.plan_review import plan_candidate_sha256, plan_candidate_snapshot


def _contextual_plan_result(
    monkeypatch: pytest.MonkeyPatch,
    proposal: dict[str, object],
    *,
    current_message: str,
    prompt: str,
    prior_run_id: str,
) -> tuple[ThyState, InMemoryEventLog, ScriptedProvider]:
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=prompt,
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="outputs-to-publish",
            message=current_message,
            context_run_ids=(prior_run_id,),
        ),
    )
    policy = Policy(
        name="test-plan-review",
        version="1.0",
        action_rules=(
            ActionRule(
                id="TEST-PLAN-REVIEW",
                action_types=("plan.proposed",),
                decision=Decision.REQUIRE_HUMAN_REVIEW,
                reason="Keep the candidate pending so its durable snapshot can be checked.",
            ),
        ),
    )
    log = InMemoryEventLog(run.id)
    provider = ScriptedProvider([proposal, proposal])
    result = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(policy), log, approver=auto_reject),
            log,
            provider=provider,
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )
    return result, log, provider


def test_contextual_outputs_compile_before_the_gated_plan_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three declared profile outputs become task requirements before Gate snapshots Plan."""
    monkeypatch.setenv("THYMIRA_MODEL_FRONTIER", "test-model")
    prior_run_id = new_id("run")
    current_message = "Repeat the earlier profile with plots."
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=f"Earlier run published profile/credit.json. Current: {current_message}",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="outputs-to-publish",
            message=current_message,
            context_run_ids=(prior_run_id,),
        ),
    )
    output_names = (
        "profile/credit.json",
        "profile/credit-distributions.png",
        "profile/credit-correlation.png",
    )
    proposal = {
        "tasks": [
            {
                "id": "profile",
                "agent": "data",
                "phase": "execute",
                "instruction": "Call profile_dataset(dataset=credit, include_plots=true).",
                "outputs_to_publish": [
                    {"name": name, "kind": kind}
                    for name, kind in zip(
                        output_names,
                        ("report", "plot", "plot"),
                        strict=True,
                    )
                ],
                "artifact_references": [
                    {"name": "profile/credit.json", "source_run_id": prior_run_id}
                ],
            }
        ]
    }
    policy = Policy(
        name="test-plan-review",
        version="1.0",
        action_rules=(
            ActionRule(
                id="TEST-PLAN-REVIEW",
                action_types=("plan.proposed",),
                decision=Decision.REQUIRE_HUMAN_REVIEW,
                reason="Keep the candidate pending so its durable snapshot can be checked.",
            ),
        ),
    )
    log = InMemoryEventLog(run.id)
    provider = ScriptedProvider([proposal, proposal])
    result = ThyState.model_validate(
        plan_node(
            full_agent_catalog(),
            Gate(PolicyEngine(policy), log, approver=auto_reject),
            log,
            provider=provider,
            route_policy=ModelRoutePolicy.from_routes(
                ("test-model",), authority="code-owned-tests"
            ),
        )(ThyState(run=run, phase=ThyPhase.PLAN))
    )

    pending = result.pending_plan_review
    assert pending is not None
    assert result.error is None
    assert result.plan == ()
    assert (
        tuple(item.name for item in pending.candidate.tasks[0].required_artifacts) == output_names
    )
    assert pending.candidate.tasks[0].instruction.startswith(
        "Call profile_dataset(dataset=credit, include_plots=true).\n\nFiles this task must publish:"
    )
    assert all(name in pending.candidate.tasks[0].instruction for name in output_names)
    request = next(
        event
        for event in log.events()
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
        and event.payload.get("decision_id") == pending.decision.id
    )
    assert request.payload["plan"] == plan_candidate_snapshot(pending.candidate)
    assert request.payload["plan_review"]["candidate_sha256"] == (
        plan_candidate_sha256(pending.candidate)
    )
    assert "Prior-run artifacts supplied as references only" in (
        pending.candidate.tasks[0].instruction
    )
    assert prior_run_id in pending.candidate.tasks[0].instruction


def test_contextual_input_only_plan_keeps_reference_without_output_obligations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_run_id = new_id("run")
    result, _, _ = _contextual_plan_result(
        monkeypatch,
        {
            "tasks": [
                {
                    "id": "inspect",
                    "agent": "data",
                    "phase": "execute",
                    "instruction": "Inspect the existing profile before answering.",
                    "outputs_to_publish": [],
                    "artifact_references": [
                        {"name": "profile/credit.json", "source_run_id": prior_run_id}
                    ],
                }
            ]
        },
        current_message="Inspect the existing profile before answering.",
        prompt="Earlier run profile/credit.json. Inspect the existing profile.",
        prior_run_id=prior_run_id,
    )

    pending = result.pending_plan_review
    assert pending is not None
    assert result.error is None
    assert pending.candidate.tasks[0].required_artifacts == ()
    assert "profile/credit.json" in pending.candidate.tasks[0].instruction
    assert "Prior-run artifacts supplied as references only" in (
        pending.candidate.tasks[0].instruction
    )


def test_contextual_plan_rejects_legacy_self_quoted_required_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_run_id = new_id("run")
    current_message = "Please create and publish profile/credit.json."
    result, log, provider = _contextual_plan_result(
        monkeypatch,
        {
            "tasks": [
                {
                    "id": "profile",
                    "agent": "data",
                    "phase": "execute",
                    "instruction": "Create and publish profile/credit.json.",
                    "required_artifacts": [
                        {
                            "name": "profile/credit.json",
                            "kind": "report",
                            "match": "exact",
                            "basis": "current_request",
                            "evidence": "create and publish profile/credit.json",
                        }
                    ],
                }
            ]
        },
        current_message=current_message,
        prompt="Earlier run had profile/credit.json.",
        prior_run_id=prior_run_id,
    )

    assert result.pending_plan_review is None
    assert result.error is not None
    assert not any(event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in log.events())
    retry_prompt = provider.calls[1]["prompt"]
    assert "legacy_required_artifacts" in retry_prompt
    assert "profile/credit.json" in retry_prompt
    assert "outputs_to_publish" in retry_prompt


def test_contextual_schema_exposes_outputs_but_hides_runtime_requirements() -> None:
    schema = _catalog_plan_output_type(("data", "coding"), contextual=True).model_json_schema()
    task_schema = next(
        item
        for item in schema["$defs"].values()
        if "outputs_to_publish" in item.get("properties", {})
    )
    properties = task_schema["properties"]
    assert "outputs_to_publish" in properties
    assert "required_artifacts" not in properties
    assert "basis" not in str(properties["outputs_to_publish"])
    assert "evidence" not in str(properties["outputs_to_publish"])


def test_invalid_outputs_after_the_single_correction_stop_before_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_run_id = new_id("run")
    result, log, _ = _contextual_plan_result(
        monkeypatch,
        {
            "tasks": [
                {
                    "id": "plot",
                    "agent": "coding",
                    "phase": "execute",
                    "instruction": "Create a plot.",
                    "outputs_to_publish": [{"kind": "plot"}],
                }
            ]
        },
        current_message="Create a plot.",
        prompt="Earlier run context.",
        prior_run_id=prior_run_id,
    )

    assert result.error == (
        "plan rejected: contextual outputs or references remained invalid after one correction"
    )
    assert result.pending_plan_review is None
    assert not any(event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in log.events())
