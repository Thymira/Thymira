"""Shared workspace paths and guidance for deterministic dataset profile artifacts."""

from __future__ import annotations

PROFILE_JSON_TEMPLATE = "profile/{dataset}.json"
PROFILE_DISTRIBUTIONS_TEMPLATE = "profile/{dataset}-distributions.png"
PROFILE_CORRELATION_TEMPLATE = "profile/{dataset}-correlation.png"
PROFILE_MIN_CORRELATION_COLUMNS = 2


def profile_json_path(dataset: str) -> str:
    """Return the deterministic JSON report path for a registered dataset profile."""
    return PROFILE_JSON_TEMPLATE.format(dataset=dataset)


def profile_distributions_path(dataset: str) -> str:
    """Return the deterministic distribution chart path for a registered dataset."""
    return PROFILE_DISTRIBUTIONS_TEMPLATE.format(dataset=dataset)


def profile_correlation_path(dataset: str) -> str:
    """Return the deterministic correlation chart path for a registered dataset."""
    return PROFILE_CORRELATION_TEMPLATE.format(dataset=dataset)


def profile_output_guidance() -> str:
    """Describe exactly which deterministic profile files a plan may require."""
    return (
        f"The profile_dataset tool always publishes `{PROFILE_JSON_TEMPLATE}`. With "
        f"include_plots=true it also publishes `{PROFILE_DISTRIBUTIONS_TEMPLATE}`. "
        f"With include_plots=true it also publishes `{PROFILE_CORRELATION_TEMPLATE}` only when "
        f"the dataset has at least "
        f"{PROFILE_MIN_CORRELATION_COLUMNS} numeric columns; do not require that conditional "
        "chart unless that condition is established."
    )


__all__ = [
    "PROFILE_CORRELATION_TEMPLATE",
    "PROFILE_DISTRIBUTIONS_TEMPLATE",
    "PROFILE_JSON_TEMPLATE",
    "PROFILE_MIN_CORRELATION_COLUMNS",
    "profile_correlation_path",
    "profile_distributions_path",
    "profile_json_path",
    "profile_output_guidance",
]
