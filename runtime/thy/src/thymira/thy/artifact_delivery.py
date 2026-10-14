"""Resolve declared deliverables by identity and independently verified content."""

from __future__ import annotations

import json
import math
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from thymira.events import sha256_bytes
from thymira.schemas import ArtifactKind
from thymira.tools import artifact_read_limit, resolve_artifact_media_type, validate_artifact_bytes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.schemas import Artifact
    from thymira.state import ArtifactStore
    from thymira.thy.models import ArtifactRequirement


def _artifact_for_requirement(
    name: str,
    kind: ArtifactKind,
    media_type: str | None,
    artifacts: Sequence[Artifact],
    used_ids: set[str],
) -> Artifact | None:
    """Resolve one delivery by exact file identity first, then by semantic typed kind."""
    try:
        required_media_type = resolve_artifact_media_type(name, media_type)
    except ValueError:
        return None
    exact = next(
        (
            artifact
            for artifact in artifacts
            if artifact.id not in used_ids
            and artifact.valid
            and artifact.name == name
            and (kind is ArtifactKind.OTHER or artifact.kind is kind)
            and _media_type_matches(required_media_type, artifact.media_type)
        ),
        None,
    )
    if exact is not None:
        return exact
    if (
        kind is ArtifactKind.OTHER
        or required_media_type is not None
        or "/" in name
        or "\\" in name
        or PurePosixPath(name).suffix
    ):
        return None

    candidates = [
        artifact
        for artifact in artifacts
        if artifact.id not in used_ids
        and artifact.kind is kind
        and artifact.valid
        and _media_type_matches(required_media_type, artifact.media_type)
    ]
    if not candidates:
        return None
    words = set(name.lower().replace("_", " ").split())
    aliases = {
        ArtifactKind.CODE: {"pipeline", "preprocess", "preprocessing", "encoder", "code"},
        ArtifactKind.MODEL: {"model", "classifier", "estimator"},
        ArtifactKind.METRICS: {"metric", "metrics", "evaluation", "summary"},
        ArtifactKind.REPORT: {"report", "analysis", "summary"},
    }.get(kind, set())
    ranked = sorted(
        candidates,
        key=lambda artifact: (
            -sum(1 for word in aliases if word in words and word in artifact.name.lower()),
            artifact.name,
        ),
    )
    score = sum(1 for word in aliases if word in words and word in ranked[0].name.lower())
    if score == 0 and len(candidates) != 1:
        return None
    return ranked[0]


def _media_type_matches(required: str | None, actual: str | None) -> bool:
    """Match MIME types case-insensitively while ignoring optional parameters."""
    if required is None:
        return True
    if actual is None:
        return False
    required_base = required.partition(";")[0].strip().casefold()
    actual_base = actual.partition(";")[0].strip().casefold()
    return required_base == actual_base


def _artifact_content_matches_requirement(  # noqa: PLR0911  # every mismatch fails closed
    artifact: Artifact,
    *,
    requirement_name: str,
    requirement_media_type: str | None,
    artifact_store: ArtifactStore | None,
) -> bool:
    """Revalidate typed bytes independently of the tool or plugin that published metadata."""
    try:
        required_media_type = resolve_artifact_media_type(requirement_name, requirement_media_type)
        actual_media_type = resolve_artifact_media_type(artifact.name, artifact.media_type)
    except ValueError:
        return False
    if artifact_store is None:
        return required_media_type is None
    if required_media_type is not None and actual_media_type != required_media_type:
        return False
    try:
        current = artifact_store.get(artifact.name)
        if (
            current is None
            or not current.valid
            or current.id != artifact.id
            or current.sha256 != artifact.sha256
        ):
            return False
        validation_media_type = required_media_type or actual_media_type
        data = artifact_store.load_bytes_bounded(
            artifact.name, artifact_read_limit(validation_media_type)
        )
        if sha256_bytes(data) != artifact.sha256:
            return False
        validate_artifact_bytes(artifact.name, data, validation_media_type)
    except (KeyError, OSError, ValueError):
        return False
    return True


def resolve_requirement(
    requirement: ArtifactRequirement,
    artifacts: Sequence[Artifact],
    used_ids: set[str],
    store: ArtifactStore | None,
) -> Artifact | None:
    """Accept an exact path, or one unambiguous typed content match explicitly declared by Plan."""
    if requirement.match == "content":
        candidates = [
            a for a in artifacts if a.valid and a.id not in used_ids and a.kind is requirement.kind
        ]
    else:
        exact = _artifact_for_requirement(
            requirement.name, requirement.kind, requirement.media_type, artifacts, used_ids
        )
        candidates = [exact] if exact is not None else []
    matches = [
        a
        for a in candidates
        if _artifact_content_matches_requirement(
            a,
            requirement_name=a.name if requirement.match == "content" else requirement.name,
            requirement_media_type=requirement.media_type,
            artifact_store=store,
        )
        and _metrics_match(a, requirement, store)
    ]
    return matches[0] if len(matches) == 1 else None


def _metrics_match(
    artifact: Artifact, requirement: ArtifactRequirement, store: ArtifactStore | None
) -> bool:
    if not any(
        (
            requirement.metric_keys,
            requirement.model_sha256,
            requirement.dataset_sha256,
            requirement.evaluation_split,
            requirement.source_run_id,
            requirement.source_model_artifact_id,
        )
    ):
        return True
    if store is None or artifact.kind is not ArtifactKind.METRICS:
        return False
    try:
        data = store.load_bytes_bounded(artifact.name, 1024 * 1024)
        payload = (
            json.loads(data.decode("utf-8")) if sha256_bytes(data) == artifact.sha256 else None
        )
    except (KeyError, OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    metrics = payload.get("metrics", payload)
    if not isinstance(metrics, dict) or any(
        isinstance(metrics.get(key), bool)
        or not isinstance(metrics.get(key), (int, float))
        or not math.isfinite(metrics[key])
        for key in requirement.metric_keys
    ):
        return False
    model = payload.get("model")
    dataset = payload.get("dataset")
    methodology = payload.get("methodology")
    return (
        (
            requirement.source_run_id is None
            or (
                isinstance(methodology, dict)
                and methodology.get("source_run_id") == requirement.source_run_id
            )
        )
        and (
            requirement.source_model_artifact_id is None
            or (
                isinstance(methodology, dict)
                and methodology.get("source_model_artifact_id")
                == requirement.source_model_artifact_id
            )
        )
        and (
            requirement.model_sha256 is None
            or (isinstance(model, dict) and model.get("sha256") == requirement.model_sha256)
        )
        and (
            requirement.dataset_sha256 is None
            or (isinstance(dataset, dict) and dataset.get("sha256") == requirement.dataset_sha256)
        )
        and (
            requirement.evaluation_split is None
            or (
                isinstance(methodology, dict)
                and methodology.get("split") == requirement.evaluation_split
            )
        )
    )
