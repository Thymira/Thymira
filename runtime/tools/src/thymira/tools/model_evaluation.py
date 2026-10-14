"""Pure evaluation provenance and classification-metric rules."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from thymira.schemas import Artifact, ThymiraModel
from thymira.tools.builtins.experiment_repro import DEFAULT_TEST_SIZE
from thymira.tools.models import ToolExecutionError

_BINARY_CLASS_COUNT = 2
_AVERAGES = {"binary", "macro", "micro", "weighted"}


class _RecordedSplit(ThymiraModel):
    """The exact bounded split configuration emitted by run_experiment."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    test_size: StrictFloat = Field(gt=0.0, lt=1.0)
    random_state: StrictInt = Field(ge=0)
    shuffle: StrictBool
    stratify: None


class EvaluationContext(ThymiraModel):
    """Core-authenticated provenance facts required to replay a previous holdout."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    target_column: StrictStr = Field(min_length=1)
    seed: StrictInt = Field(ge=0)
    split: _RecordedSplit
    training_kind: Literal["default_logistic_regression"]
    source_run_id: StrictStr = Field(min_length=1)
    source_model_artifact_id: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def _split_matches_training(self) -> EvaluationContext:
        """Accept only the fixed default split whose behavior this runtime can reproduce."""
        if (
            self.seed != self.split.random_state
            or self.split.test_size != DEFAULT_TEST_SIZE
            or self.split.shuffle is not True
            or self.split.stratify is not None
        ):
            raise ValueError("training split evidence does not match the recorded default split")
        return self


def parse_evaluation_context(data: bytes) -> EvaluationContext:
    """Parse the strict Core provenance payload and translate schema errors for tool callers."""
    try:
        return EvaluationContext.model_validate_json(data)
    except (ValidationError, ValueError) as exc:
        raise ToolExecutionError("evaluation context artifact is missing or malformed") from exc


def verify_evaluation_context(
    context: EvaluationContext,
    provenance: Artifact,
    current_run_id: str,
    model: Artifact,
    dataset: Artifact,
    schema: Artifact,
    target_column: str,
) -> None:
    """Bind Core's split evidence to this Run and the exact registered inputs."""
    if provenance.produced_by != current_run_id:
        raise ToolExecutionError("evaluation context was not produced by the current Core Run")
    required_inputs = {model.id, dataset.id, schema.id}
    if not required_inputs.issubset(set(provenance.input_artifact_ids)):
        raise ToolExecutionError(
            "evaluation context lineage does not include the registered model, dataset and schema"
        )
    if context.model_sha256 != model.sha256:
        raise ToolExecutionError("model SHA-256 does not match evaluation provenance")
    if context.dataset_sha256 != dataset.sha256:
        raise ToolExecutionError("dataset SHA-256 does not match evaluation provenance")
    if context.schema_sha256 != schema.sha256:
        raise ToolExecutionError("dataset schema SHA-256 does not match evaluation provenance")
    if context.target_column != target_column:
        raise ToolExecutionError("target column does not match evaluation provenance")


def classification_metrics(  # noqa: PLR0912  # distinct metric availability reasons stay explicit
    targets: list[str],
    predicted: list[str],
    class_labels: list[str],
    prediction_positive_class: str | None,
    probabilities: list[float] | None,
    requested_positive_class: str | None,
    requested_average: str,
) -> tuple[dict[str, float | None], str, str | None, str | None]:
    """Compute finite classification metrics and explain an unavailable ROC-AUC."""
    binary = len(class_labels) == _BINARY_CLASS_COUNT
    if requested_average == "auto":
        average = "binary" if binary else "macro"
    else:
        average = requested_average
    if average not in _AVERAGES:
        raise ToolExecutionError(f"unsupported averaging method: {average}")
    if average == "binary" and not binary:
        raise ToolExecutionError("binary averaging requires a two-class model")
    if requested_positive_class is not None and not binary:
        raise ToolExecutionError("positive_class is supported only for binary classification")
    positive_class = (
        requested_positive_class
        or prediction_positive_class
        or (class_labels[1] if binary else None)
    )
    if binary and positive_class not in class_labels:
        raise ToolExecutionError("positive_class is not one of the model classes")
    options: dict[str, Any] = {"average": average, "zero_division": 0}
    if average == "binary":
        options["pos_label"] = positive_class
    metrics: dict[str, float | None] = {
        "accuracy": float(accuracy_score(targets, predicted)),
        "precision": float(precision_score(targets, predicted, **options)),
        "recall": float(recall_score(targets, predicted, **options)),
        "f1": float(f1_score(targets, predicted, **options)),
        "roc_auc": None,
    }
    unavailable: str | None = None
    if not binary:
        unavailable = "ROC-AUC is unavailable for multiclass models in this evaluation protocol."
    elif probabilities is None:
        unavailable = "The registered model did not provide predict_proba scores."
    elif positive_class is None:
        unavailable = "A positive class could not be selected from the model classes."
    else:
        binary_targets = [target == positive_class for target in targets]
        if len(set(binary_targets)) < _BINARY_CLASS_COUNT:
            unavailable = (
                "ROC-AUC requires both positive and negative labels in the evaluated rows."
            )
        else:
            try:
                metrics["roc_auc"] = float(roc_auc_score(binary_targets, probabilities))
            except ValueError as exc:
                unavailable = f"ROC-AUC could not be computed: {exc}"
    if any(value is not None and not 0.0 <= value <= 1.0 for value in metrics.values()):
        raise ToolExecutionError("computed classification metrics are outside the [0, 1] range")
    return metrics, average, positive_class, unavailable


__all__ = [
    "EvaluationContext",
    "classification_metrics",
    "parse_evaluation_context",
    "verify_evaluation_context",
]
