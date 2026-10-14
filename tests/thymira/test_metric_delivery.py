"""Metric completion validates content independently of the planner's suggested filename."""

from __future__ import annotations

import pytest

from thymira.schemas import ArtifactKind
from thymira.state import LocalArtifactStore
from thymira.thy.models import ArtifactRequirement


def test_semantic_metrics_accepts_changed_name_only_with_requested_content(tmp_path):
    from thymira.thy.artifact_delivery import resolve_requirement

    store = LocalArtifactStore(tmp_path, "run_" + "1" * 32)
    artifact = store.save_json(
        "metrics/observed.json",
        {
            "metrics": {"precision": 0.7, "recall": 0.6, "f1": 0.65, "roc_auc": 0.8},
            "model": {"sha256": "a" * 64},
        },
        produced_by="agent_" + "2" * 32,
        kind=ArtifactKind.METRICS,
        media_type="application/json",
    )
    required = ArtifactRequirement(
        name="evaluation",
        kind=ArtifactKind.METRICS,
        match="content",
        metric_keys=("precision", "recall", "f1", "roc_auc"),
        model_sha256="a" * 64,
    )
    assert resolve_requirement(required, store.list_active(), set(), store) == artifact


@pytest.mark.parametrize(
    "payload",
    [{"accuracy": 0.768}, {"precision": 0.7, "recall": 0.6, "f1": 0.65, "roc_auc": "not a number"}],
)
def test_correct_name_with_insufficient_metrics_is_not_completion(tmp_path, payload):
    from thymira.thy.artifact_delivery import resolve_requirement

    store = LocalArtifactStore(tmp_path, "run_" + "1" * 32)
    store.save_json(
        "model_metrics.json",
        payload,
        produced_by="agent_" + "2" * 32,
        kind=ArtifactKind.METRICS,
        media_type="application/json",
    )
    required = ArtifactRequirement(
        name="model_metrics.json",
        kind=ArtifactKind.METRICS,
        metric_keys=("precision", "recall", "f1", "roc_auc"),
    )
    assert resolve_requirement(required, store.list_active(), set(), store) is None


def test_identical_model_bytes_do_not_authorize_another_source_revision(tmp_path):
    from thymira.thy.artifact_delivery import resolve_requirement

    store = LocalArtifactStore(tmp_path, "run_" + "1" * 32)
    store.save_json(
        "metrics.json",
        {
            "metrics": {"f1": 0.8},
            "model": {"sha256": "a" * 64},
            "methodology": {"source_model_artifact_id": "artifact_" + "2" * 32},
        },
        produced_by="run_" + "1" * 32,
        kind=ArtifactKind.METRICS,
        media_type="application/json",
    )
    requirement = ArtifactRequirement(
        name="metrics.json",
        kind=ArtifactKind.METRICS,
        metric_keys=("f1",),
        model_sha256="a" * 64,
        source_model_artifact_id="artifact_" + "3" * 32,
    )
    assert resolve_requirement(requirement, store.list_active(), set(), store) is None
