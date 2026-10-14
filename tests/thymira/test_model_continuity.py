"""Prior model snapshots keep identity, scope and bytes across conversation Runs."""

from __future__ import annotations

import pytest

from tests.thymira.test_api_sessions import _client, _complete, _send
from thymira.schemas import Actor, ArtifactKind, EventType

pytestmark = pytest.mark.integration


def _trained(deps, rid):
    store = deps.artifact_store_factory(rid)
    data = store.save_bytes(
        "datasets/sample.csv", b"x,y\n1,a\n2,b\n", produced_by=rid, kind=ArtifactKind.DATASET
    )
    schema = store.save_json(
        "datasets/sample.schema.json",
        {
            "artifact_name": data.name,
            "name": "sample",
            "columns": ["x", "y"],
            "dtypes": ["Int64", "String"],
            "row_count": 2,
        },
        produced_by=rid,
    )
    model = store.save_bytes(
        "models/baseline.pkl",
        b"opaque-never-deserialized",
        produced_by=rid,
        kind=ArtifactKind.MODEL,
    )
    log = deps.event_store.open(rid)
    for artifact in (data, schema, model):
        log.append(
            EventType.ARTIFACT_CREATED,
            Actor.system(),
            {"artifact_id": artifact.id, "name": artifact.name, "sha256": artifact.sha256},
        )
    log.append(
        EventType.MODEL_TRAINED,
        Actor.system(),
        {
            "model_artifact_id": model.id,
            "seed": 7,
            "split": {"test_size": 0.25, "random_state": 7, "shuffle": True, "stratify": None},
            "training_script_sha256": "a" * 64,
        },
    )
    log.append(
        EventType.EXPERIMENT_COMPLETED,
        Actor.system(),
        {
            "experiment": {
                "model_artifact_id": model.id,
                "dataset_artifact_id": data.id,
                "parameters": {"dataset": "sample", "target_column": "y"},
            }
        },
    )
    return model


def test_followup_freezes_approved_model_identity_and_survives_reconstruction(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    client, deps = _client(tmp_path)
    sid = client.post("/sessions", json={}).json()["id"]
    rid = _send(client, sid).json()["id"]
    model = _trained(deps, rid)
    _complete(deps, rid)
    next_id = _send(client, sid, "Evaluate the previous model", "next").json()["id"]
    current = deps.run_service.get_run(next_id)
    assert current.conversation.model_inputs[0].model_artifact_id == model.id
    assert current.conversation.model_inputs[0].model_sha256 == model.sha256
    _, rebuilt = _client(tmp_path)
    assert (
        rebuilt.run_service.get_run(next_id).conversation.model_inputs
        == current.conversation.model_inputs
    )


def test_unapproved_model_is_not_a_reusable_result(tmp_path, monkeypatch):
    from thymira.core import RunController, RunTransitionKind

    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    client, deps = _client(tmp_path)
    sid = client.post("/sessions", json={}).json()["id"]
    rid = _send(client, sid).json()["id"]
    _trained(deps, rid)
    RunController(deps.run_store).advance(rid, RunTransitionKind.CANCEL)
    next_id = _send(client, sid, "Evaluate a model", "next").json()["id"]
    assert deps.run_service.get_run(next_id).conversation.model_inputs == ()


def test_import_copies_exact_bytes_once_and_refuses_scope_changes(tmp_path, monkeypatch):
    from thymira.core.model_continuity import materialize_model_inputs, read_artifact
    from thymira.events import InMemoryEventLog
    from thymira.schemas import new_id

    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    client, deps = _client(tmp_path)
    sid = client.post("/sessions", json={}).json()["id"]
    rid = _send(client, sid).json()["id"]
    model = _trained(deps, rid)
    _complete(deps, rid)
    next_id = _send(client, sid, "Evaluate the previous model", "next").json()["id"]
    run = deps.run_service.get_run(next_id)
    store = deps.artifact_store_factory(next_id)
    log = InMemoryEventLog(next_id)
    kwargs = {"get_run": deps.run_store.get, "events": deps.event_store}
    materialize_model_inputs(tmp_path / "runtime", run, store, log, **kwargs)
    first = store.manifest()
    count = len(log.events())
    materialize_model_inputs(tmp_path / "runtime", run, store, log, **kwargs)
    assert store.manifest() == first
    assert len(log.events()) == count
    assert store.load_bytes(run.conversation.model_inputs[0].model_alias) == read_artifact(
        tmp_path / "runtime", model
    )
    with pytest.raises(ValueError, match="another project"):
        materialize_model_inputs(
            tmp_path / "runtime",
            run.model_copy(update={"project_id": new_id("project")}),
            store,
            log,
            **kwargs,
        )
    with pytest.raises(ValueError, match="another project or Session"):
        materialize_model_inputs(
            tmp_path / "runtime",
            run.model_copy(update={"session_id": new_id("session")}),
            store,
            log,
            **kwargs,
        )


def test_changed_source_bytes_cannot_be_reused(tmp_path, monkeypatch):
    from thymira.core.model_continuity import materialize_model_inputs
    from thymira.events import InMemoryEventLog

    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    client, deps = _client(tmp_path)
    sid = client.post("/sessions", json={}).json()["id"]
    rid = _send(client, sid).json()["id"]
    model = _trained(deps, rid)
    _complete(deps, rid)
    next_id = _send(client, sid, "Evaluate the previous model", "next").json()["id"]
    run = deps.run_service.get_run(next_id)
    (tmp_path / "runtime" / "artifacts" / rid / model.uri).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest or size"):
        materialize_model_inputs(
            tmp_path / "runtime",
            run,
            deps.artifact_store_factory(next_id),
            InMemoryEventLog(next_id),
            get_run=deps.run_store.get,
            events=deps.event_store,
        )


def test_identical_bytes_with_foreign_producer_are_not_relabelled_as_core_input(
    tmp_path, monkeypatch
):
    from thymira.core.model_continuity import materialize_model_inputs, read_artifact
    from thymira.events import InMemoryEventLog
    from thymira.schemas import new_id

    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    client, deps = _client(tmp_path)
    sid = client.post("/sessions", json={}).json()["id"]
    rid = _send(client, sid).json()["id"]
    model = _trained(deps, rid)
    _complete(deps, rid)
    next_id = _send(client, sid, "Evaluate previous model", "next").json()["id"]
    run = deps.run_store.get(next_id)
    store = deps.artifact_store_factory(next_id)
    log = InMemoryEventLog(next_id)
    reference = run.conversation.model_inputs[0]
    store.save_bytes(
        reference.model_alias,
        read_artifact(tmp_path / "runtime", model),
        produced_by=new_id("agent"),
        kind=ArtifactKind.MODEL,
    )
    with pytest.raises(ValueError, match="provenance"):
        materialize_model_inputs(
            tmp_path / "runtime",
            run,
            store,
            log,
            get_run=deps.run_store.get,
            events=deps.event_store,
        )
    assert not log.events()
