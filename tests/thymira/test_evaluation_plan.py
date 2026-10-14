"""Existing model requests cannot acquire training tools through Plan normalization."""

from __future__ import annotations

import pytest

from thymira.core.graph.adapters import default_thy_catalog
from thymira.schemas import (
    ConversationInput,
    ModelWorkIntent,
    Run,
    new_id,
    resolve_model_work_intent,
)
from thymira.schemas.model_input import ModelInput
from thymira.thy.evaluation_plan import normalize_evaluation_plan
from thymira.thy.models import AgentTask, PlanOutput, ThyAgentKind, ThyPhase


def _reference(**updates):
    value = ModelInput(
        source_run_id=new_id("run"),
        model_artifact_id=new_id("artifact"),
        model_name="models/baseline.pkl",
        model_sha256="a" * 64,
        dataset_artifact_id=new_id("artifact"),
        dataset_sha256="b" * 64,
        schema_artifact_id=new_id("artifact"),
        schema_sha256="c" * 64,
        dataset_name="sample",
        target_column="label",
        training_seq=5,
        training_hash="d" * 64,
    )
    return value.model_copy(update=updates)


def _run(refs):
    return Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="More metrics for the previous model",
        conversation=ConversationInput(
            ordinal=2,
            client_message_id="next",
            message="More metrics for the previous model",
            work_kind="evaluate_existing",
            model_inputs=refs,
        ),
    )


def _plan():
    return PlanOutput(
        tasks=(
            AgentTask(
                id="training",
                agent=ThyAgentKind.EXPERIMENT,
                phase=ThyPhase.EXECUTE,
                instruction="Train another model",
            ),
        )
    )


def test_evaluation_replaces_training_proposal_with_bound_nontraining_capability():
    ref = _reference()
    catalog = default_thy_catalog()
    plan = normalize_evaluation_plan(_run((ref,)), _plan(), catalog)
    assert plan.source_model_id == ref.model_artifact_id
    assert len(plan.tasks) == 1
    assert catalog.get(plan.tasks[0].agent.value).tool_allowlist == ("evaluate_model",)
    assert ref.model_alias in plan.tasks[0].instruction
    assert plan.tasks[0].required_artifacts[0].metric_keys == (
        "precision",
        "recall",
        "f1",
        "roc_auc",
    )
    assert PlanOutput.model_validate_json(plan.model_dump_json()) == plan


def test_two_models_in_latest_source_require_explicit_identity():
    ref = _reference()
    other = _reference(source_run_id=ref.source_run_id)
    with pytest.raises(ValueError, match="ambiguous"):
        normalize_evaluation_plan(_run((ref, other)), _plan(), default_thy_catalog())


def test_model_proposal_cannot_select_another_source():
    ref = _reference()
    with pytest.raises(ValueError, match="does not match"):
        normalize_evaluation_plan(
            _run((ref,)),
            _plan().model_copy(update={"source_model_id": new_id("artifact")}),
            default_thy_catalog(),
        )


@pytest.mark.parametrize("kind", ["general", "train_new"])
def test_plan_node_uses_human_evaluation_intent_despite_legacy_label(monkeypatch, kind):
    from tests.thymira.test_thy_plan import TEST_ROUTE_POLICY, _gate
    from thymira.agents.llm.scripted import ScriptedProvider
    from thymira.events import InMemoryEventLog
    from thymira.policies import auto_reject
    from thymira.thy.models import ThyState
    from thymira.thy.nodes.plan import plan_node

    for key in ("FAST", "STANDARD", "FRONTIER"):
        monkeypatch.setenv("THYMIRA_MODEL_" + key, "test-model")
    run = _run((_reference(),))
    run = run.model_copy(
        update={
            "conversation": run.conversation.model_copy(
                update={"work_kind": kind, "message": "Calculate more model metrics"}
            )
        }
    )
    log = InMemoryEventLog(run.id)
    proposal = PlanOutput(
        work_kind=kind,
        tasks=(
            AgentTask(
                id="code",
                agent=ThyAgentKind.CODING,
                phase=ThyPhase.EXECUTE,
                instruction="Calculate model metrics",
            ),
        ),
    )
    result = ThyState.model_validate(
        plan_node(
            default_thy_catalog(),
            _gate(log, approver=auto_reject, plan_passes=True),
            log,
            provider=ScriptedProvider([proposal]),
            route_policy=TEST_ROUTE_POLICY,
        )(ThyState(run=run))
    )
    assert result.error is None
    assert len(result.plan) == 1
    assert result.plan[0].agent is ThyAgentKind.EVALUATION


def test_evaluation_does_not_drop_additional_deliverables():
    from thymira.schemas import ArtifactKind
    from thymira.thy.models import ArtifactRequirement

    plan = _plan()
    task = plan.tasks[0].model_copy(
        update={
            "required_artifacts": (
                ArtifactRequirement(name="requested-plot.png", kind=ArtifactKind.PLOT),
            )
        }
    )
    with pytest.raises(ValueError, match="additional deliverable"):
        normalize_evaluation_plan(
            _run((_reference(),)),
            plan.model_copy(update={"tasks": (task,)}),
            default_thy_catalog(),
        )


def test_conflicting_explicit_model_and_run_do_not_select_silently():
    first, second = _reference(), _reference()
    run = _run((first, second))
    run = run.model_copy(
        update={
            "conversation": run.conversation.model_copy(
                update={
                    "message": f"Evaluate {first.model_artifact_id} from {second.source_run_id}"
                }
            )
        }
    )
    with pytest.raises(ValueError, match="contradict"):
        normalize_evaluation_plan(run, _plan(), default_thy_catalog())


@pytest.mark.parametrize(
    "message",
    [
        "Evaluate the new model",
        "More metrics for the new model",
        "Create a model evaluation report",
        "Create metrics for the model",
        "Do not train the model; calculate more metrics",
        "No entrenes el modelo, calcula métricas",
    ],
)
def test_evaluation_wording_does_not_authorize_training(message):
    assert resolve_model_work_intent(message) is not ModelWorkIntent.TRAIN_NEW


@pytest.mark.parametrize(
    "message",
    [
        "Train a new model",
        "Fit a logistic regression",
        "haz un modelo basico tipo logistic regresion",
        "Create a basic model",
        "No entrenes una red neuronal, pero entrena un clasificador",
    ],
)
def test_explicit_training_requests_remain_supported(message):
    assert resolve_model_work_intent(message) is ModelWorkIntent.TRAIN_NEW


@pytest.mark.parametrize("explicit", [False, True])
def test_comparison_cannot_silently_choose_only_the_latest_model(explicit):
    run = _run((_reference(), _reference()))
    run = run.model_copy(
        update={
            "conversation": run.conversation.model_copy(
                update={
                    "message": (
                        f"Compare {run.conversation.model_inputs[0].model_artifact_id} "
                        "with the previous model"
                    )
                    if explicit
                    else "Compare the previous two models",
                }
            )
        }
    )
    with pytest.raises(ValueError, match="comparison is ambiguous"):
        normalize_evaluation_plan(run, _plan(), default_thy_catalog())
