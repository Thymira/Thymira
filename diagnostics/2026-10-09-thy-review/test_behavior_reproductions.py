"""Out-of-tree diagnostic regressions for linked training intent and THY scope filtering."""

# ruff: noqa: S101
# Pytest assertions are the expected-behavior checks in these diagnostic reproductions.

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from tests.thymira.fixtures_tools import development_policy
from tests.thymira.test_api_sessions import _client
from tests.thymira.test_session_clarifications import (
    _chat,
    _continue_work,
    _declare_german_credit,
)
from tests.thymira.test_thy_e2e import (
    _PLAN_PASSES,
    _tool_context,
)
from tests.thymira.test_thy_e2e import (
    TEST_ROUTE_POLICY as E2E_ROUTE_POLICY,
)
from tests.thymira.test_thy_e2e import (
    _run as _e2e_run,
)
from tests.thymira.test_thy_plan import TEST_ROUTE_POLICY, _gate, _run

from thymira.agents import LLMToolCall, ScriptedProvider
from thymira.core.graph.agent_catalog import default_thy_catalog
from thymira.events import InMemoryEventLog
from thymira.policies import Gate, PolicyEngine, auto_approve, auto_reject
from thymira.schemas import ArtifactKind, ConversationInput, EventType, ToolCallStatus
from thymira.state import LocalArtifactStore
from thymira.thy import ThyInput, ThyOutput, run_thy
from thymira.thy.agents import DataProfile, data_agent_catalog
from thymira.thy.models import (
    AgentTask,
    PlanOutput,
    RegisteredDataset,
    ThyAgentKind,
    ThyPhase,
    ThyState,
)
from thymira.thy.nodes.plan import plan_node
from thymira.tools import ToolRegistry
from thymira.tools.builtins import Glob, ProfileDataset, QuerySql, ReadFile

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _test_model_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    for variable in (
        "THYMIRA_MODEL_FAST",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FRONTIER",
    ):
        monkeypatch.setenv(variable, "test-model")


def test_api_clarified_dataset_reply_preserves_training_work_kind(tmp_path: Path) -> None:
    """A dataset/complexity answer must not erase the linked request to train a model."""
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()["id"]
    original = "Puedes realizar un modelo?"
    _continue_work(deps, kind="clarification", text="¿Qué dataset deseas usar?")

    clarification = _chat(client, session, text=original, key="training-request")
    assert clarification.status_code == HTTPStatus.CREATED, clarification.text
    assert clarification.json()["kind"] == "clarification"
    assert clarification.json()["run_id"] is None
    assert deps.session_service.get_session(session).run_ids == ()

    _declare_german_credit(tmp_path, target="id")
    _continue_work(deps, kind="action", continues=False)
    reply = "german_credit, el modelo uno sencillito"
    response = _chat(client, session, text=reply, key="dataset-and-complexity")

    assert response.status_code == HTTPStatus.CREATED, response.text
    run = deps.run_service.get_run(response.json()["run_id"])
    assert run.conversation is not None
    assert run.conversation.clarification_context == (original,)
    assert original in run.conversation.requested_work
    assert reply in run.conversation.requested_work
    # Diagnostic expectation: current code publishes work_kind="general" here.
    assert run.conversation.work_kind == "train_new"


_DATA_TASK = AgentTask(
    id="profile",
    agent=ThyAgentKind.DATA,
    phase=ThyPhase.EXECUTE,
    instruction="Profile german_credit.",
)
_EXPERIMENT_TASK = AgentTask(
    id="train",
    agent=ThyAgentKind.EXPERIMENT,
    phase=ThyPhase.EXECUTE,
    instruction="Train the requested model.",
)
_PLANNED_DATA_AND_EXPERIMENT = PlanOutput(tasks=(_DATA_TASK, _EXPERIMENT_TASK))


def _plan_for_reply(
    message: str,
    *,
    clarification_context: tuple[str, ...] = (),
    work_kind: str = "general",
) -> ThyState:
    conversation = ConversationInput(
        ordinal=2 if clarification_context else 1,
        client_message_id="diagnostic-reply",
        message=message,
        clarification_context=clarification_context,
        work_kind=work_kind,
    )
    run = _run().model_copy(
        update={"prompt": conversation.requested_work, "conversation": conversation}
    )
    log = InMemoryEventLog(run.id)
    node = plan_node(
        default_thy_catalog(),
        _gate(log, approver=auto_reject, plan_passes=True),
        log,
        provider=ScriptedProvider([_PLANNED_DATA_AND_EXPERIMENT], model="test-model"),
        route_policy=TEST_ROUTE_POLICY,
    )
    return ThyState.model_validate(
        node(
            ThyState(
                run=run,
                datasets=(RegisteredDataset(name="german_credit", target="id"),),
            )
        )
    )


def test_thy_plan_keeps_experiment_for_linked_training_reply_but_not_controls() -> None:
    """EDA/explanation remain non-training; clarification context retains experiment scope."""
    for control in ("Haz un EDA de german_credit", "Explica el modelo"):
        control_state = _plan_for_reply(control)
        assert control_state.error is None, control_state.error
        assert all(
            task.agent not in {ThyAgentKind.EXPERIMENT, ThyAgentKind.ML}
            for task in control_state.plan
        )

    training_state = _plan_for_reply(
        "german_credit, el modelo uno sencillito",
        clarification_context=("Puedes realizar un modelo?",),
        work_kind="train_new",
    )
    assert training_state.error is None, training_state.error
    # Diagnostic expectation: current normalization leaves only the DATA task.
    assert any(task.agent is ThyAgentKind.EXPERIMENT for task in training_state.plan)


def test_graph_request_cannot_complete_without_registered_plot_artifact(tmp_path: Path) -> None:
    """A valid profile alone must not satisfy the human request for a graph."""
    project = tmp_path / "project"
    data_dir = project / "data"
    (project / ".thymira").mkdir(parents=True)
    data_dir.mkdir()
    (project / ".thymira" / "config.yaml").write_text(
        "project:\n  name: graph-diagnostic\n  domain: research\n"
        "datasets:\n  - name: scores\n    path: data/scores.csv\n",
        encoding="utf-8",
    )
    (data_dir / "scores.csv").write_text("value,group\n1,A\n2,B\n3,A\n", encoding="utf-8")

    request = "Sacame alguna grafica"
    conversation = ConversationInput(
        ordinal=1,
        client_message_id="graph-request",
        message=request,
        work_kind="general",
    )
    run = _e2e_run().model_copy(update={"prompt": request, "conversation": conversation})
    log = InMemoryEventLog(run.id)
    store = LocalArtifactStore(tmp_path / "artifacts", run.id)
    gate = Gate(
        PolicyEngine(development_policy().merged_with(_PLAN_PASSES)),
        log,
        approver=auto_approve,
    )
    catalog_task = AgentTask(
        id="profile",
        agent=ThyAgentKind.DATA,
        phase=ThyPhase.EXECUTE,
        instruction="Profile scores and produce the graph requested by the user.",
        required_artifacts=(),
    )
    profile_call = LLMToolCall(
        id="profile-without-plots",
        name="profile_dataset",
        arguments={
            "dataset": "scores",
            "description": "Profile scores and fulfill the requested graph output.",
            "include_plots": False,
        },
    )
    provider = ScriptedProvider(
        [
            PlanOutput(tasks=(catalog_task,)),
            profile_call,
            DataProfile(
                columns=("value", "group"),
                dtypes={"value": "Int64", "group": "String"},
                missing={"value": 0, "group": 0},
            ),
        ],
        model="test-model",
    )
    registry = ToolRegistry((Glob(), ReadFile(), ProfileDataset(), QuerySql()))
    context = _tool_context(tmp_path, run=run, log=log, store=store, gate=gate)

    output = run_thy(
        ThyInput(run=run),
        data_agent_catalog(),
        log,
        provider=provider,
        project_dir=project,
        gate=gate,
        artifact_store=store,
        tool_registry=registry,
        tool_context=context,
        route_policy=E2E_ROUTE_POLICY,
    )

    assert isinstance(output, ThyOutput)
    assert any(
        event.type is EventType.TOOL_COMPLETED
        and event.payload.get("tool") == "profile_dataset"
        and event.payload.get("status") == ToolCallStatus.COMPLETED.value
        for event in log.events()
    )
    plot_artifacts = [
        artifact for artifact in store.list_active() if artifact.kind is ArtifactKind.PLOT
    ]
    assert plot_artifacts or output.error is not None
