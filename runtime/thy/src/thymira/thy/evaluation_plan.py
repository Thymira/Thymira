"""Bind an evaluation plan to an immutable prior model and a content contract."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from thymira.events import canonical_json
from thymira.schemas import ArtifactKind
from thymira.thy.models import AgentTask, ArtifactRequirement, PlanOutput, ThyAgentKind, ThyPhase

if TYPE_CHECKING:
    from thymira.agents import AgentCatalog
    from thymira.schemas import Run
    from thymira.schemas.model_input import ModelInput


DEFAULT_EVALUATION_METRICS = ("precision", "recall", "f1", "roc_auc")


def prior_model_catalog(run: Run) -> str:
    """Expose source identities and Run-local names without physical storage paths."""
    if run.conversation is None:
        return ""
    return canonical_json(
        [
            {
                "source_run_id": item.source_run_id,
                "model_artifact_id": item.model_artifact_id,
                "model_name": item.model_name,
                "sha256": item.model_sha256,
                "model_artifact": item.model_alias,
                "dataset": item.dataset_alias,
                "target_column": item.target_column,
                "evaluation_context_artifact": item.context_alias,
            }
            for item in run.conversation.model_inputs
        ]
    )


def normalize_evaluation_plan(run: Run, planned: PlanOutput, catalog: AgentCatalog) -> PlanOutput:
    """Restrict existing-model evaluation to the evaluator, never a training fallback."""
    if run.conversation is None:
        raise ValueError("existing-model evaluation requires an explicit registered model input")
    if ThyAgentKind.EVALUATION.value not in catalog.names():
        raise ValueError("the evaluation agent is unavailable")
    inputs = run.conversation.model_inputs
    request = run.conversation.requested_work
    reference = _select_model(inputs, planned.source_model_id, request)
    requirements = []
    for task in planned.tasks:
        for item in task.required_artifacts:
            if item.kind is not ArtifactKind.METRICS:
                raise ValueError(
                    "evaluation cannot produce this additional deliverable: " + item.name
                )
            requirements.append(
                item.model_copy(
                    update={
                        "match": "exact" if _mentions(request, item.name) else "content",
                        "metric_keys": item.metric_keys or DEFAULT_EVALUATION_METRICS,
                        "model_sha256": reference.model_sha256,
                        "source_run_id": reference.source_run_id,
                        "source_model_artifact_id": reference.model_artifact_id,
                        "dataset_sha256": reference.dataset_sha256,
                        "evaluation_split": "recorded_holdout",
                        "media_type": "application/json",
                    }
                )
            )
    if not requirements:
        requirements.append(
            ArtifactRequirement(
                name="model evaluation metrics",
                kind=ArtifactKind.METRICS,
                match="content",
                metric_keys=DEFAULT_EVALUATION_METRICS,
                model_sha256=reference.model_sha256,
                source_run_id=reference.source_run_id,
                source_model_artifact_id=reference.model_artifact_id,
                dataset_sha256=reference.dataset_sha256,
                evaluation_split="recorded_holdout",
                media_type="application/json",
            )
        )
    # The selected source and schema become immutable arguments shown to the agent and Gate.
    arguments = {
        "model_artifact": reference.model_alias,
        "dataset": reference.dataset_alias,
        "target_column": reference.target_column,
        "evaluation_context_artifact": reference.context_alias,
        "split": "recorded_holdout",
    }
    exact = [item.name for item in requirements if item.match == "exact"]
    if len(set(exact)) > 1:
        raise ValueError("evaluation requires one metrics destination")
    if exact:
        arguments["metrics_artifact_name"] = exact[0]
    # These are fields of one evaluation result, not separate files per requested metric.
    keys = tuple(dict.fromkeys(key for item in requirements for key in item.metric_keys))
    combined = requirements[0].model_copy(
        update={
            "name": exact[0] if exact else "model evaluation metrics",
            "match": "exact" if exact else "content",
            "metric_keys": keys,
        }
    )
    return PlanOutput(
        work_kind="evaluate_existing",
        source_model_id=reference.model_artifact_id,
        tasks=(
            AgentTask(
                id="evaluate-existing-model",
                agent=ThyAgentKind.EVALUATION,
                phase=ThyPhase.EXECUTE,
                instruction="Evaluate the existing model without training. Use evaluate_model with "
                + canonical_json(arguments)
                + ". Required metrics: "
                + ", ".join(
                    dict.fromkeys(key for item in requirements for key in item.metric_keys)
                ),
                required_artifacts=(combined,),
                model_input_ids=(reference.model_artifact_id,),
            ),
        ),
    )


def _select_model(inputs: tuple[ModelInput, ...], proposed: str | None, request: str) -> ModelInput:
    if not inputs:
        raise ValueError(
            "no approved model with recorded evaluation inputs is available. Provide the "
            "approved source Run and model artifact with recorded evaluation inputs."
        )
    mentioned_ids = set(re.findall(r"\bartifact_[0-9a-f]{32}\b", request))
    if mentioned_ids - {item.model_artifact_id for item in inputs}:
        raise ValueError("requested model reference is unavailable in this Session snapshot")
    mentioned_runs = set(re.findall(r"\brun_[0-9a-f]{32}\b", request))
    if mentioned_runs - {item.source_run_id for item in inputs}:
        raise ValueError("requested source Run is unavailable in this Session snapshot")
    explicit = [
        item
        for item in inputs
        if _mentions(request, item.model_artifact_id) or _mentions(request, item.model_name)
    ]
    if len(inputs) > 1 and re.search(
        r"\b(?:compare|comparing|comparison|compara(?:r|mos|n)?|comparaci[oó]n)\b",
        request,
        re.IGNORECASE,
    ):
        raise ValueError("model comparison is ambiguous; this evaluator requires one exact model")
    if explicit:
        candidates = explicit
    elif mentioned_runs:
        candidates = [item for item in inputs if item.source_run_id in mentioned_runs]
    else:
        latest = inputs[0].source_run_id
        candidates = [item for item in inputs if item.source_run_id == latest]
    if len(candidates) != 1:
        raise ValueError("model reference is ambiguous; specify the exact artifact ID")
    selected = candidates[0]
    if mentioned_runs and mentioned_runs != {selected.source_run_id}:
        raise ValueError("requested model and source Run selectors contradict each other")
    if proposed is not None and proposed != selected.model_artifact_id:
        raise ValueError("proposed model does not match the current request and approved source")
    return selected


def _mentions(request: str, name: str) -> bool:
    """Match a complete logical name; a basename inside another path is not that name."""
    return bool(re.search(r"(?<![\w./\\-])" + re.escape(name) + r"(?![\w./\\-])", request))
