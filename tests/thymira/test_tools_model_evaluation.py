"""Model evaluation and early experiment destination contracts."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError

from tests.thymira.fixtures_tools import development_policy, review_gated_context
from thymira.events import InMemoryEventLog
from thymira.policies import Gate, PolicyEngine, RiskProfile, auto_approve
from thymira.schemas import (
    Actor,
    ActorKind,
    ArtifactKind,
    EventType,
    SandboxEnforcement,
    SandboxMode,
    ToolCallStatus,
    new_id,
)
from thymira.state import ArtifactWrite, LocalArtifactStore
from thymira.tools import (
    Tool,
    ToolContext,
    ToolExecutionError,
    ToolInvocation,
    ToolManager,
    ToolRegistry,
)
from thymira.tools.builtins import EvaluateModel
from thymira.tools.builtins.evaluate_model import EvaluateModelArguments
from thymira.tools.builtins.run_experiment import RunExperiment, RunExperimentArguments
from thymira.tools.datasets import register_dataset
from thymira.tools.mlflow import MlflowTracker
from thymira.tools.sandbox import LocalSubprocessSandbox, SandboxRun, StagedInput

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@dataclass
class _StoppedSandbox:
    """Count whether destination preflight reaches subprocess execution."""

    calls: int = 0

    def run(
        self,
        argv: list[str],
        *,
        workspace: Path,
        mode: SandboxMode,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        staged_inputs: tuple[StagedInput, ...] | list[StagedInput] | None = None,
    ) -> SandboxRun:
        del argv, workspace, timeout_s, env, staged_inputs
        self.calls += 1
        return SandboxRun(
            stdout="",
            stderr="sandbox reached before destination refusal",
            exit_code=1,
            mode=mode,
            enforcement=SandboxEnforcement.PARTIAL,
        )


def _invocation(tmp_path: Path) -> ToolInvocation:
    """Register the small synthetic dataset used by preflight tests."""
    run_id = new_id("run")
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "training.csv"
    source.write_text("value,target\n0,no\n1,yes\n2,no\n3,yes\n", encoding="utf-8")
    register_dataset(store, source, "training", produced_by=new_id("agent"))
    return ToolInvocation(
        run_id=run_id,
        agent_id=new_id("agent"),
        workspace=workspace,
        artifact_store=store,
    )


def _context(tmp_path: Path) -> ToolContext:
    """Build a local manager context whose policy permits tmp_path subprocess work."""
    run_id = new_id("run")
    event_log = InMemoryEventLog(run_id)
    return ToolContext(
        run_id=run_id,
        agent_id=new_id("agent"),
        workspace=tmp_path / "workspace",
        event_log=event_log,
        gate=Gate(PolicyEngine(development_policy()), event_log, approver=auto_approve),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts", run_id),
        risk_profile=RiskProfile(
            risk_level="limited", activity_category="analysis", confidence=1.0
        ),
    )


def _register_balanced_dataset(context: ToolContext) -> None:
    context.workspace.mkdir(parents=True, exist_ok=True)
    source = context.workspace / "training.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("signal", "target"))
        writer.writerows((index % 2, "yes" if index % 2 else "no") for index in range(100))
    register_dataset(
        context.artifact_store,
        source,
        "training",
        produced_by=context.agent_id,
        source_path="training.csv",
    )


def _save_evaluation_context(context: ToolContext) -> str:
    model = context.artifact_store.get("models/thymira-experiment.joblib")
    dataset = context.artifact_store.get("datasets/training.csv")
    schema_artifact = context.artifact_store.get("datasets/training.schema.json")
    assert model is not None
    assert dataset is not None
    assert schema_artifact is not None
    trained = next(
        event for event in context.event_log.events() if event.type is EventType.MODEL_TRAINED
    )
    payload = {
        "model_sha256": model.sha256,
        "dataset_sha256": dataset.sha256,
        "schema_sha256": schema_artifact.sha256,
        "target_column": "target",
        "seed": trained.payload["seed"],
        "split": trained.payload["split"],
        "training_kind": "default_logistic_regression",
        "source_run_id": context.run_id,
        "source_model_artifact_id": trained.payload["model_artifact_id"],
    }
    name = "provenance/evaluation-context.json"
    context.artifact_store.save_artifact_batch(
        (
            ArtifactWrite(
                name,
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                kind=ArtifactKind.OTHER,
                media_type="application/json",
                input_artifact_names=(
                    "models/thymira-experiment.joblib",
                    "datasets/training.csv",
                    "datasets/training.schema.json",
                ),
            ),
        ),
        produced_by=context.run_id,
    )
    return name


def test_run_experiment_rejects_binary_model_with_json_extension() -> None:
    with pytest.raises(ValidationError, match="joblib"):
        RunExperimentArguments.model_validate(
            {
                "dataset": "training",
                "target_column": "target",
                "description": "Reject a binary model extension that declares JSON.",
                "model_artifact_name": "models/model_metrics.json",
            }
        )


def test_run_experiment_refuses_colliding_outputs_before_sandbox(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path)
    sandbox = _StoppedSandbox()

    result = RunExperiment(sandbox=sandbox, mode=SandboxMode.DANGER_FULL_ACCESS).execute(
        invocation,
        {
            "dataset": "training",
            "target_column": "target",
            "seed": 7,
            "timeout_s": 30.0,
            "experiment_name": "new-name",
            "model_artifact_name": "models/shared.joblib",
            "metrics_artifact_name": "models/shared.joblib",
        },
    )

    assert not result.success
    assert "destinations collide" in (result.error or "")
    assert sandbox.calls == 0


def test_evaluate_model_requires_provenance_for_recorded_holdout() -> None:
    with pytest.raises(ValidationError, match="evaluation_context_artifact"):
        EvaluateModelArguments.model_validate(
            {
                "model_artifact": "models/existing.joblib",
                "dataset": "evaluation",
                "target_column": "target",
                "description": "Evaluate the recorded test split",
            }
        )


def test_evaluate_model_refuses_a_provenance_hash_mismatch_before_sandbox(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    model = invocation.artifact_store.save_bytes(
        "models/model.joblib",
        b"model bytes",
        produced_by=invocation.agent_id,
        kind=ArtifactKind.MODEL,
        media_type="application/octet-stream",
    )
    dataset = invocation.artifact_store.get("datasets/training.csv")
    schema = invocation.artifact_store.get("datasets/training.schema.json")
    assert dataset is not None
    assert schema is not None
    context_name = "provenance/context.json"
    invocation.artifact_store.save_artifact_batch(
        (
            ArtifactWrite(
                context_name,
                json.dumps(
                    {
                        "model_sha256": hashlib.sha256(b"different model").hexdigest(),
                        "dataset_sha256": dataset.sha256,
                        "schema_sha256": schema.sha256,
                        "target_column": "target",
                        "seed": 7,
                        "split": {
                            "test_size": 0.25,
                            "random_state": 7,
                            "shuffle": True,
                            "stratify": None,
                        },
                        "training_kind": "default_logistic_regression",
                        "source_run_id": new_id("run"),
                        "source_model_artifact_id": model.id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                kind=ArtifactKind.OTHER,
                media_type="application/json",
                input_artifact_names=(
                    "models/model.joblib",
                    "datasets/training.csv",
                    "datasets/training.schema.json",
                ),
            ),
        ),
        produced_by=invocation.run_id,
    )
    sandbox = _StoppedSandbox()

    with pytest.raises(ToolExecutionError, match="model SHA-256"):
        EvaluateModel(sandbox=sandbox, mode=SandboxMode.DANGER_FULL_ACCESS).execute(
            invocation,
            {
                "model_artifact": "models/model.joblib",
                "dataset": "training",
                "target_column": "target",
                "evaluation_context_artifact": context_name,
                "description": "Reject a provenance model hash mismatch",
            },
        )
    assert sandbox.calls == 0


def test_evaluate_model_rejects_agent_authored_holdout_context(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path)
    model = invocation.artifact_store.save_bytes(
        "models/model.joblib",
        b"model bytes",
        produced_by=invocation.agent_id,
        kind=ArtifactKind.MODEL,
        media_type="application/octet-stream",
    )
    dataset = invocation.artifact_store.get("datasets/training.csv")
    schema = invocation.artifact_store.get("datasets/training.schema.json")
    assert dataset is not None
    assert schema is not None
    context_name = "provenance/agent-authored.json"
    invocation.artifact_store.save_json(
        context_name,
        {
            "model_sha256": model.sha256,
            "dataset_sha256": dataset.sha256,
            "schema_sha256": schema.sha256,
            "target_column": "target",
            "seed": 7,
            "split": {
                "test_size": 0.25,
                "random_state": 7,
                "shuffle": True,
                "stratify": None,
            },
            "training_kind": "default_logistic_regression",
            "source_run_id": new_id("run"),
            "source_model_artifact_id": model.id,
        },
        produced_by=invocation.agent_id,
        media_type="application/json",
    )
    sandbox = _StoppedSandbox()

    with pytest.raises(ToolExecutionError, match="current Core Run"):
        EvaluateModel(sandbox=sandbox, mode=SandboxMode.DANGER_FULL_ACCESS).execute(
            invocation,
            {
                "model_artifact": "models/model.joblib",
                "dataset": "training",
                "target_column": "target",
                "evaluation_context_artifact": context_name,
                "description": "Reject a caller-authored holdout context",
            },
        )
    assert sandbox.calls == 0


def test_evaluate_model_gate_approves_only_the_exact_retried_call(tmp_path: Path) -> None:
    context = review_gated_context(tmp_path, run_id=new_id("run"))
    _register_balanced_dataset(context)
    context.artifact_store.save_bytes(
        "models/existing.joblib",
        b"sandbox will reject these model bytes",
        produced_by=context.agent_id,
        kind=ArtifactKind.MODEL,
        media_type="application/octet-stream",
    )
    sandbox = _StoppedSandbox()
    manager = ToolManager(
        ToolRegistry(
            (cast("Tool", EvaluateModel(sandbox=sandbox, mode=SandboxMode.WORKSPACE_WRITE)),)
        )
    )
    arguments = {
        "model_artifact": "models/existing.joblib",
        "dataset": "training",
        "target_column": "target",
        "split": "all_rows",
        "description": "Evaluate this registered model over the explicitly selected rows",
    }

    pending = manager.execute(context, "evaluate_model", arguments)

    assert pending.call.status is ToolCallStatus.DENIED
    assert pending.pending_approval is not None
    assert sandbox.calls == 0
    context.event_log.append(
        EventType.HUMAN_APPROVAL,
        Actor(kind=ActorKind.HUMAN, id="reviewer", authenticated=True),
        {
            "decision_id": pending.pending_approval.id,
            "approved": True,
            "automatic": False,
        },
        subject_id=pending.pending_approval.id,
    )

    retried = manager.execute(context, "evaluate_model", arguments)

    assert retried.call.status is ToolCallStatus.FAILED
    assert retried.call.policy_decision_id == pending.pending_approval.id
    assert "sandbox reached" in (retried.call.error or "")
    assert sandbox.calls == 1
    started = next(
        event for event in context.event_log.events() if event.type is EventType.TOOL_STARTED
    )
    assert started.payload["decision_id"] == pending.pending_approval.id


@pytest.mark.integration
def test_evaluate_model_reports_recorded_holdout_and_explicit_all_rows_without_training(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    _register_balanced_dataset(context)
    sandbox = LocalSubprocessSandbox()
    manager = ToolManager(
        ToolRegistry(
            (
                cast(
                    "Tool",
                    RunExperiment(sandbox=sandbox, mode=SandboxMode.DANGER_FULL_ACCESS),
                ),
                cast(
                    "Tool",
                    EvaluateModel(sandbox=sandbox, mode=SandboxMode.DANGER_FULL_ACCESS),
                ),
            )
        )
    )
    trained = manager.execute(
        context,
        "run_experiment",
        {
            "dataset": "training",
            "target_column": "target",
            "experiment_name": "thymira-experiment",
            "seed": 7,
            "description": "Train a synthetic balanced baseline",
        },
    )
    assert trained.call.status is ToolCallStatus.COMPLETED
    provenance = _save_evaluation_context(context)
    model_before = context.artifact_store.get("models/thymira-experiment.joblib")
    assert model_before is not None
    tracker = MlflowTracker(context.workspace)
    tracker_runs_before = _tracker_snapshot(tracker.query_runs())

    heldout = manager.execute(
        context,
        "evaluate_model",
        {
            "model_artifact": "models/thymira-experiment.joblib",
            "dataset": "training",
            "target_column": "target",
            "evaluation_context_artifact": provenance,
            "positive_class": "no",
            "description": "Calculate full classifier test metrics",
        },
    )

    assert heldout.call.status is ToolCallStatus.COMPLETED
    payload = json.loads(heldout.result.stdout)
    assert set(payload["metrics"]) == {"accuracy", "precision", "recall", "f1", "roc_auc"}
    assert payload["metrics"] == {
        "accuracy": 1.0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "roc_auc": 1.0,
    }
    assert payload["methodology"]["split"] == "recorded_holdout"
    assert payload["methodology"]["evaluated_rows"] == 25
    assert payload["methodology"]["positive_class"] == "no"
    assert payload["methodology"]["averaging"] == "binary"
    assert payload["methodology"]["is_holdout"] is True
    metrics_artifact = context.artifact_store.get(payload["metrics_artifact_name"])
    assert metrics_artifact is not None
    assert metrics_artifact.kind is ArtifactKind.METRICS
    assert context.artifact_store.load_json(payload["metrics_artifact_name"]) == payload

    all_rows = manager.execute(
        context,
        "evaluate_model",
        {
            "model_artifact": "models/thymira-experiment.joblib",
            "dataset": "training",
            "target_column": "target",
            "split": "all_rows",
            "description": "Evaluate all rows explicitly",
        },
    )

    assert all_rows.call.status is ToolCallStatus.COMPLETED
    all_rows_payload = json.loads(all_rows.result.stdout)
    assert all_rows_payload["methodology"]["split"] == "all_rows"
    assert all_rows_payload["methodology"]["evaluated_rows"] == 100
    assert all_rows_payload["methodology"]["is_holdout"] is False
    assert (
        "does not establish an independent holdout"
        in all_rows_payload["methodology"]["interpretation"]
    )
    model_after = context.artifact_store.get("models/thymira-experiment.joblib")
    assert model_after is not None
    assert model_after.sha256 == model_before.sha256
    assert _tracker_snapshot(tracker.query_runs()) == tracker_runs_before


def _tracker_snapshot(runs: tuple[Any, ...]) -> tuple[tuple[Any, ...], ...]:
    """Compare tracker values rather than their fresh read-view object identities."""
    return tuple(
        (
            run.run_id,
            run.experiment_name,
            run.status,
            run.params,
            run.metrics,
            run.artifacts,
            run.tags,
        )
        for run in runs
    )
