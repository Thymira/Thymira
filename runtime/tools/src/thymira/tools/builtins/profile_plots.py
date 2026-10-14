"""Render bounded descriptive charts from the deterministic profile's computed values."""

from __future__ import annotations

import json
from io import BytesIO
from itertools import pairwise
from math import ceil
from typing import TYPE_CHECKING, Any

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from thymira.schemas import ArtifactKind
from thymira.state import ArtifactWrite
from thymira.tools.models import ToolResult
from thymira.tools.profile_paths import (
    PROFILE_MIN_CORRELATION_COLUMNS,
    profile_correlation_path,
    profile_distributions_path,
    profile_json_path,
)
from thymira.tools.results import ProfileValue

if TYPE_CHECKING:
    from thymira.tools.models import ToolInvocation

_MAX_PLOT_COLUMNS = 12
_PLOT_GRID_COLUMNS = 3


def _png(figure: Figure) -> bytes:
    """Render without pyplot's shared state or a GUI backend."""
    FigureCanvasAgg(figure)
    output = BytesIO()
    figure.savefig(output, format="png", dpi=120, bbox_inches="tight")
    return output.getvalue()


def render_profile_plots(payload: dict[str, Any]) -> dict[str, bytes]:
    """Show distributions and available numeric correlations, with explicit column bounds."""
    columns = payload["columns"]
    selected = list(columns)[:_MAX_PLOT_COLUMNS]
    dataset = payload["dataset"]
    figure = Figure(figsize=(15, max(3, ceil(len(selected) / _PLOT_GRID_COLUMNS) * 3)))
    figure.suptitle(
        f"{payload['dataset']}: {payload['shape']['rows']} rows; "
        f"distributions for {len(selected)} of {len(columns)} selected columns"
    )
    axes = figure.subplots(max(1, ceil(len(selected) / _PLOT_GRID_COLUMNS)), 3, squeeze=False)
    for axis in axes.flat:
        axis.set_visible(False)
    for name, axis in zip(selected, axes.flat, strict=False):
        axis.set_visible(True)
        values = columns[name]
        histogram = values.get("histogram")
        if histogram and histogram["counts"]:
            edges, counts = histogram["edges"], histogram["counts"]
            widths = [right - left or 1 for left, right in pairwise(edges)]
            axis.bar(edges[:-1], counts, width=widths, align="edge")
        else:
            top = values["top_values"][:10]
            axis.barh([str(item[name]) for item in top], [item["count"] for item in top])
        axis.set_title(f"{name} (missing: {values['null_count']})")
        axis.tick_params(axis="both", labelsize=8)
    if not selected:
        figure.text(0.5, 0.5, "No selected columns", ha="center")
    figure.tight_layout()
    plots = {profile_distributions_path(dataset): _png(figure)}
    correlation = payload["correlation"]
    numeric = list(correlation)[:_MAX_PLOT_COLUMNS]
    if len(numeric) >= PROFILE_MIN_CORRELATION_COLUMNS:
        figure = Figure(figsize=(9, 8))
        axis = figure.subplots()
        matrix = [
            [
                value if value is not None else float("nan")
                for value in correlation[name][: len(numeric)]
            ]
            for name in numeric
        ]
        image = axis.imshow(matrix, cmap="coolwarm", vmin=-1, vmax=1)
        axis.set_xticks(range(len(numeric)), numeric, rotation=90)
        axis.set_yticks(range(len(numeric)), numeric)
        axis.set_title(f"Numeric correlations: {len(numeric)} of {len(correlation)} columns")
        figure.colorbar(image, ax=axis)
        figure.tight_layout()
        plots[profile_correlation_path(dataset)] = _png(figure)
    return plots


def publish_profile_plots(invocation: ToolInvocation, payload: dict[str, Any]) -> ToolResult:
    """Publish the unchanged profile and its charts together, with source lineage."""
    plots = render_profile_plots(payload)
    report_name = profile_json_path(payload["dataset"])
    text = json.dumps(payload, sort_keys=True)
    source = invocation.artifact_store.get(payload["source"]["artifact"])
    if source is None:
        raise ValueError("The registered profile source is no longer active")
    writes = [
        ArtifactWrite(
            name=report_name,
            data=text.encode("utf-8"),
            kind=ArtifactKind.REPORT,
            media_type="application/json",
            input_artifact_ids=(source.id,),
        ),
        *(
            ArtifactWrite(
                name=name,
                data=data,
                kind=ArtifactKind.PLOT,
                media_type="image/png",
                input_artifact_names=(report_name,),
            )
            for name, data in plots.items()
        ),
    ]
    artifacts = invocation.artifact_store.save_artifact_batch(
        writes, produced_by=invocation.agent_id
    )
    model_text = json.dumps({**payload, "plot_artifacts": list(plots)}, sort_keys=True)
    value = ProfileValue(
        text=model_text,
        dataset=payload["dataset"],
        profile_version=payload["profile_version"],
        source_artifact=payload["source"]["artifact"],
        source_sha256=payload["source"]["sha256"],
        schema_sha256=payload["source"]["schema_sha256"],
        rows=payload["shape"]["rows"],
        columns=payload["shape"]["columns"],
        artifact_id=artifacts[0].id,
        plot_artifacts=tuple(plots),
    )
    return ToolResult(
        success=True,
        stdout=model_text,
        value=value,
        artifact_ids=tuple(item.id for item in artifacts),
    )
