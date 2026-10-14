"""Plan-proposal approval across the production API composition and durable Run stores."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from tests.thymira.api_support import TEST_CREDENTIAL, authenticated_client
from tests.thymira.fixtures_agent_output import DataProfileOutput
from thymira.agents import RiskClassification, load_agent_specs
from thymira.agents.llm.scripted import ScriptedProvider
from thymira.api import build_default_deps, create_app
from thymira.core import build_runtime_graph_factory, default_activity_profile
from thymira.core.execution_review import PLAN_RESUME_TARGET
from thymira.core.graph.adapters import _recover_plan_review
from thymira.core.graph.state import initial_runtime_state
from thymira.core.plan_review import (
    PlanReviewError,
    plan_review_execution_context,
    plan_review_from_events,
)
from thymira.core.worker import _approved_resume_target, _OwnerControlConsumer
from thymira.events import verify_events
from thymira.mira.preflight import RiskJudgment
from thymira.schemas import (
    ActivityProfile,
    Actor,
    ActorKind,
    ControlInputKind,
    Decision,
    EventType,
    ModelRoutePolicy,
    PolicyDecision,
    RiskLevel,
    Run,
)
from thymira.thy.models import AgentTask, PlanOutput, ReworkSignal, ThyAgentKind, ThyPhase

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient
    from langgraph.graph.state import CompiledStateGraph

    from thymira.api.deps import RuntimeDeps
    from thymira.schemas import ControlInput, Event
    from thymira.state import RunHandle

pytestmark = pytest.mark.integration

_TEST_ROUTE_POLICY = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")


@pytest.fixture(autouse=True)
def _configured_test_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    config = workspace / ".thymira" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "project:\n  name: plan-review-test\n  domain: research\n",
        encoding="utf-8",
        newline="\n",
    )
    return workspace


def _catalog(directory: Path):
    directory.mkdir()
    (directory / "data.yaml").write_text(
        "name: data\nrole: agent\ntask_kinds: [analyze]\nmax_turns: 1\nmax_depth: 1\n"
        "system_prompt_ref: data.md\n"
        "output_schema_ref: tests.thymira.fixtures_agent_output:DataProfileOutput\n",
        encoding="utf-8",
        newline="\n",
    )
    (directory / "data.md").write_text("Describe the supplied dataset.", encoding="utf-8")
    return load_agent_specs(directory, known_capabilities=frozenset())


def _complete_profile(run: Run) -> ActivityProfile:
    return default_activity_profile(run).model_copy(
        update={
            "purpose": "Describe a local research dataset.",
            "affected_population": "No person is directly affected by the report.",
            "decision_effect": "The report does not make or recommend decisions.",
            "autonomy": "Descriptive analysis only.",
            "human_oversight": "A researcher reviews the report before use.",
            "jurisdiction": "EU",
            "data_categories": ("synthetic_test_data",),
            "sensitive_attributes": (),
            "potential_consequences": ("A reader could misinterpret a summary statistic.",),
        }
    )


@pytest.mark.parametrize(
    ("approve_plan", "reject_plan", "stale_plan"),
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
)
def test_default_policy_parks_plan_review_after_execution_start(
    tmp_path: Path,
    approve_plan: bool,
    reject_plan: bool,
    stale_plan: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A human plan decision must park in PLANNING, not turn the Run into FAILED."""
    workspace = _workspace(tmp_path)
    catalog = _catalog(tmp_path / "catalog")
    provider = ScriptedProvider(
        [
            RiskJudgment(
                risk_level=RiskLevel.LOW,
                activity_category="descriptive_analysis",
                confidence=0.99,
                justification="The complete profile declares a descriptive local report.",
            ),
            RiskClassification(
                risk_level=RiskLevel.LOW,
                activity_category="descriptive_analysis",
                confidence=0.99,
                needs_human_review=False,
                summary="A bounded descriptive analysis with no decision effect.",
            ),
            PlanOutput(
                tasks=(
                    AgentTask(
                        id="profile-dataset",
                        agent=ThyAgentKind.DATA,
                        phase=ThyPhase.EXECUTE,
                        instruction="Describe the supplied dataset.",
                    ),
                )
            ),
            DataProfileOutput(row_count=1, columns=("income", "risk")),
        ]
    )
    dependency_ref = []

    def factory(run: Run) -> CompiledStateGraph:
        deps = dependency_ref[0]
        assert deps.project_resolution is not None
        return build_runtime_graph_factory(
            deps.run_store,
            gate_factory=deps.gate_factory,
            artifact_store_factory=deps.artifact_store_factory,
            tool_manager=deps.tool_manager,
            record_repository=deps.record_repository,
            checkpoint_repository=deps.checkpoint_repository,
            provider=provider,
            project_dir=workspace,
            project_config=deps.project_resolution.config,
            catalog=catalog,
            specs=(),
            activity_profile_factory=_complete_profile,
            risk_interview=deps.risk_interview,
            plan_repository=deps.board_repository,
            dag_repository=deps.dag_repository,
            route_policy=_TEST_ROUTE_POLICY,
        )(run)

    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        graph_factory=factory,
        principal_resolver=TEST_CREDENTIAL,
        model_route_policy=_TEST_ROUTE_POLICY,
    )
    dependency_ref.append(deps)
    assert deps.project_resolution is not None
    session = deps.session_service.create(
        project_id=deps.project_resolution.project_id, client="test"
    )
    run = deps.run_service.create_run(
        session.id, "Describe the dataset.", actor=Actor.system(), workspace=workspace
    )

    initial_events = deps.run_store.events(run.id)
    start_request = next(
        event for event in initial_events if event.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    start_decision_id = start_request.payload["decision_id"]
    start_decision = next(
        event
        for event in initial_events
        if event.type is EventType.POLICY_DECISION and event.payload.get("id") == start_decision_id
    )
    assert start_decision.payload["subject_kind"] == "run"
    assert start_decision.payload["decision"] == Decision.REQUIRE_HUMAN_REVIEW.value

    client = authenticated_client(create_app(deps))
    response = client.post(
        f"/runs/{run.id}/approve",
        json={"expected_decision_id": start_decision_id, "note": "Authorize execution start."},
    )
    assert response.status_code == 202, response.text

    events = deps.run_store.events(run.id)
    failure = next((event for event in events if event.type is EventType.RUN_FAILED), None)
    assert deps.run_service.get_run(run.id).status == "WAITING_FOR_APPROVAL", (
        failure.payload if failure is not None else events[-1].payload
    )
    run_state = deps.run_service.get_run_state(run.id)
    assert run_state.stage.value == "planning"
    assert run_state.condition.value == "waiting"
    plan_request = next(
        event
        for event in events
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
        and event.payload.get("action_type") == "plan.proposed"
    )
    assert plan_request.payload["plan"]["tasks"][0]["id"] == "profile-dataset"
    assert (
        plan_request.payload["plan"]["tasks"][0]["instruction"] == "Describe the supplied dataset."
    )
    assert not any(event.type is EventType.AGENT_STARTED for event in events)
    assert not any(event.type is EventType.RUN_FAILED for event in events)
    assert verify_events(events).valid
    assert _approved_resume_target(events, run.id) is None
    if stale_plan:
        _approve_stale_plan_without_resuming(client, deps, run, plan_request, monkeypatch)
        return
    if reject_plan:
        _reject_plan(client, deps, run, plan_request)
        return
    if approve_plan:
        _approve_plan_and_check_recovery(client, deps, run, plan_request)


def _approve_stale_plan_without_resuming(
    client: TestClient,
    deps: RuntimeDeps,
    run: Run,
    request: Event,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a stale Run scope before an owner control can resume the parked plan."""
    del client  # The Gate answer is recorded directly so the Run remains parked for the worker.
    decision_id = request.payload["decision_id"]
    decision = next(
        event.payload
        for event in deps.run_store.events(run.id)
        if event.type is EventType.POLICY_DECISION and event.payload.get("id") == decision_id
    )
    approval_actor = Actor(kind=ActorKind.HUMAN, id="test-approver", authenticated=True)
    gate = deps.gate_factory(
        run.id,
        approver=lambda _request: True,
        human=approval_actor,
    )
    assert gate.resolve_pending_approval(PolicyDecision.model_validate(decision)) is True

    changed_run = run.model_copy(update={"prompt": "Replace the task with model training."})
    monkeypatch.setattr(deps.lifecycle_repository, "get_run", lambda _run_id: changed_run)
    transitions: list[object] = []
    owner = SimpleNamespace(
        run_id=run.id,
        assert_live=lambda: None,
        append_transition=lambda *args, **kwargs: transitions.append((args, kwargs)),
    )
    command = SimpleNamespace(
        kind=ControlInputKind.RESUME,
        input_id="stale-plan-resume",
        payload={},
        authority=SimpleNamespace(actor=approval_actor),
    )
    consumer = _OwnerControlConsumer(deps.run_store, deps.lifecycle_repository)
    prior_resumes = [
        event.event_id
        for event in deps.run_store.events(run.id)
        if event.type is EventType.RUN_TRANSITIONED and event.payload.get("command") == "resume"
    ]

    with pytest.raises(PlanReviewError, match="scope changed"):
        # Narrow recording doubles expose the consumer boundary; no owner write may be reached.
        consumer.apply_control(cast("ControlInput", command), cast("RunHandle", owner))

    assert transitions == []
    assert deps.run_service.get_run_state(run.id).condition.value == "waiting"
    assert [
        event.event_id
        for event in deps.run_store.events(run.id)
        if event.type is EventType.RUN_TRANSITIONED and event.payload.get("command") == "resume"
    ] == prior_resumes


def _reject_plan(client: TestClient, deps: RuntimeDeps, run: Run, request: Event) -> None:
    """Reject the pending candidate without allowing Execute to start."""
    response = client.post(
        f"/runs/{run.id}/reject",
        json={"expected_decision_id": request.payload["decision_id"], "note": "Reject this plan."},
    )
    assert response.status_code == 202, response.text
    events = deps.run_store.events(run.id)
    assert deps.run_service.get_run(run.id).status == "BLOCKED"
    assert not any(event.type is EventType.AGENT_STARTED for event in events)
    assert not any(event.type is EventType.RUN_FAILED for event in events)
    assert verify_events(events).valid


def _approve_plan_and_check_recovery(
    client: TestClient, deps: RuntimeDeps, run: Run, request: Event
) -> None:
    """Approve the exact snapshot and verify recovery across an agent-start checkpoint lag."""
    response = client.post(
        f"/runs/{run.id}/approve",
        json={"expected_decision_id": request.payload["decision_id"], "note": "Approve this plan."},
    )
    assert response.status_code == 202, response.text
    events = deps.run_store.events(run.id)
    assert not any(event.type is EventType.RUN_FAILED for event in events)
    assert (
        sum(
            event.type is EventType.HUMAN_APPROVAL_REQUESTED
            and event.payload.get("action_type") == "plan.proposed"
            for event in events
        )
        == 1
    )
    assert sum(event.type is EventType.AGENT_STARTED for event in events) == 1
    assert verify_events(events).valid
    assert _approved_resume_target(events, run.id) == PLAN_RESUME_TARGET

    review = plan_review_from_events(events, run.id)
    assert review is not None
    execution_decision, execution_constraints = plan_review_execution_context(events, review)
    lagged_checkpoint = initial_runtime_state(run).model_copy(
        update={
            "execution_decision": execution_decision,
            "execution_constraints": execution_constraints,
        }
    )
    recovered, pending = _recover_plan_review(
        lagged_checkpoint,
        run,
        events,
        deps.gate_factory(run.id),
    )
    assert recovered == review
    assert not pending
    rework_checkpoint = lagged_checkpoint.model_copy(
        update={
            "rework_signal": ReworkSignal(
                decision=review.decision,
                from_phase="modeling",
                target_phase="preparation",
                justification="Revisit the preparation after the approved task ran.",
            )
        }
    )
    recovered, pending = _recover_plan_review(
        rework_checkpoint,
        run,
        events,
        deps.gate_factory(run.id),
    )
    assert recovered is None
    assert not pending
