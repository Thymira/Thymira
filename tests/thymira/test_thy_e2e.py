"""End-to-end THY runs over the credit-risk project (THY-31).

Boundary. Real: the whole ThyGraph (`build_thy_graph`/`run_thy`) wired to a real `Delegator`/
`AgentRunner`, the Tool Manager + Gate, a `LocalArtifactStore` and an `InMemoryEventLog`, all
under `tmp_path`; the project context is a copy of the checked-in `examples/credit-risk` with its
declared dataset in place (`_workspace`), exactly as a real project supplies it. Faked: the model
(a `ScriptedProvider`, never the network) and the tools (small in-process fakes that save
artifacts or fail on demand — never a subprocess).

Two scenarios, matching the deliverable:

- the happy path drives Inspect -> Plan -> Execute -> Summarize to completion and asserts the
  seams a caller in `thymira.core` reads back: a `ThyOutput` carrying a `Recommendation`, at least
  three produced artifacts (metrics, model, the analysis report), and a hash-chain the event log
  still verifies;
- the failed run drives a delegated `run_python` that always fails, and asserts the run ends in a
  clean FAILED state (`ThyOutput.error` set, halted at Execute, the failure diagnosed after being
  retried) rather than hanging or dropping the error silently.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel

from tests.thymira.fixtures_tools import development_policy
from thymira.agents import AgentCatalog, AgentSpec, LLMToolCall, ScriptedProvider
from thymira.agents.llm.routing import Role
from thymira.events import InMemoryEventLog
from thymira.policies import (
    ActionRule,
    Gate,
    Policy,
    PolicyEngine,
    RiskProfile,
    ToolCapability,
    auto_approve,
)
from thymira.schemas import (
    ArtifactKind,
    ConversationInput,
    Decision,
    EventType,
    ModelRoutePolicy,
    Run,
    ToolCallStatus,
    new_id,
)
from thymira.state import LocalArtifactStore
from thymira.thy import (
    AgentTask,
    ExperimentResult,
    PlanOutput,
    SynthesisNarrative,
    ThyAgentKind,
    ThyInput,
    ThyOutput,
    ThyPhase,
    run_thy,
)
from thymira.thy.agents.data import DataProfile, data_agent_catalog
from thymira.tools import LegacyToolValue, Tool, ToolContext, ToolRegistry, ToolResult
from thymira.tools.builtins import Glob, ProfileDataset, QuerySql, ReadFile

if TYPE_CHECKING:
    from thymira.tools import ToolInvocation

pytestmark = pytest.mark.integration

TEST_ROUTE_POLICY = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")


@pytest.fixture(autouse=True)
def _configured_test_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind the direct run_thy provider seam to an explicit code-owned test route."""
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")


_TRACKER_RUN_ID = "run_" + "a" * 32

_PLAN_PASSES = Policy(
    name="plan-passes",
    version="1.0",
    action_rules=(
        ActionRule(
            id="TEST-PLAN",
            action_types=("plan.proposed",),
            decision=Decision.PASS,
            reason="test policy: THY's plan needs no review",
        ),
    ),
)


def _project_dir() -> Path:
    """Absolute path to the checked-in credit-risk example project."""
    return Path(__file__).resolve().parents[2] / "examples" / "credit-risk"


def _workspace(tmp_path: Path) -> Path:
    """A copy of the checked-in credit-risk project with its declared dataset in place.

    `.thymira/config.yaml` declares `german_credit` at `data/applications.csv`, which the
    repository does not commit twice (see examples/credit-risk/data/README.md); a real project
    copies `data/german_credit.csv` there before running, and so does this test.

    Named `project`, not `workspace`, to not collide with `_tool_context`'s own
    `tmp_path / "workspace"` (the agent's run_python sandbox) -- the two are unrelated
    directories under the same `tmp_path`.
    """
    project = tmp_path / "project"
    shutil.copytree(_project_dir(), project)
    (project / "data").mkdir(exist_ok=True)
    shutil.copyfile(
        Path(__file__).resolve().parents[2] / "data" / "german_credit.csv",
        project / "data" / "applications.csv",
    )
    return project


def _run() -> Run:
    return Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt=(
            "Train and evaluate a credit-risk classifier on "
            "german_credit and recommend the best model."
        ),
    )


class _FakeArgs(BaseModel):
    """Permissive argument model so a scripted tool call needs no exact schema."""

    note: str = ""


@dataclass(frozen=True)
class _FakeTool:
    """A tool double: it may save one artifact, or fail, and records nothing itself."""

    name: str
    capability: ToolCapability
    saves: tuple[str, ArtifactKind] | None = None
    fails: bool = False
    description: str = "fake tool for the THY end-to-end test"
    arguments_model: type[BaseModel] = _FakeArgs
    result_model = LegacyToolValue

    def execute(self, invocation: ToolInvocation, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        if self.fails:
            return ToolResult(
                success=False,
                exit_code=1,
                error=f"{self.name} failed: simulated division by zero",
            )
        artifact_ids: tuple[str, ...] = ()
        if self.saves is not None:
            filename, kind = self.saves
            artifact = invocation.artifact_store.save_bytes(
                filename,
                b"fake artifact bytes",
                produced_by=invocation.agent_id,
                kind=kind,
            )
            artifact_ids = (artifact.id,)
        return ToolResult(
            success=True,
            stdout="ok",
            value=LegacyToolValue(text="ok"),
            exit_code=0,
            artifact_ids=artifact_ids,
        )


def _capability(tool_id: str) -> ToolCapability:
    """A local, workspace-writing capability explicitly allowed by the test policy."""
    return ToolCapability(
        id=tool_id,
        risk_tags=("code_execution",),
        side_effects=("workspace_write",),
        external_effects=(),
        reversibility="reversible",
    )


def _experiment_catalog(*, tool_names: tuple[str, ...], max_turns: int) -> AgentCatalog:
    """A single-spec catalog whose 'experiment' agent outputs an `ExperimentResult`.

    Named 'experiment' so `execute_node`'s `catalog.get(ThyAgentKind.EXPERIMENT.value)` resolves
    it, and typed to `ExperimentResult` so the `ExperimentResult -> Experiment` mapping fires.
    """
    spec = AgentSpec(
        name="experiment",
        role=Role.AGENT,
        task_kinds=("code",),
        tool_allowlist=tool_names,
        max_turns=max_turns,
        max_depth=1,
        system_prompt_ref="prompts/experiment.md",
        output_schema_ref="thymira.thy.agents.experiment:ExperimentResult",
    )
    return AgentCatalog(
        (spec,),
        system_prompts={"experiment": "You are the experiment agent. Train and log one model."},
        output_schemas={"experiment": ExperimentResult},
    )


def _tool_context(
    tmp_path: Path, *, run: Run, log: InMemoryEventLog, store: LocalArtifactStore, gate: Gate
) -> ToolContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        run_id=run.id,
        agent_id=new_id("agent"),
        workspace=workspace,
        event_log=log,
        gate=gate,
        artifact_store=store,
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=1.0
        ),
    )


def _experiment_task(instruction: str) -> PlanOutput:
    return PlanOutput(
        tasks=(
            AgentTask(
                id="train",
                agent=ThyAgentKind.EXPERIMENT,
                phase=ThyPhase.EXECUTE,
                instruction=instruction,
            ),
        )
    )


def test_happy_path_yields_a_recommendation_artifacts_and_a_verifiable_log(tmp_path: Path) -> None:
    run = _run()
    log = InMemoryEventLog(run.id)
    store = LocalArtifactStore(tmp_path / "artifacts", run.id)
    # The fake tools persist isolated artifacts, so this test uses the explicit development policy
    # rather than relying on automatic approval to bypass the production side-effect review.
    policy = development_policy().merged_with(_PLAN_PASSES)
    gate = Gate(PolicyEngine(policy), log, approver=auto_approve)
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                _FakeTool(
                    name="run_python",
                    capability=_capability("run_python"),
                    saves=("metrics.json", ArtifactKind.METRICS),
                ),
            ),
            cast(
                "Tool",
                _FakeTool(
                    name="mlflow_log_artifact",
                    capability=_capability("mlflow_log_artifact"),
                    saves=("model.pkl", ArtifactKind.MODEL),
                ),
            ),
        )
    )
    catalog = _experiment_catalog(tool_names=("run_python", "mlflow_log_artifact"), max_turns=6)
    tool_context = _tool_context(tmp_path, run=run, log=log, store=store, gate=gate)
    instruction = "Train and evaluate a credit-risk classifier."
    provider = ScriptedProvider(
        [
            _experiment_task(instruction),
            LLMToolCall(id="c1", name="run_python", arguments={}),
            LLMToolCall(id="c2", name="mlflow_log_artifact", arguments={}),
            LLMToolCall(
                id="c3",
                name="final_result",
                arguments={
                    "parameters": {"seed": "7"},
                    "metrics": {"accuracy": 0.91},
                    "seed": 7,
                    "model_artifact_id": None,
                    "tracker_run_id": _TRACKER_RUN_ID,
                },
            ),
            SynthesisNarrative(
                tradeoffs="Gradient boosting trades interpretability for accuracy.",
                limitations="Evaluated on a single train/test split.",
            ),
        ]
    )

    output = run_thy(
        ThyInput(run=run),
        catalog,
        log,
        provider=provider,
        project_dir=_workspace(tmp_path),
        gate=gate,
        artifact_store=store,
        tool_registry=registry,
        tool_context=tool_context,
        route_policy=TEST_ROUTE_POLICY,
    )

    assert isinstance(output, ThyOutput)
    assert output.error is None
    assert output.recommendation is not None
    assert output.recommendation.best_model == instruction
    assert output.recommendation.metrics == {"accuracy": 0.91}
    assert "interpretability" in output.recommendation.tradeoffs
    assert len(output.experiment_ids) == 1
    assert len(output.artifact_ids) >= 3

    kinds = {artifact.kind for artifact in store.list_active()}
    assert {ArtifactKind.METRICS, ArtifactKind.MODEL, ArtifactKind.REPORT} <= kinds
    assert store.get("analysis.md") is not None
    assert store.verify() == []
    assert log.verify().valid
    assert "datasets/german_credit.schema.json" in {a.name for a in store.list_active()}


def test_failed_run_reaches_summarize_after_a_run_python_failure_is_diagnosed(
    tmp_path: Path,
) -> None:
    run = _run()
    log = InMemoryEventLog(run.id)
    store = LocalArtifactStore(tmp_path / "artifacts", run.id)
    policy = development_policy().merged_with(_PLAN_PASSES)
    gate = Gate(PolicyEngine(policy), log, approver=auto_approve)
    registry = ToolRegistry(
        (
            cast(
                "Tool",
                _FakeTool(name="run_python", capability=_capability("run_python"), fails=True),
            ),
        )
    )
    # max_turns=3 bounds the retry loop: the agent never produces a final result, so the run is
    # forced to FAILED after a few attempts rather than looping.
    catalog = _experiment_catalog(tool_names=("run_python",), max_turns=3)
    tool_context = _tool_context(tmp_path, run=run, log=log, store=store, gate=gate)
    provider = ScriptedProvider(
        [
            _experiment_task("Train a model that cannot be trained."),
            *[LLMToolCall(id=f"c{i}", name="run_python", arguments={}) for i in range(6)],
        ]
    )

    output = run_thy(
        ThyInput(run=run),
        catalog,
        log,
        provider=provider,
        project_dir=_workspace(tmp_path),
        gate=gate,
        artifact_store=store,
        tool_registry=registry,
        tool_context=tool_context,
        route_policy=TEST_ROUTE_POLICY,
    )

    # Execute still reaches Summarize, but unresolved tool failure must prevent successful
    # completion even when the plan named no mandatory artifacts.
    assert output.error == "experiment failed: run_python failed: simulated division by zero"
    assert output.summary == "no experiments to compare; nothing to recommend"
    assert output.recommendation is None
    assert output.experiment_ids == ()

    # The failure was diagnosed from the tool's own error, after being retried more than once.
    failed_run_python = [
        event
        for event in log.events()
        if event.type is EventType.TOOL_COMPLETED
        and event.payload.get("tool") == "run_python"
        and event.payload.get("status") == ToolCallStatus.FAILED.value
    ]
    assert len(failed_run_python) >= 2
    assert "simulated division by zero" in str(failed_run_python[-1].payload.get("error"))

    # A clean stop, not a silent drop: the event log still verifies and no report was written.
    assert log.verify().valid
    assert store.get("analysis.md") is None


@pytest.mark.parametrize(
    ("current_message", "include_plots", "required_paths"),
    [
        pytest.param(
            "Puedes hacer algo más con el dataset?",
            False,
            ("profile/german_credit.json",),
            id="generic-follow-up-does-not-repeat-old-plots",
        ),
        pytest.param(
            "Repeat the german_credit EDA with distributions and numeric correlations.",
            True,
            (
                "profile/german_credit.json",
                "profile/german_credit-distributions.png",
                "profile/german_credit-correlation.png",
            ),
            id="explicitly-repeat-eda-and-plots",
        ),
    ],
)
def test_follow_up_profile_uses_prior_outputs_as_references_and_publishes_fresh_outputs(
    tmp_path: Path,
    current_message: str,
    include_plots: bool,
    required_paths: tuple[str, ...],
) -> None:
    """A two-run graph keeps historical artifacts contextual while rerunning the real tool."""
    project = _workspace(tmp_path)
    policy = development_policy().merged_with(_PLAN_PASSES)
    paths = (
        ("profile/german_credit.json", ArtifactKind.REPORT),
        ("profile/german_credit-distributions.png", ArtifactKind.PLOT),
        ("profile/german_credit-correlation.png", ArtifactKind.PLOT),
    )
    data = DataProfile(
        columns=("duration", "amount", "class"),
        dtypes={"duration": "Int64", "amount": "Int64", "class": "String"},
        missing={"duration": 0, "amount": 0, "class": 0},
        target_candidates=("class",),
    )

    def _run_profile(
        run: Run, tmp: Path, provider: ScriptedProvider
    ) -> tuple[
        ThyOutput,
        LocalArtifactStore,
        InMemoryEventLog,
    ]:
        log = InMemoryEventLog(run.id)
        store = LocalArtifactStore(tmp / "artifacts" / run.id, run.id)
        gate = Gate(PolicyEngine(policy), log, approver=auto_approve)
        context = ToolContext(
            run_id=run.id,
            agent_id=new_id("agent"),
            workspace=tmp / "tool-workspace" / run.id,
            event_log=log,
            gate=gate,
            artifact_store=store,
            risk_profile=RiskProfile(
                risk_level="limited", activity_category="analysis", confidence=1.0
            ),
        )
        output = run_thy(
            ThyInput(run=run),
            data_agent_catalog(),
            log,
            provider=provider,
            project_dir=project,
            gate=gate,
            artifact_store=store,
            tool_registry=ToolRegistry(
                (
                    cast("Tool", Glob()),
                    cast("Tool", ReadFile()),
                    cast("Tool", ProfileDataset()),
                    cast("Tool", QuerySql()),
                )
            ),
            tool_context=context,
            route_policy=TEST_ROUTE_POLICY,
        )
        return output, store, log

    first_run = _run().model_copy(
        update={
            "prompt": ("Perform a basic EDA of german_credit with distributions and correlations.")
        }
    )
    first_instruction = (
        "Call profile_dataset for german_credit with include_plots=true and publish the profile "
        "and charts."
    )
    first_provider = ScriptedProvider(
        [
            PlanOutput(
                tasks=(
                    AgentTask(
                        id="profile",
                        agent=ThyAgentKind.DATA,
                        phase=ThyPhase.EXECUTE,
                        instruction=first_instruction,
                    ),
                )
            ),
            LLMToolCall(
                id="first-profile",
                name="profile_dataset",
                arguments={
                    "dataset": "german_credit",
                    "description": "Profile the registered German credit dataset",
                    "include_plots": True,
                },
            ),
            data,
        ]
    )
    first_output, first_store, first_log = _run_profile(
        first_run,
        tmp_path / "first",
        first_provider,
    )

    assert first_output.error is None
    assert first_output.missing_artifacts == ()
    assert {name for name, _kind in paths} <= {item.name for item in first_store.list_active()}
    assert first_store.verify() == []
    assert first_log.verify().valid

    second_run = Run(
        id=new_id("run"),
        project_id=first_run.project_id,
        session_id=first_run.session_id,
        prompt=(
            f"Earlier run {first_run.id} published profile/german_credit.json and its charts.\n"
            f"Current request: {current_message}"
        ),
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="repeat-profile-with-context",
            message=current_message,
            context_run_ids=(first_run.id,),
        ),
    )
    contaminated = {
        "tasks": [
            {
                "id": "profile",
                "agent": ThyAgentKind.DATA.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": first_instruction,
                "required_artifacts": [{"name": name, "kind": kind.value} for name, kind in paths],
                "artifact_references": [],
            }
        ]
    }
    output_names = ", ".join(required_paths)
    second_instruction = (
        f"Call profile_dataset for german_credit with include_plots={str(include_plots).lower()}. "
        f"Publish {output_names} as fresh outputs."
    )
    second_plan = {
        "tasks": [
            {
                "id": "profile",
                "agent": ThyAgentKind.DATA.value,
                "phase": ThyPhase.EXECUTE.value,
                "instruction": second_instruction,
                "outputs_to_publish": [
                    {
                        "name": name,
                        "kind": kind.value,
                    }
                    for name, kind in paths
                    if name in required_paths
                ],
                "artifact_references": [
                    {"name": name, "source_run_id": first_run.id} for name, _kind in paths
                ],
            }
        ]
    }
    second_provider = ScriptedProvider(
        [
            contaminated,
            second_plan,
            LLMToolCall(
                id="second-profile",
                name="profile_dataset",
                arguments={
                    "dataset": "german_credit",
                    "description": "Repeat the registered dataset profile",
                    "include_plots": include_plots,
                },
            ),
            data,
        ]
    )
    second_output, second_store, second_log = _run_profile(
        second_run, tmp_path / "second", second_provider
    )

    assert second_output.error is None
    assert second_output.missing_artifacts == ()
    assert set(second_output.required_artifacts) == set(required_paths)
    assert set(second_output.artifact_ids).isdisjoint(
        {artifact.id for artifact in first_store.list_active()}
    )
    active_names = {item.name for item in second_store.list_active()}
    assert set(required_paths) <= active_names
    assert not ({name for name, _kind in paths} - set(required_paths)) & active_names
    assert second_store.verify() == []
    assert second_log.verify().valid
    execution_prompt = next(
        call["prompt"]
        for call in second_provider.calls
        if "Prior-run artifacts supplied as references only" in call["prompt"]
    )
    assert first_run.id in execution_prompt
    assert all(name in execution_prompt for name, _kind in paths)
    assert any(
        event.type is EventType.AGENT_COMPLETED and event.payload.get("end_reason") == "completed"
        for event in second_log.events()
    )
