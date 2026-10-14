"""Evaluate a registered model on a verified holdout or an explicit full dataset."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sklearn.model_selection import train_test_split

from thymira.policies import ToolCapability
from thymira.schemas import (
    Artifact,
    ArtifactKind,
    SandboxEnforcement,
    SandboxMode,
    new_id,
)
from thymira.tools.builtins.argument_contracts import DESCRIPTION_FIELD, Description
from thymira.tools.builtins.subprocess_command import (
    ensure_workspace_root,
    link_shaped_refusal,
    persist_for_uninstrumented_backend,
    python_module_command,
    read_only_staging_refusal,
    with_sandbox_evidence,
    workspace_relative_path,
)
from thymira.tools.builtins.subprocess_mode import (
    capability_for_sandbox_mode,
    requested_sandbox_mode,
)
from thymira.tools.datasets import MAX_DATASET_BYTES, DatasetSchema, schema_artifact_name
from thymira.tools.model_evaluation import (
    EvaluationContext,
    classification_metrics,
    parse_evaluation_context,
    verify_evaluation_context,
)
from thymira.tools.model_predictions import PredictionRequest, read_model_predictions
from thymira.tools.model_sidecars import MAX_MODEL_ARTIFACT_BYTES, MAX_SIDECAR_BYTES
from thymira.tools.models import ToolExecutionError, ToolInvocation, ToolResult
from thymira.tools.results import ModelEvaluationValue
from thymira.tools.sandbox import LocalSubprocessSandbox, Sandbox, SandboxRun, StagedInput

_MAX_EVALUATION_ROWS = 1_000_000
_EVALUATION_TIMEOUT_S = 120.0


class EvaluateModelArguments(BaseModel):
    """A registered model, dataset and explicit evaluation population."""

    model_config = ConfigDict(extra="forbid")

    model_artifact: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    target_column: str = Field(min_length=1)
    description: Description = DESCRIPTION_FIELD
    split: Literal["recorded_holdout", "all_rows"] = "recorded_holdout"
    evaluation_context_artifact: str | None = Field(default=None, min_length=1)
    positive_class: str | None = Field(default=None, min_length=1)
    averaging: Literal["auto", "binary", "macro", "micro", "weighted"] = "auto"
    metrics_artifact_name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _holdout_needs_evidence(self) -> EvaluateModelArguments:
        """Require the Core-produced origin record before calling any result a holdout."""
        if self.split == "recorded_holdout" and self.evaluation_context_artifact is None:
            raise ValueError(
                "recorded_holdout requires evaluation_context_artifact with verified Core "
                "training evidence"
            )
        return self

    @model_validator(mode="after")
    def _metric_destination_is_json(self) -> EvaluateModelArguments:
        """Keep the requested output path compatible with the metrics JSON artifact."""
        name = self.metrics_artifact_name
        if name is None:
            return self
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or PureWindowsPath(normalized).drive
            or ".." in path.parts
            or normalized != path.as_posix()
            or path.parts[0] == ".thymira"
        ):
            raise ValueError("metrics artifact name must be canonical and workspace-relative")
        if path.suffix.casefold() != ".json":
            raise ValueError("metrics artifact must use the .json extension")
        return self


@dataclass(frozen=True, slots=True)
class EvaluateModel:
    """Compute classification metrics without fitting or modifying the registered model."""

    sandbox: Sandbox = field(default_factory=LocalSubprocessSandbox)
    mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    name: str = "evaluate_model"
    description: str = (
        "Evaluate an existing registered model without fitting it. Replays a holdout only when "
        "the Core evaluation context verifies the exact model, dataset, schema and split. "
        "Otherwise name split='all_rows' explicitly; that result does not establish a holdout. "
        "Report accuracy, precision, recall, F1 and ROC-AUC or its unavailability reason."
    )
    arguments_model: type[BaseModel] = EvaluateModelArguments
    result_model: type[BaseModel] = ModelEvaluationValue
    _capability: ToolCapability = field(
        default_factory=lambda: ToolCapability(
            id="evaluate_model",
            risk_tags=("code_execution",),
            data_access=("dataset", "model"),
            side_effects=("workspace_write",),
            external_effects=(),
        )
    )

    @property
    def capability(self) -> ToolCapability:
        """Return the capability that records this instance's requested sandbox mode."""
        return capability_for_sandbox_mode(self._capability, self.mode)

    def execute(  # noqa: PLR0911, PLR0912, PLR0915  # ordered preflight/inference/publication stages
        self, invocation: ToolInvocation, arguments: dict[str, Any]
    ) -> ToolResult:
        """Load the registered inputs, run isolated inference and publish typed metrics."""
        mode = requested_sandbox_mode(self.capability)
        if refusal := read_only_staging_refusal(mode):
            return refusal

        model_artifact, model_bytes = _load_verified_artifact(
            invocation, arguments["model_artifact"], "model artifact", MAX_MODEL_ARTIFACT_BYTES
        )
        if model_artifact.kind is not ArtifactKind.MODEL:
            raise ToolExecutionError("registered model artifact does not have kind=model")
        dataset_artifact, schema_artifact, schema, frame, _source_dataset_bytes = _load_dataset(
            invocation, arguments["dataset"]
        )
        if dataset_artifact.kind is not ArtifactKind.DATASET:
            raise ToolExecutionError("registered evaluation input does not have kind=dataset")
        target_column = arguments["target_column"]
        if target_column not in schema.columns or target_column not in frame.columns:
            raise ToolExecutionError(f"dataset is missing target column: {target_column}")
        if frame.height == 0:
            raise ToolExecutionError("evaluation dataset has no rows")
        if frame.get_column(target_column).null_count():
            raise ToolExecutionError("evaluation target column contains missing labels")

        context: EvaluationContext | None = None
        context_name = arguments.get("evaluation_context_artifact")
        if context_name is not None:
            context_artifact, context = _load_evaluation_context(invocation, context_name)
            verify_evaluation_context(
                context,
                context_artifact,
                invocation.run_id,
                model_artifact,
                dataset_artifact,
                schema_artifact,
                target_column,
            )
        if arguments["split"] == "recorded_holdout":
            if context is None:
                raise ToolExecutionError(
                    "recorded holdout cannot be reconstructed without verified evaluation context"
                )
            try:
                _, evaluation_indices = train_test_split(
                    list(range(frame.height)),
                    test_size=context.split.test_size,
                    random_state=context.split.random_state,
                    shuffle=context.split.shuffle,
                    stratify=context.split.stratify,
                )
            except ValueError as exc:
                raise ToolExecutionError(
                    f"recorded training split cannot be replayed: {exc}"
                ) from exc
            split_metadata: dict[str, Any] = {
                "split": "recorded_holdout",
                "test_size": context.split.test_size,
                "random_state": context.split.random_state,
                "seed": context.seed,
                "shuffle": context.split.shuffle,
                "stratify": context.split.stratify,
                "source_run_id": context.source_run_id,
                "source_model_artifact_id": context.source_model_artifact_id,
                "is_holdout": True,
                "interpretation": (
                    "Replayed the hash-verified training split over the original registered "
                    "dataset."
                ),
            }
        else:
            evaluation_indices = list(range(frame.height))
            split_metadata = {
                "split": "all_rows",
                "test_size": None,
                "random_state": None,
                "seed": None,
                "shuffle": None,
                "stratify": None,
                "source_run_id": context.source_run_id if context is not None else None,
                "source_model_artifact_id": (
                    context.source_model_artifact_id if context is not None else None
                ),
                "is_holdout": False,
                "interpretation": (
                    "All rows of the named registered dataset were evaluated; this does not "
                    "establish an independent holdout."
                ),
            }
        if not evaluation_indices:
            raise ToolExecutionError("evaluation split contains no rows")
        evaluation_frame = frame[evaluation_indices]
        targets = [str(value) for value in evaluation_frame.get_column(target_column).to_list()]

        workspace = Path(invocation.workspace).resolve()
        work_dir = workspace / ".thymira"
        call_id = new_id("tool")
        dataset_path = work_dir / f"{call_id}-evaluation.parquet"
        model_path = work_dir / f"{call_id}-model.bin"
        request_path = work_dir / f"{call_id}-request.json"
        response_path = work_dir / f"{call_id}-response.json"
        metrics_name = _metrics_destination(
            invocation,
            arguments.get("metrics_artifact_name"),
            arguments["model_artifact"],
            schema.artifact_name,
            context_name,
            mode,
        )
        if isinstance(metrics_name, ToolResult):
            return metrics_name
        dataset_relative = workspace_relative_path(workspace, dataset_path)
        model_relative = workspace_relative_path(workspace, model_path)
        request_relative = workspace_relative_path(workspace, request_path)
        response_relative = workspace_relative_path(workspace, response_path)
        request = PredictionRequest(
            model_path=model_relative,
            dataset_path=dataset_relative,
            target_column=target_column,
            positive_class=arguments.get("positive_class"),
        )
        if refusal := link_shaped_refusal(
            mode, workspace, work_dir, dataset_path, model_path, request_path, response_path
        ):
            return refusal
        ensure_workspace_root(workspace)
        dataset_buffer = io.BytesIO()
        evaluation_frame.write_parquet(dataset_buffer)
        evaluation_bytes = dataset_buffer.getvalue()
        staged_inputs = (
            StagedInput(dataset_path.relative_to(workspace).as_posix(), evaluation_bytes),
            StagedInput(model_path.relative_to(workspace).as_posix(), model_bytes),
            StagedInput(
                request_path.relative_to(workspace).as_posix(),
                request.model_dump_json().encode("utf-8"),
            ),
        )
        run = self.sandbox.run(
            python_module_command(
                "thymira.tools.model_prediction_worker", request_relative, response_relative
            ),
            workspace=workspace,
            mode=mode,
            timeout_s=_EVALUATION_TIMEOUT_S,
            staged_inputs=staged_inputs,
        )
        if run.spec is None:
            persist_for_uninstrumented_backend(workspace, staged_inputs)
        if run.exit_code != 0:
            return _post_sandbox_failure(run, run.stderr or "model evaluation prediction failed")
        try:
            workspace_relative_path(workspace, response_path)
            predictions = read_model_predictions(
                response_path, columns=evaluation_frame.columns, targets=targets
            )
        except (OSError, ToolExecutionError, ValueError) as exc:
            return _post_sandbox_failure(run, f"model prediction produced no valid sidecar: {exc}")

        metrics, averaging, selected_positive, auc_unavailable_reason = classification_metrics(
            targets,
            list(predictions.predictions),
            list(predictions.class_labels),
            predictions.positive_class,
            (
                list(predictions.positive_probabilities)
                if predictions.positive_probabilities is not None
                else None
            ),
            arguments.get("positive_class"),
            arguments["averaging"],
        )
        payload = {
            "metrics": metrics,
            "methodology": {
                **split_metadata,
                "target_column": target_column,
                "evaluated_rows": len(targets),
                "averaging": averaging,
                "averaging_selection": arguments["averaging"],
                "positive_class": selected_positive,
                "positive_class_selection": (
                    "explicit argument"
                    if arguments.get("positive_class") is not None
                    else "model class order"
                ),
                "zero_division": 0,
                "roc_auc_method": "binary ROC-AUC from the model probability for positive_class",
                "roc_auc_unavailable_reason": auc_unavailable_reason,
            },
            "model": {
                "artifact": model_artifact.name,
                "artifact_id": model_artifact.id,
                "sha256": model_artifact.sha256,
            },
            "dataset": {
                "name": arguments["dataset"],
                "artifact": dataset_artifact.name,
                "artifact_id": dataset_artifact.id,
                "sha256": dataset_artifact.sha256,
                "schema_artifact": schema_artifact.name,
                "schema_sha256": schema_artifact.sha256,
            },
            "metrics_artifact_name": metrics_name,
        }
        try:
            artifact = invocation.artifact_store.save_json(
                metrics_name,
                payload,
                produced_by=invocation.agent_id,
                kind=ArtifactKind.METRICS,
                media_type="application/json",
            )
        except (OSError, ValueError) as exc:
            return _post_sandbox_failure(run, f"model evaluation metrics could not be saved: {exc}")
        text = json.dumps(payload, sort_keys=True)
        return with_sandbox_evidence(
            ToolResult(
                success=True,
                stdout=text,
                value=ModelEvaluationValue(
                    text=text,
                    model_artifact=model_artifact.name,
                    dataset=arguments["dataset"],
                    split=arguments["split"],
                    metrics=metrics,
                    evaluated_rows=len(targets),
                    positive_class=selected_positive,
                    averaging=averaging,
                    artifact_id=artifact.id,
                ),
                exit_code=run.exit_code,
                artifact_ids=(artifact.id,),
            ),
            run,
            timeout_s=_EVALUATION_TIMEOUT_S,
        )


def _load_verified_artifact(
    invocation: ToolInvocation, name: str, label: str, max_bytes: int
) -> tuple[Artifact, bytes]:
    """Read one bounded registered artifact and verify its recorded content hash."""
    artifact = invocation.artifact_store.get(name)
    if artifact is None or not artifact.valid:
        raise ToolExecutionError(f"{label} is missing or invalid: {name}")
    if artifact.size_bytes > max_bytes:
        raise ToolExecutionError(f"{label} exceeds {max_bytes} bytes")
    try:
        data = invocation.artifact_store.load_bytes_bounded(name, max_bytes)
    except (OSError, KeyError, ValueError) as exc:
        raise ToolExecutionError(f"{label} is missing or invalid: {name}") from exc
    if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
        raise ToolExecutionError(f"{label} SHA-256 does not match its registered artifact")
    return artifact, data


def _load_dataset(
    invocation: ToolInvocation, name: str
) -> tuple[Artifact, Artifact, DatasetSchema, pl.DataFrame, bytes]:
    """Load registered dataset bytes and schema after checking their manifests."""
    schema_name = schema_artifact_name(name)
    schema_artifact, schema_bytes = _load_verified_artifact(
        invocation, schema_name, "dataset schema artifact", MAX_SIDECAR_BYTES
    )
    try:
        schema = DatasetSchema.model_validate_json(schema_bytes)
    except (ValidationError, ValueError) as exc:
        raise ToolExecutionError("registered dataset schema is malformed") from exc
    dataset_artifact, dataset_bytes = _load_verified_artifact(
        invocation, schema.artifact_name, "registered dataset", MAX_DATASET_BYTES
    )
    if hashlib.sha256(dataset_bytes).hexdigest() != schema.sha256:
        raise ToolExecutionError("registered dataset bytes do not match its captured schema hash")
    if schema.row_count > _MAX_EVALUATION_ROWS:
        raise ToolExecutionError(f"evaluation dataset exceeds the {_MAX_EVALUATION_ROWS}-row limit")
    suffix = PurePosixPath(schema.artifact_name).suffix.casefold()
    try:
        if suffix == ".csv":
            frame = pl.read_csv(io.BytesIO(dataset_bytes))
        elif suffix == ".parquet":
            frame = pl.read_parquet(io.BytesIO(dataset_bytes))
        else:
            raise ToolExecutionError(f"unsupported registered dataset: {schema.artifact_name}")
    except pl.exceptions.PolarsError as exc:
        raise ToolExecutionError("registered dataset could not be read") from exc
    if frame.height != schema.row_count or tuple(frame.columns) != schema.columns:
        raise ToolExecutionError("registered dataset no longer matches its captured schema")
    return dataset_artifact, schema_artifact, schema, frame, dataset_bytes


def _load_evaluation_context(
    invocation: ToolInvocation, name: str
) -> tuple[Artifact, EvaluationContext]:
    """Read the strict JSON provenance artifact supplied by Core."""
    artifact, data = _load_verified_artifact(
        invocation, name, "evaluation context artifact", MAX_SIDECAR_BYTES
    )
    if artifact.media_type != "application/json":
        raise ToolExecutionError("evaluation context artifact must be application/json")
    return artifact, parse_evaluation_context(data)


def _metrics_destination(
    invocation: ToolInvocation,
    requested: str | None,
    model_name: str,
    dataset_name: str,
    context_name: str | None,
    mode: SandboxMode,
) -> str | ToolResult:
    """Resolve a fresh metrics destination without overwriting registered inputs or outputs."""
    name = requested or f"metrics/evaluations/{new_id('tool')}.json"
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or PureWindowsPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or ".." in path.parts
        or path.as_posix() != name
        or path.parts[0] == ".thymira"
        or path.suffix.casefold() != ".json"
    ):
        return _evaluation_refusal(
            mode, "metrics artifact name must be a canonical workspace JSON path"
        )
    protected = {model_name.casefold(), dataset_name.casefold()}
    if context_name is not None:
        protected.add(context_name.casefold())
    if name.casefold() in protected:
        return _evaluation_refusal(
            mode, "metrics artifact destination collides with an input artifact"
        )
    existing = {
        artifact.name.casefold(): artifact.name
        for artifact in invocation.artifact_store.manifest().values()
        if artifact.valid
    }.get(name.casefold())
    if existing is not None:
        return _evaluation_refusal(mode, f"metrics artifact destination already exists: {existing}")
    return name


def _post_sandbox_failure(run: SandboxRun, error: str) -> ToolResult:
    """Return a failed result without erasing the child process's sandbox evidence."""
    return with_sandbox_evidence(
        ToolResult(
            success=False,
            stdout=run.stdout,
            stderr=run.stderr,
            exit_code=run.exit_code,
            error=error,
        ),
        run,
        timeout_s=_EVALUATION_TIMEOUT_S,
    )


def _evaluation_refusal(mode: SandboxMode, reason: str) -> ToolResult:
    """Return a pre-execution refusal without claiming the sandbox was used."""
    return ToolResult(
        success=False,
        exit_code=125,
        error=reason,
        sandbox_mode=mode,
        sandbox_enforcement=SandboxEnforcement.UNUSABLE,
    )


__all__ = ["EvaluateModel", "EvaluateModelArguments"]
