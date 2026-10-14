"""Read-only model discovery and verified input publication across conversation Runs.

Historical stores are read directly: constructing LocalArtifactStore could change their ACLs.
Only an approved source response, exact artifact revision and recorded training evidence can
become a new Run input. This copies bytes; it never loads an estimator or authorizes execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from thymira.core.conversation_response import approved_conversation_response
from thymira.events import canonical_json, sha256_bytes, verify_events
from thymira.schemas import Actor, Artifact, ArtifactKind, EventType, RunState
from thymira.schemas.model_input import ModelInput
from thymira.state import ArtifactWrite
from thymira.tools.builtins.experiment_repro import is_default_training_evidence

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.events import EventLog
    from thymira.schemas import ConversationTurn, Event, Run
    from thymira.state import ArtifactStore, EventStore

_MAX_INPUT_BYTES = 128 * 1024 * 1024
_MAX_MODELS = 16


def _contained(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact location escapes its source store")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("artifact location contains a filesystem link")
        if part == root:
            break
    return path


def read_manifest(root: Path, run_id: str) -> dict[str, Artifact]:
    """Read registered metadata without constructing a mutating store."""
    directory = _contained(root / "artifacts", run_id)
    path = _contained(directory, "manifest.json")
    if not path.exists():
        return {}
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("source artifact inventory exceeds its read budget")
    values = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: Artifact.model_validate_json(canonical_json(value)) for name, value in values.items()
    }


def read_artifact(root: Path, artifact: Artifact, *, max_bytes: int = _MAX_INPUT_BYTES) -> bytes:
    """Verify exact registered bytes; paths, IDs and logical names are never interchangeable."""
    directory = _contained(root / "artifacts", artifact.run_id)
    path = _contained(directory, artifact.uri)
    limit = min(max_bytes, _MAX_INPUT_BYTES)
    if limit < 1 or not artifact.valid or artifact.size_bytes > limit:
        raise ValueError("artifact is invalid or exceeds the input budget")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) != artifact.size_bytes or sha256_bytes(data) != artifact.sha256:
        raise ValueError("source artifact digest or size differs from its registered revision")
    return data


def _verified_trace(events: EventStore, run_id: str) -> tuple[Event, ...]:
    trace = tuple(events.read(run_id))
    if not verify_events(trace).valid or any(event.run_id != run_id for event in trace):
        raise ValueError("source Run evidence failed verification")
    return trace


def _approved_trace(run: Run, trace: tuple[Event, ...]) -> tuple[Event, ...]:
    transitions = [event for event in trace if event.type is EventType.RUN_TRANSITIONED]
    if not transitions:
        raise ValueError("source model has no approved terminal response")
    state = RunState.model_validate_json(canonical_json(transitions[-1].payload["state"]))
    if not approved_conversation_response(run, state, trace):
        raise ValueError("source model has no approved terminal response")
    digest = transitions[-1].payload.get("audit_terminal_hash")
    snapshot = next((event.seq for event in trace if event.hash == digest), None)
    if snapshot is None:
        raise ValueError("source model has no bound audit snapshot")
    return tuple(event for event in trace if event.seq <= snapshot)


def model_inputs_for_turns(
    root: Path, turns: Sequence[ConversationTurn], events: EventStore
) -> tuple[ModelInput, ...]:
    """Freeze up to sixteen approved model revisions, newest source Run first."""
    result: list[ModelInput] = []
    for turn in reversed(turns):
        if not turn.assistant_message:
            continue
        trace = _approved_trace(turn.run, _verified_trace(events, turn.run.id))
        manifest = read_manifest(root, turn.run.id)
        for artifact in manifest.values():
            if artifact.kind is not ArtifactKind.MODEL or not artifact.valid:
                continue
            reference = _model_input(artifact, manifest, trace)
            if reference is not None:
                result.append(reference)
        if len(result) >= _MAX_MODELS:
            return tuple(result[:_MAX_MODELS])
    return tuple(result)


def _model_input(
    model: Artifact, manifest: dict[str, Artifact], trace: Sequence[Event]
) -> ModelInput | None:
    if model.name is None:
        return None
    trained = [
        event
        for event in trace
        if event.type is EventType.MODEL_TRAINED
        and event.payload.get("model_artifact_id") == model.id
    ]
    experiments = [
        event.payload.get("experiment", {})
        for event in trace
        if event.type is EventType.EXPERIMENT_COMPLETED
    ]
    experiment = next(
        (value for value in experiments if value.get("model_artifact_id") == model.id), None
    )
    if len(trained) != 1 or experiment is None or trained[0].hash is None:
        return None
    parameters = experiment.get("parameters", {})
    dataset_name, target = parameters.get("dataset"), parameters.get("target_column")
    if not isinstance(dataset_name, str) or not isinstance(target, str):
        return None
    dataset = next(
        (item for item in manifest.values() if item.id == experiment.get("dataset_artifact_id")),
        None,
    )
    schema = manifest.get(f"datasets/{dataset_name}.schema.json")
    if dataset is None or schema is None or not dataset.valid or not schema.valid:
        return None
    for item in (model, dataset, schema):
        if item.run_id != model.run_id or not any(
            event.type is EventType.ARTIFACT_CREATED
            and event.payload.get("artifact_id") == item.id
            and event.payload.get("name") == item.name
            and event.payload.get("sha256") == item.sha256
            for event in trace
        ):
            raise ValueError("artifact metadata has no matching source event")
    return ModelInput(
        source_run_id=model.run_id,
        model_artifact_id=model.id,
        model_name=model.name,
        model_sha256=model.sha256,
        dataset_artifact_id=dataset.id,
        dataset_sha256=dataset.sha256,
        schema_artifact_id=schema.id,
        schema_sha256=schema.sha256,
        dataset_name=dataset_name,
        target_column=target,
        training_seq=trained[0].seq,
        training_hash=trained[0].hash,
    )


def materialize_model_inputs(
    root: Path,
    run: Run,
    store: ArtifactStore,
    log: EventLog,
    *,
    get_run: Callable[[str], Run],
    events: EventStore,
    selected_model_ids: tuple[str, ...] | None = None,
) -> None:
    """Publish scoped input copies idempotently, including original revision lineage."""
    if run.conversation is None:
        return
    available = {item.model_artifact_id for item in run.conversation.model_inputs}
    selected = available if selected_model_ids is None else set(selected_model_ids)
    if selected - available:
        raise ValueError("selected model is outside this request snapshot")
    for reference in run.conversation.model_inputs:
        if reference.model_artifact_id not in selected:
            continue
        source = get_run(reference.source_run_id)
        if source.project_id != run.project_id or source.session_id != run.session_id:
            raise ValueError("prior model belongs to another project or Session")
        if reference.source_run_id not in run.conversation.context_run_ids:
            raise ValueError("prior model is outside this request snapshot")
        trace = _approved_trace(source, _verified_trace(events, source.id))
        manifest = read_manifest(root, source.id)
        model = next((a for a in manifest.values() if a.id == reference.model_artifact_id), None)
        if model is None or not model.valid or _model_input(model, manifest, trace) != reference:
            raise ValueError("prior model reference is stale or invalid")
        _publish_input(root, reference, manifest, trace, store, log, run.id)


def _publish_input(
    root: Path,
    reference: ModelInput,
    manifest: dict[str, Artifact],
    trace: Sequence[Event],
    store: ArtifactStore,
    log: EventLog,
    run_id: str,
) -> None:
    by_id = {artifact.id: artifact for artifact in manifest.values()}
    model, dataset, schema = (
        by_id[identity]
        for identity in (
            reference.model_artifact_id,
            reference.dataset_artifact_id,
            reference.schema_artifact_id,
        )
    )
    suffix = Path(dataset.name or "").suffix.casefold()
    if suffix not in {".csv", ".parquet"}:
        raise ValueError("source dataset format is unsupported")
    data_name = f"datasets/{reference.dataset_alias}{suffix}"
    schema_name = f"datasets/{reference.dataset_alias}.schema.json"
    schema_value = json.loads(read_artifact(root, schema).decode("utf-8"))
    schema_value.update(name=reference.dataset_alias, artifact_name=data_name)
    schema_bytes = canonical_json(schema_value).encode("utf-8")
    training = next(event.payload for event in trace if event.seq == reference.training_seq)
    context = {
        "model_sha256": model.sha256,
        "dataset_sha256": dataset.sha256,
        "schema_sha256": sha256_bytes(schema_bytes),
        "target_column": reference.target_column,
        "seed": training.get("seed"),
        "split": training.get("split"),
        "training_kind": "default_logistic_regression"
        if is_default_training_evidence(training)
        else "unknown",
        "source_run_id": reference.source_run_id,
        "source_model_artifact_id": model.id,
    }
    writes = (
        ArtifactWrite(
            reference.model_alias,
            read_artifact(root, model),
            kind=ArtifactKind.MODEL,
            media_type=model.media_type,
            input_artifact_names=(data_name, schema_name),
        ),
        ArtifactWrite(
            data_name,
            read_artifact(root, dataset),
            kind=ArtifactKind.DATASET,
            media_type=dataset.media_type,
        ),
        ArtifactWrite(
            schema_name,
            schema_bytes,
            media_type="application/json",
            input_artifact_names=(data_name,),
        ),
        ArtifactWrite(
            reference.context_alias,
            canonical_json(context).encode("utf-8"),
            media_type="application/json",
            input_artifact_names=(reference.model_alias, data_name, schema_name),
        ),
    )
    missing = []
    for write in writes:
        current = store.get(write.name)
        if current is None:
            missing.append(write)
        else:
            inputs = [store.get(name) for name in write.input_artifact_names]
            if (
                not current.valid
                or current.sha256 != sha256_bytes(write.data)
                or current.kind is not write.kind
                or current.produced_by != run_id
                or any(item is None for item in inputs)
                or set(current.input_artifact_ids)
                != {item.id for item in inputs if item is not None}
                or sha256_bytes(store.load_bytes_bounded(write.name, _MAX_INPUT_BYTES))
                != current.sha256
            ):
                raise ValueError("a frozen prior input was overwritten or has different provenance")
    if missing:
        store.save_artifact_batch(tuple(missing), produced_by=run_id)
    sources = {
        reference.model_alias: [model.id],
        data_name: [dataset.id],
        schema_name: [schema.id],
        reference.context_alias: [model.id, dataset.id, schema.id],
    }
    for write in writes:
        current = store.get(write.name)
        if current is None:
            raise ValueError("prior input publication did not commit")
        if not any(
            event.type is EventType.ARTIFACT_CREATED
            and event.payload.get("artifact_id") == current.id
            for event in log.events()
        ):
            log.append(
                EventType.ARTIFACT_CREATED,
                Actor.system(),
                {
                    "artifact_id": current.id,
                    "name": current.name,
                    "sha256": current.sha256,
                    "source_run_id": reference.source_run_id,
                    "source_artifact_ids": sources[write.name],
                    "input_copy": True,
                },
                subject_id=current.id,
            )
