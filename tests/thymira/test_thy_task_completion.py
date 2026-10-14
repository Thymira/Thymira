"""THY validates evaluation completion against task-owned, normalized metric evidence."""

from __future__ import annotations

import pytest

from thymira.agents.task_completion import assess_task_completion
from thymira.core.graph.adapters import default_thy_catalog
from thymira.events import InMemoryEventLog
from thymira.schemas import (
    Actor,
    ActorKind,
    ArtifactKind,
    ConversationInput,
    EventType,
    Run,
    Task,
    ToolCallStatus,
    new_id,
)
from thymira.schemas.model_input import ModelInput
from thymira.state import LocalArtifactStore
from thymira.thy.agents.coding import CodeResult
from thymira.thy.agents.evaluation import EvaluationResult
from thymira.thy.evaluation_plan import normalize_evaluation_plan
from thymira.thy.models import (
    AgentTask,
    ArtifactRequirement,
    PlanOutput,
    ThyAgentKind,
    ThyPhase,
)
from thymira.thy.task_completion import task_completion_validator
from thymira.tools.results import ModelEvaluationValue, ToolFailureValue, canonical_result


@pytest.mark.parametrize(
    ("scenario", "should_complete"),
    [
        ("valid", True),
        ("invented", False),
        ("missing", False),
        ("flat_envelope", False),
        ("extra_envelope", False),
        ("malformed_inner", False),
        ("non_object_inner", False),
        ("failed", False),
        ("failure_envelope", False),
        ("denied", False),
        ("other_split", False),
        ("other_task", False),
        ("invalidated", False),
        ("duplicate", False),
        ("two_valid", True),
    ],
)
def test_evaluation_completion_requires_the_normalized_task_owned_metrics(
    tmp_path, scenario: str, should_complete: bool
) -> None:
    run_id = new_id("run")
    source_run_id = new_id("run")
    agent_id = new_id("agent")
    task_id = new_id("task")
    event_log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    reference = ModelInput(
        source_run_id=source_run_id,
        model_artifact_id=new_id("artifact"),
        model_name="models/prior.joblib",
        model_sha256="a" * 64,
        dataset_artifact_id=new_id("artifact"),
        dataset_sha256="b" * 64,
        schema_artifact_id=new_id("artifact"),
        schema_sha256="c" * 64,
        dataset_name="prior-data",
        target_column="target",
        training_seq=4,
        training_hash="d" * 64,
    )
    run = Run(
        id=run_id,
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Evaluate the previous model",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="evaluation-turn",
            message="More metrics for the previous model",
            work_kind="evaluate_existing",
            model_inputs=(reference,),
        ),
    )
    plan = normalize_evaluation_plan(
        run,
        PlanOutput(
            tasks=(
                AgentTask(
                    id="proposal",
                    agent=ThyAgentKind.CODING,
                    phase=ThyPhase.EXECUTE,
                    instruction="Calculate model metrics.",
                ),
            )
        ),
        default_thy_catalog(),
    )
    planned_task = plan.tasks[0]
    task = Task(
        id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        objective=planned_task.instruction,
    )
    event_log.append(
        EventType.AGENT_STARTED,
        Actor(kind=ActorKind.AGENT, id=agent_id),
        {"task_id": task_id, "agent_id": agent_id, "agent": "evaluation"},
    )
    expected_id = _publish_metrics(store, agent_id, reference, split="recorded_holdout")
    other_split_id = _publish_metrics(store, agent_id, reference, split="all_rows")
    arguments = {
        "model_artifact": reference.model_alias,
        "dataset": reference.dataset_alias,
        "target_column": reference.target_column,
        "evaluation_context_artifact": reference.context_alias,
        "split": "recorded_holdout",
    }
    metrics_id = _record_evaluation_scenario(
        event_log,
        store,
        task_id,
        agent_id,
        reference,
        arguments=arguments,
        expected_id=expected_id,
        other_split_id=other_split_id,
        scenario=scenario,
    )

    evidence = assess_task_completion(event_log.events(), task_id, store)
    settled = task_completion_validator(planned_task, store)(
        task,
        EvaluationResult(summary="Evaluation result", metrics_artifact_id=metrics_id),
        event_log.events(),
        evidence,
    )

    assert (settled.error is None) is should_complete
    if should_complete:
        assert settled.verified_artifact_ids == (metrics_id,)
    else:
        assert settled.error is not None


def test_partial_republication_does_not_repair_every_output_of_a_failed_call(
    tmp_path,
) -> None:
    run_id = new_id("run")
    agent_id = new_id("agent")
    task_id = new_id("task")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    task = Task(
        id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        objective="Publish both planned sources.",
    )
    planned_task = AgentTask(
        id="sources",
        agent=ThyAgentKind.CODING,
        phase=ThyPhase.EXECUTE,
        instruction="Publish both planned sources.",
        required_artifacts=(
            ArtifactRequirement(name="first.py", kind=ArtifactKind.CODE),
            ArtifactRequirement(name="second.py", kind=ArtifactKind.CODE),
        ),
    )
    log.append(
        EventType.AGENT_STARTED,
        Actor(kind=ActorKind.AGENT, id=agent_id),
        {"task_id": task_id, "agent_id": agent_id, "agent": "coding"},
    )
    first = store.save_bytes(
        "first.py",
        b"print('previous valid first')",
        produced_by=agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )
    second = store.save_bytes(
        "second.py",
        b"print('new second')",
        produced_by=agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )
    _record_file_attempt(
        log,
        task_id,
        agent_id,
        "write_file",
        "prior-first",
        {"path": "first.py", "kind": "code"},
        status=ToolCallStatus.COMPLETED,
        artifact_id=first.id,
    )
    _record_file_attempt(
        log,
        task_id,
        agent_id,
        "run_python",
        "failed-pair",
        {
            "code": "print('attempt both')",
            "output_artifacts": [
                {"path": "first.py", "kind": "code"},
                {"path": "second.py", "kind": "code"},
            ],
        },
        status=ToolCallStatus.FAILED,
    )
    _record_file_attempt(
        log,
        task_id,
        agent_id,
        "write_file",
        "later-second",
        {"path": "second.py", "kind": "code"},
        status=ToolCallStatus.COMPLETED,
        artifact_id=second.id,
    )

    evidence = assess_task_completion(log.events(), task_id, store)
    settled = task_completion_validator(planned_task, store)(
        task,
        CodeResult(stdout="both outputs are ready", exit_code=0, artifacts=[]),
        log.events(),
        evidence,
    )

    assert settled.unresolved_failures
    assert settled.unresolved_failures[0].call_id == "failed-pair"
    assert settled.repaired_tool_call_ids == ()
    assert settled.missing_required_artifacts == ()
    assert settled.error is not None


def test_untyped_failed_write_can_be_repaired_by_a_typed_required_publication(
    tmp_path,
) -> None:
    """An untyped write declares a path obligation without pretending its kind is OTHER."""
    run_id = new_id("run")
    agent_id = new_id("agent")
    task_id = new_id("task")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    task = Task(
        id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        objective="Publish the required Python source.",
    )
    planned_task = AgentTask(
        id="source",
        agent=ThyAgentKind.CODING,
        phase=ThyPhase.EXECUTE,
        instruction=task.objective,
        required_artifacts=(ArtifactRequirement(name="A.py", kind=ArtifactKind.CODE),),
    )
    log.append(
        EventType.AGENT_STARTED,
        Actor(kind=ActorKind.AGENT, id=agent_id),
        {"task_id": task_id, "agent_id": agent_id, "agent": "coding"},
    )
    _record_file_attempt(
        log,
        task_id,
        agent_id,
        "write_file",
        "untyped-failure",
        {"path": "A.py", "content": "first attempt"},
        status=ToolCallStatus.FAILED,
    )
    published = store.save_bytes(
        "A.py",
        b"print('corrected source')\n",
        produced_by=agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )
    _record_file_attempt(
        log,
        task_id,
        agent_id,
        "write_file",
        "typed-repair",
        {"path": "A.py", "kind": "code", "content": "print('corrected source')"},
        status=ToolCallStatus.COMPLETED,
        artifact_id=published.id,
    )

    evidence = assess_task_completion(log.events(), task_id, store)
    assert [attempt.call_id for attempt in evidence.unresolved_failures] == ["untyped-failure"]
    settled = task_completion_validator(planned_task, store)(
        task,
        CodeResult(stdout="source published", exit_code=0, artifacts=[]),
        log.events(),
        evidence,
    )

    assert settled.error is None
    assert settled.unresolved_failures == ()
    assert settled.repaired_tool_call_ids == ("untyped-failure",)
    assert published.id in settled.verified_artifact_ids


def test_task_plan_settlement_consumes_dynamic_manifest_output_declarations(tmp_path) -> None:
    """THY can settle a required path from a failed dynamic manifest via a later write_file."""
    run_id = new_id("run")
    agent_id = new_id("agent")
    task_id = new_id("task")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    path = "reports/summary.txt"
    task = Task(
        id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        objective="Publish the required summary.",
    )
    planned_task = AgentTask(
        id="summary",
        agent=ThyAgentKind.CODING,
        phase=ThyPhase.EXECUTE,
        instruction="Publish the required summary.",
        required_artifacts=(ArtifactRequirement(name=path, kind=ArtifactKind.OTHER),),
    )
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    dynamic_arguments = {
        "source_artifact_id": "artifact_registered_source",
        "source_artifact_name": "analysis.py",
        "output_manifest_path": "outputs-1.json",
    }
    log.append(
        EventType.TOOL_STARTED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": "dynamic-failure",
            "tool": "run_python",
            "arguments": dynamic_arguments,
        },
        subject_id="dynamic-failure",
    )
    log.append(
        EventType.TOOL_COMPLETED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": "dynamic-failure",
            "tool": "run_python",
            "status": ToolCallStatus.FAILED,
            "exit_code": 0,
            "artifact_ids": [],
            "error": "required output publication failed",
            "value": {
                "kind": "failure",
                "value": {
                    "text": "required output publication failed",
                    "error": "required output publication failed",
                    "code": "TOOL_FAILURE",
                },
            },
            "output_declarations": [{"path": path, "kind": "other"}],
            "publication_progress": {"missing": [[path, "other"]], "present": []},
        },
        subject_id="dynamic-failure",
    )
    published = store.save_bytes(
        path,
        b"required summary\n",
        produced_by=agent_id,
        kind=ArtifactKind.OTHER,
        media_type="text/plain",
    )
    log.append(
        EventType.TOOL_STARTED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": "required-repair",
            "tool": "write_file",
            "arguments": {"path": path, "kind": "other", "content": "required summary"},
        },
        subject_id="required-repair",
    )
    log.append(
        EventType.TOOL_COMPLETED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": "required-repair",
            "tool": "write_file",
            "status": ToolCallStatus.COMPLETED,
            "artifact_ids": [published.id],
        },
        subject_id="required-repair",
    )

    evidence = assess_task_completion(log.events(), task_id, store)
    assert [attempt.call_id for attempt in evidence.unresolved_failures] == ["dynamic-failure"]
    settled = task_completion_validator(planned_task, store)(
        task,
        CodeResult(stdout="summary published", exit_code=0, artifacts=[]),
        log.events(),
        evidence,
    )

    assert settled.error is None
    assert settled.unresolved_failures == ()
    assert settled.repaired_tool_call_ids == ("dynamic-failure",)
    assert published.id in settled.verified_artifact_ids


@pytest.mark.parametrize(
    ("dynamic", "wrong_kind_b"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_thy_keeps_failed_batch_unresolved_when_only_required_subset_is_republished(
    tmp_path, dynamic: bool, wrong_kind_b: bool
) -> None:
    """THY repairs a failed batch only after every declared path/kind is republished."""
    run_id = new_id("run")
    agent_id = new_id("agent")
    task_id = new_id("task")
    log = InMemoryEventLog(run_id)
    store = LocalArtifactStore(tmp_path / "artifacts", run_id)
    task = Task(
        id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        objective="Publish the required source and the companion output.",
    )
    required_path = "reports/summary.txt"
    extra_path = "reports/details.txt"
    planned_task = AgentTask(
        id="summary",
        agent=ThyAgentKind.CODING,
        phase=ThyPhase.EXECUTE,
        instruction=task.objective,
        required_artifacts=(ArtifactRequirement(name=required_path, kind=ArtifactKind.OTHER),),
    )
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    failed_args: dict[str, object]
    if dynamic:
        failed_args = {"output_manifest_path": "outputs.json"}
    else:
        failed_args = {
            "output_artifacts": [
                {"path": required_path, "kind": "other"},
                {"path": extra_path, "kind": "other"},
            ]
        }
    log.append(
        EventType.TOOL_STARTED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": "failed-batch",
            "tool": "run_python",
            "arguments": failed_args,
        },
        subject_id="failed-batch",
    )
    failed_payload: dict[str, object] = {
        "task_id": task_id,
        "tool_call_id": "failed-batch",
        "tool": "run_python",
        "status": ToolCallStatus.FAILED,
        "exit_code": 0,
        "artifact_ids": [],
        "error": "batch publication failed",
        "value": {
            "kind": "failure",
            "value": {
                "text": "batch publication failed",
                "error": "batch publication failed",
                "code": "TOOL_FAILURE",
            },
        },
    }
    if dynamic:
        failed_payload["output_declarations"] = [
            {"path": required_path, "kind": "other"},
            {"path": extra_path, "kind": "other"},
        ]
        failed_payload["publication_progress"] = {
            "missing": [[required_path, "other"], [extra_path, "other"]],
            "present": [],
        }
    log.append(
        EventType.TOOL_COMPLETED,
        actor,
        failed_payload,
        subject_id="failed-batch",
    )
    required = store.save_bytes(
        required_path,
        b"required summary\n",
        produced_by=agent_id,
        kind=ArtifactKind.OTHER,
        media_type="text/plain",
    )
    _record_file_attempt(
        log,
        task_id,
        agent_id,
        "write_file",
        "publish-required-subset",
        {"path": required_path, "kind": "other", "content": "required summary"},
        status=ToolCallStatus.COMPLETED,
        artifact_id=required.id,
    )
    if wrong_kind_b:
        unrelated_kind = store.save_bytes(
            extra_path,
            b"not the declared output kind\n",
            produced_by=agent_id,
            kind=ArtifactKind.DATASET,
            media_type="text/plain",
        )
        _record_file_attempt(
            log,
            task_id,
            agent_id,
            "write_file",
            "publish-wrong-kind-extra",
            {"path": extra_path, "kind": "dataset", "content": "wrong kind"},
            status=ToolCallStatus.COMPLETED,
            artifact_id=unrelated_kind.id,
        )

    evidence = assess_task_completion(log.events(), task_id, store)
    settled = task_completion_validator(planned_task, store)(
        task,
        CodeResult(stdout="required subset exists", exit_code=0, artifacts=[]),
        log.events(),
        evidence,
    )

    assert [attempt.call_id for attempt in settled.unresolved_failures] == ["failed-batch"]
    assert settled.repaired_tool_call_ids == ()
    assert settled.error is not None


def _record_evaluation_scenario(
    event_log: InMemoryEventLog,
    store: LocalArtifactStore,
    task_id: str,
    agent_id: str,
    reference: ModelInput,
    *,
    arguments: dict[str, str],
    expected_id: str,
    other_split_id: str,
    scenario: str,
) -> str | None:
    """Build one task-completion event scenario from real canonical tool results."""
    result_id: str | None = expected_id
    if scenario in {"failed", "denied"}:
        _record_failure(event_log, task_id, agent_id, arguments, scenario)
        result_id = None
    elif scenario == "failure_envelope":
        _record_failure(event_log, task_id, agent_id, arguments, "failed")
    else:
        owner_task_id = new_id("task") if scenario == "other_task" else task_id
        _record_success(
            event_log,
            owner_task_id,
            agent_id,
            arguments,
            expected_id,
            recorded_value=_noncanonical_evaluation_envelope(scenario, arguments, expected_id),
        )
        if scenario == "other_split":
            _record_success(
                event_log,
                task_id,
                agent_id,
                {**arguments, "split": "all_rows"},
                other_split_id,
                call_id="other-split",
            )
            result_id = other_split_id
        elif scenario == "two_valid":
            result_id = _publish_metrics(
                store,
                agent_id,
                reference,
                split="recorded_holdout",
                name="second-recorded-holdout.json",
            )
            _record_success(
                event_log,
                task_id,
                agent_id,
                arguments,
                result_id,
                call_id="second-valid-contract",
            )
        elif scenario == "duplicate":
            _record_success(
                event_log,
                task_id,
                agent_id,
                arguments,
                expected_id,
                call_id="duplicate-valid-contract",
            )
        elif scenario == "invalidated":
            store.invalidate(["metrics/recorded_holdout.json"], "test invalidation")
        elif scenario == "invented":
            result_id = new_id("artifact")
        elif scenario == "missing":
            result_id = None
    return result_id


def _noncanonical_evaluation_envelope(
    scenario: str, arguments: dict[str, str], artifact_id: str
) -> dict[str, object] | None:
    """Represent malformed durable evidence without weakening its typed success fixture."""
    if scenario == "flat_envelope":
        return {"artifact_id": artifact_id}
    if scenario == "extra_envelope":
        return {**_evaluation_success_envelope(arguments, artifact_id), "unexpected": True}
    if scenario == "malformed_inner":
        return {"kind": "success", "value": {"artifact_id": artifact_id}}
    if scenario == "non_object_inner":
        return {"kind": "success", "value": []}
    return None


def _publish_metrics(
    store,
    producer: str,
    reference: ModelInput,
    *,
    split: str,
    name: str | None = None,
) -> str:
    payload = {
        "metrics": {"precision": 0.8, "recall": 0.7, "f1": 0.75, "roc_auc": 0.9},
        "model": {"sha256": reference.model_sha256},
        "dataset": {"sha256": reference.dataset_sha256},
        "methodology": {
            "source_run_id": reference.source_run_id,
            "source_model_artifact_id": reference.model_artifact_id,
            "split": split,
        },
    }
    artifact_name = name or f"{split}.json"
    artifact = store.save_json(
        f"metrics/{artifact_name}",
        payload,
        produced_by=producer,
        kind=ArtifactKind.METRICS,
        media_type="application/json",
    )
    return artifact.id


def _record_file_attempt(
    event_log: InMemoryEventLog,
    task_id: str,
    agent_id: str,
    tool: str,
    call_id: str,
    arguments: dict[str, object],
    *,
    status: ToolCallStatus,
    artifact_id: str | None = None,
) -> None:
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    event_log.append(
        EventType.TOOL_STARTED,
        actor,
        {"task_id": task_id, "tool_call_id": call_id, "tool": tool, "arguments": arguments},
        subject_id=call_id,
    )
    payload: dict[str, object] = {
        "task_id": task_id,
        "tool_call_id": call_id,
        "tool": tool,
        "status": status,
    }
    if artifact_id is not None:
        payload["artifact_ids"] = [artifact_id]
    else:
        payload["error"] = "required output publication failed"
    event_log.append(
        EventType.TOOL_COMPLETED,
        actor,
        payload,
        subject_id=call_id,
    )


def _record_success(
    event_log: InMemoryEventLog,
    task_id: str,
    agent_id: str,
    arguments: dict[str, str],
    artifact_id: str,
    *,
    call_id: str = "evaluate-call",
    recorded_value: dict[str, object] | None = None,
) -> None:
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    event_log.append(
        EventType.TOOL_STARTED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": call_id,
            "tool": "evaluate_model",
            "arguments": arguments,
        },
        subject_id=call_id,
    )
    event_log.append(
        EventType.TOOL_COMPLETED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": call_id,
            "tool": "evaluate_model",
            "status": ToolCallStatus.COMPLETED,
            "artifact_ids": [artifact_id],
            "value": (
                recorded_value
                if recorded_value is not None
                else _evaluation_success_envelope(arguments, artifact_id)
            ),
        },
        subject_id=call_id,
    )


def _evaluation_success_envelope(arguments: dict[str, str], artifact_id: str) -> dict[str, object]:
    """Build the same registered typed result envelope the tool manager records."""
    result = ModelEvaluationValue(
        model_artifact=arguments["model_artifact"],
        dataset=arguments["dataset"],
        split=arguments["split"],
        metrics={"precision": 0.8, "recall": 0.7, "f1": 0.75, "roc_auc": 0.9},
        evaluated_rows=10,
        averaging="auto",
        artifact_id=artifact_id,
    )
    return canonical_result(result, success=True)


def _record_failure(
    event_log: InMemoryEventLog,
    task_id: str,
    agent_id: str,
    arguments: dict[str, str],
    scenario: str,
) -> None:
    call_id = f"{scenario}-call"
    actor = Actor(kind=ActorKind.AGENT, id=agent_id)
    if scenario == "denied":
        event_log.append(
            EventType.TOOL_DENIED,
            actor,
            {
                "task_id": task_id,
                "tool_call_id": call_id,
                "tool": "evaluate_model",
                "reason": "policy denied",
            },
            subject_id=call_id,
        )
        return
    event_log.append(
        EventType.TOOL_STARTED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": call_id,
            "tool": "evaluate_model",
            "arguments": arguments,
        },
        subject_id=call_id,
    )
    event_log.append(
        EventType.TOOL_COMPLETED,
        actor,
        {
            "task_id": task_id,
            "tool_call_id": call_id,
            "tool": "evaluate_model",
            "status": ToolCallStatus.FAILED,
            "error": "evaluation failed",
            "value": canonical_result(ToolFailureValue(error="evaluation failed"), success=False),
        },
        subject_id=call_id,
    )
