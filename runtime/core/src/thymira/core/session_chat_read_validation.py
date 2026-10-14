"""Pure validation for the one recoverable Session read-proposal shape."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

if TYPE_CHECKING:
    from thymira.agents.llm.base import LLMResponse


class RecoverableEmptyGlobProposalError(RuntimeError):
    """A known-usage lookup whose sole schema error is an empty glob pattern."""

    code = "empty_glob_pattern"
    guidance = (
        "The glob lookup needs a non-empty filename pattern. Keep path literal and put wildcard "
        'syntax in pattern, for example `path="runs/<run_id>/artifacts", pattern="*.png"`.'
    )


def is_recoverable_empty_glob(response: LLMResponse, schema: type[Any]) -> bool:
    """Validate a placeholder copy to prove the empty glob pattern is the only defect."""
    if response.tool_calls:
        return False
    try:
        proposal = json.loads(response.text)
    except (TypeError, json.JSONDecodeError):
        return False
    return _valid_empty_glob_shape(proposal, schema)


def _valid_empty_glob_shape(proposal: object, schema: type[Any]) -> bool:
    """Prove every field except the missing pattern validates against the current schema."""
    if not isinstance(proposal, dict):
        return False
    lookup = proposal.get("lookup")
    if proposal.get("kind") != "lookup" or not isinstance(lookup, dict):
        return False
    if lookup.get("operation") != "glob":
        return False
    pattern = lookup.get("pattern")
    if "pattern" in lookup and pattern not in (None, ""):
        return False
    validation_copy = {**proposal, "lookup": {**lookup, "pattern": "*.png"}}
    try:
        schema.model_validate(validation_copy)
    except ValidationError:
        return False
    return True
