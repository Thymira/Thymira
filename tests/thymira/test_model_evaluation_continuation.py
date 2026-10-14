"""A local model survives a Session follow-up, gated evaluation and graph reconstruction."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import partial
from typing import cast

import pytest

from tests.thymira.fixtures_tools import development_policy, review_gated_context
from tests.thymira.test_api_sessions import _client, _complete, _send
from tests.thymira.test_evaluation_plan import _plan
from tests.thymira.test_session_discussions import _chat
from tests.thymira.test_thy_plan import _gate as _plan_gate
from tests.thymira.test_tools_model_evaluation import _context, _register_balanced_dataset
from thymira.agents.llm.base import LLMToolCall
from thymira.agents.llm.scripted import ScriptedProvider
from thymira.core.graph.adapters import default_thy_catalog
from thymira.core.model_continuity import materialize_model_inputs
from thymira.events import verify_events
from thymira.mira.checks import ControlStatus
from thymira.mira.checks.modelling import check_lineage_coherence
from thymira.mira.checks.report import check_report_fidelity
from thymira.policies import Gate, PolicyEngine, auto_approve
from thymira.schemas import (
    Actor,
    ActorKind,
    EventType,
    ModelRoutePolicy,
    SandboxMode,
    ToolCallStatus,
)
from thymira.state import LocalArtifactStore
from thymira.state.session_discussions import LocalDiscussionRepository
from thymira.thy.agents.evaluation import EvaluationResult
from thymira.thy.agents.experiment import ExperimentResult
from thymira.thy.graph import run_thy
from thymira.thy.models import (
    AgentTask,
    PlanOutput,
    SynthesisNarrative,
    ThyAgentKind,
    ThyInput,
    ThyPhase,
    ThyProgress,
    ThyState,
)
from thymira.tools import Tool, ToolManager, ToolRegistry
from thymira.tools.builtins import EvaluateModel, RunExperiment
from thymira.tools.builtins.run_python import builtins_registry
from thymira.tools.datasets import register_dataset
from thymira.tools.sandbox import LocalSubprocessSandbox

pytestmark = pytest.mark.integration


def _publish_session_evaluation(tmp_path, session_id, source_run_id, message):
    client, deps = _client(tmp_path)
    deps.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": "train_new",
                }
            )
        ],
        model="test-model",
    )
    response = _chat(client, session_id, message, key="evaluate", mode="auto")
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    assert run_id
    discussion = LocalDiscussionRepository(deps.session_chat.root, session_id).records()[-1]
    assert discussion.work_kind == "evaluate_existing"
    assert discussion.discussion.run_id == run_id
    published = deps.run_service.get_run(run_id)
    assert published.conversation is not None
    frozen_inputs = published.conversation.model_inputs
    assert frozen_inputs
    source_events = tuple(deps.event_store.read(source_run_id))
    return run_id, frozen_inputs, source_events


def _experiment_tool_result(log, _messages):
    completed = next(
        event for event in log.events() if event.type is EventType.EXPERIMENT_COMPLETED
    )
    experiment = completed.payload["experiment"]
    return ExperimentResult(
        parameters=experiment["parameters"],
        metrics=experiment["metrics"],
        seed=experiment["seed"],
        model_artifact_id=experiment["model_artifact_id"],
        tracker_run_id=experiment["tracker_run_id"],
    )


@pytest.mark.parametrize(
    "evaluator_message",
    [
        "Calculate precision, recall, F1, and ROC-AUC for the previous model",
        (
            "Without training a new model, evaluate the existing classifier and calculate "
            "precision, recall, F1, and ROC-AUC."
        ),
    ],
)
def test_prior_model_evaluation_survives_approval_and_reconstruction(
    tmp_path, monkeypatch, evaluator_message
):
    _configure_test_routes(monkeypatch)
    client, deps = _client(tmp_path)
    sid = client.post("/sessions", json={}).json()["id"]
    source_id = _send(client, sid).json()["id"]
    _train_source(tmp_path, deps, source_id)
    sandbox = LocalSubprocessSandbox()

    run_id, frozen_inputs, source_events = _publish_session_evaluation(
        tmp_path, sid, source_id, evaluator_message
    )
    _, deps = _client(tmp_path)
    run = deps.run_service.get_run(run_id)
    assert run.conversation is not None
    assert run.conversation.work_kind == "evaluate_existing"
    assert run.conversation.model_inputs == frozen_inputs
    reference = run.conversation.model_inputs[0]
    catalog = default_thy_catalog()
    log = deps.event_store.open(run_id)
    store = deps.artifact_store_factory(run_id)
    context = replace(
        review_gated_context(
            tmp_path / "evaluation",
            run_id=run_id,
            log=log,
            engine=PolicyEngine(development_policy()),
        ),
        artifact_store=store,
    )
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                EvaluateModel(
                    sandbox=sandbox,
                    mode=SandboxMode.DANGER_FULL_ACCESS,
                ),
            ),
        )
    )
    route = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")

    def prepare(ids):
        materialize_model_inputs(
            tmp_path / "runtime",
            run,
            store,
            log,
            get_run=deps.run_store.get,
            events=deps.event_store,
            selected_model_ids=ids,
        )

    arguments = {
        "model_artifact": reference.model_alias,
        "dataset": reference.dataset_alias,
        "target_column": reference.target_column,
        "evaluation_context_artifact": reference.context_alias,
        "description": "Calculate held-out metrics without fitting",
        "metrics_artifact_name": "metrics/different-name.json",
    }
    planner_provider = ScriptedProvider(
        [_plan(), LLMToolCall(id="evaluate", name="evaluate_model", arguments=arguments)],
        model="test-model",
    )
    parked = run_thy(
        ThyInput(run=run),
        catalog,
        log,
        provider=planner_provider,
        project_dir=tmp_path / "workspace",
        gate=_plan_gate(log, approver=auto_approve, plan_passes=True),
        artifact_store=store,
        tool_registry=registry,
        tool_context=context,
        prepare_inputs=prepare,
        route_policy=route,
    )
    assert parked.error is None, parked.error
    plan_prompt = planner_provider.calls[0]["prompt"]
    assert "verified-prior-model-catalog" in plan_prompt
    assert reference.model_artifact_id in plan_prompt
    assert reference.model_sha256 in plan_prompt
    assert reference.source_run_id in plan_prompt
    for metric in ("precision", "recall", "F1", "ROC-AUC"):
        assert metric.casefold() in plan_prompt.casefold()
    assert parked.progress is not None
    assert not [e for e in log.events() if e.type is EventType.TOOL_STARTED]
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(parked.progress.model_dump_json(), encoding="utf-8")
    decision = next(
        e.payload["decision_id"]
        for e in log.events()
        if e.type is EventType.HUMAN_APPROVAL_REQUESTED
    )
    log.append(
        EventType.HUMAN_APPROVAL,
        Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        {"decision_id": decision, "approved": True, "automatic": False},
        subject_id=decision,
    )

    # Reconstruct the consuming store and graph; the exact deferred call remains bound.
    store = LocalArtifactStore(tmp_path / "runtime" / "artifacts" / run_id, run_id)
    context = replace(context, artifact_store=store)
    progress = ThyProgress.model_validate_json(checkpoint.read_text(encoding="utf-8"))

    def report_published_metrics(_messages):
        """Use the task-owned terminal value returned by the actual evaluator call."""
        terminal = next(
            event
            for event in log.events()
            if event.type is EventType.TOOL_COMPLETED
            and event.payload.get("tool") == "evaluate_model"
            and event.payload.get("status") == ToolCallStatus.COMPLETED
        )
        assert terminal.payload["value"]["kind"] == "success"
        artifact_id = terminal.payload["value"]["value"]["artifact_id"]
        assert artifact_id in terminal.payload["artifact_ids"]
        return EvaluationResult(
            summary="Recorded holdout evaluated.",
            metrics_artifact_id=artifact_id,
        )

    resumed = run_thy(
        ThyInput(run=run),
        catalog,
        log,
        artifact_store=store,
        provider=ScriptedProvider([report_published_metrics]),
        project_dir=tmp_path / "workspace",
        tool_registry=registry,
        tool_context=context,
        prepare_inputs=prepare,
        route_policy=route,
        initial_state=ThyState(
            run=run,
            plan=progress.plan,
            completed=progress.completed,
            agent_messages=progress.agent_messages,
            artifacts=progress.artifacts,
        ),
    )
    _assert_published_evaluation(resumed, store, reference, log, deps, source_id, source_events)


def test_session_training_reopens_into_one_real_experiment_specialist_call(tmp_path, monkeypatch):
    _configure_test_routes(monkeypatch)
    client, deps = _client(tmp_path)
    workspace = tmp_path / "workspace"
    data_dir = workspace / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "training.csv").write_text(
        "signal,target\n"
        + "".join(f"{index % 2},{'yes' if index % 2 else 'no'}\n" for index in range(100)),
        encoding="utf-8",
    )
    (workspace / ".thymira" / "config.yaml").write_text(
        "project:\n  name: conversations\n  domain: research\n"
        "datasets:\n  - name: training\n    path: data/training.csv\n"
        "    target: target\n",
        encoding="utf-8",
    )
    session = client.post("/sessions", json={}).json()["id"]
    request = (
        "Realiza un modelo basico sobre el dataset training usando target como variable objetivo."
    )
    deps.session_chat.provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "work_kind": "evaluate_existing",
                }
            )
        ],
        model="test-model",
    )

    published = _chat(client, session, text=request, key="train-model", mode="auto")

    assert published.status_code == 201, published.text
    value = published.json()
    assert value["run_id"]
    discussion = LocalDiscussionRepository(deps.session_chat.root, session).records()[-1]
    assert discussion.work_kind == "train_new"
    first_run = deps.run_service.get_run(value["run_id"])
    assert first_run.conversation is not None
    assert first_run.conversation.work_kind == "train_new"
    assert first_run.conversation.model_inputs == ()

    _, reopened = _client(tmp_path)
    run = reopened.run_service.get_run(value["run_id"])
    assert run.conversation == first_run.conversation
    log = reopened.event_store.open(run.id)
    store = reopened.artifact_store_factory(run.id)
    tool_context = replace(
        _context(tmp_path / "specialist"),
        run_id=run.id,
        workspace=workspace,
        event_log=log,
        gate=Gate(PolicyEngine(development_policy()), log, approver=auto_approve),
        artifact_store=store,
    )
    registry = builtins_registry(
        sandbox=LocalSubprocessSandbox(),
        subprocess_mode=SandboxMode.DANGER_FULL_ACCESS,
    )
    register_dataset(
        store, data_dir / "training.csv", "training", produced_by=tool_context.agent_id
    )
    task = AgentTask(
        id="wrong-evaluator",
        agent=ThyAgentKind.EVALUATION,
        phase=ThyPhase.EXECUTE,
        instruction="Evaluate an existing classifier and calculate its metrics.",
    )

    provider = ScriptedProvider(
        [
            PlanOutput(tasks=(task,), work_kind="evaluate_existing"),
            LLMToolCall(
                id="train-once",
                name="run_experiment",
                arguments={
                    "dataset": "training",
                    "target_column": "target",
                    "seed": 7,
                    "experiment_name": "session-baseline",
                    "description": "Train the requested synthetic baseline",
                },
            ),
            partial(_experiment_tool_result, log),
            SynthesisNarrative(
                tradeoffs="The baseline is simple and easy to review.",
                limitations="The local fixture is small and synthetic.",
            ),
        ],
        model="test-model",
    )
    route = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")

    output = run_thy(
        ThyInput(run=run),
        default_thy_catalog(),
        log,
        provider=provider,
        project_dir=workspace,
        gate=_plan_gate(log, approver=auto_approve, plan_passes=True),
        artifact_store=store,
        tool_registry=registry,
        tool_context=tool_context,
        route_policy=route,
    )

    assert output.error is None, output.error
    assert len(output.experiment_ids) == 1, output.summary
    started = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "run_experiment"
    ]
    assert len(started) == 1
    arguments = started[0].payload["arguments"]
    assert {
        key: arguments[key]
        for key in (
            "dataset",
            "target_column",
            "seed",
            "experiment_name",
            "description",
        )
    } == {
        "dataset": "training",
        "target_column": "target",
        "seed": 7,
        "experiment_name": "session-baseline",
        "description": "Train the requested synthetic baseline",
    }
    assert arguments["code"] is None
    assert arguments["model_artifact_name"] is None
    assert arguments["metrics_artifact_name"] is None
    trained = [event for event in log.events() if event.type is EventType.MODEL_TRAINED]
    assert len(trained) == 1
    assert verify_events(log.events()).valid
    assert [
        event.payload["agent"] for event in log.events() if event.type is EventType.AGENT_STARTED
    ] == ["experiment"]
    assert trained[0].payload["seed"] == 7
    completed = next(
        event for event in log.events() if event.type is EventType.EXPERIMENT_COMPLETED
    ).payload["experiment"]
    assert completed["id"] == output.experiment_ids[0]
    assert trained[0].payload["model_artifact_id"] == completed["model_artifact_id"]


def _assert_published_evaluation(resumed, store, reference, log, deps, source_id, source_events):
    assert resumed.error is None
    assert resumed.progress is None
    metrics = json.loads(store.load_bytes("metrics/different-name.json"))
    assert set(metrics["metrics"]) == {"accuracy", "precision", "recall", "f1", "roc_auc"}
    assert metrics["methodology"]["evaluated_rows"] == 25
    assert metrics["methodology"]["source_model_artifact_id"] == reference.model_artifact_id
    assert metrics["model"]["sha256"] == reference.model_sha256
    events = log.events()
    assert not [event for event in events if event.type is EventType.MODEL_TRAINED]
    assert len([event for event in events if event.type is EventType.TOOL_STARTED]) == 1
    assert tuple(deps.event_store.read(source_id)) == source_events
    assert verify_events(events).valid
    assert resumed.summary is not None
    assert "precision" in resumed.summary
    assert check_lineage_coherence(store)[0] is ControlStatus.PASSED
    assert check_report_fidelity(store, events)[0] is ControlStatus.PASSED


def _configure_test_routes(monkeypatch) -> None:
    """Keep the synthetic continuation on an explicit local-only model route."""
    for key in ("FAST", "STANDARD", "FRONTIER"):
        monkeypatch.setenv("THYMIRA_MODEL_" + key, "test-model")
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")


def _train_source(tmp_path, deps, source_id):
    source_log = deps.event_store.open(source_id)
    source = replace(
        _context(tmp_path / "source"),
        run_id=source_id,
        event_log=source_log,
        artifact_store=deps.artifact_store_factory(source_id),
        gate=Gate(PolicyEngine(development_policy()), source_log, approver=auto_approve),
    )
    _register_balanced_dataset(source)
    for artifact in source.artifact_store.manifest().values():
        source_log.append(
            EventType.ARTIFACT_CREATED,
            Actor.system(),
            {
                "artifact_id": artifact.id,
                "name": artifact.name,
                "sha256": artifact.sha256,
            },
        )
    sandbox = LocalSubprocessSandbox()
    manager = ToolManager(
        ToolRegistry(
            (
                cast(
                    "Tool",
                    RunExperiment(
                        sandbox=sandbox,
                        mode=SandboxMode.DANGER_FULL_ACCESS,
                    ),
                ),
            )
        )
    )
    trained = manager.execute(
        source,
        "run_experiment",
        {
            "dataset": "training",
            "target_column": "target",
            "seed": 7,
            "experiment_name": "baseline",
            "description": "Train a synthetic baseline",
        },
    )
    assert trained.call.status is ToolCallStatus.COMPLETED
    _complete(deps, source_id, "Synthetic baseline completed.")
