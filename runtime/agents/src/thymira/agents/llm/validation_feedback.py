"""Bounded schema feedback without model values or custom validator exception messages."""

from __future__ import annotations

from typing import TYPE_CHECKING, get_args

from pydantic_core import ErrorType

from thymira.events import scrub_credentials

if TYPE_CHECKING:
    from pydantic import BaseModel, ValidationError

_SAFE_ERROR_CODES = frozenset(get_args(ErrorType))
_MAX_FEEDBACK_ERRORS = 8
_MAX_LOCATION_LENGTH = 160


def validation_feedback(error: ValidationError, schema: type[BaseModel]) -> str:
    """Report field locations and error codes, omitting raw inputs and exception contexts."""
    failures = []
    schema_json = schema.model_json_schema()
    for item in error.errors(include_url=False, include_context=False, include_input=False)[
        :_MAX_FEEDBACK_ERRORS
    ]:
        raw_location = _safe_location(item["loc"], schema_json)
        location = scrub_credentials(raw_location)[:_MAX_LOCATION_LENGTH] or "response"
        error_code = item["type"]
        safe_error_code = error_code if error_code in _SAFE_ERROR_CODES else "validation_error"
        failures.append(f"{location}: {safe_error_code}")
    return scrub_credentials("Schema validation errors: " + "; ".join(failures))


def _safe_location(location: tuple[object, ...], root: dict[str, object]) -> str:
    """Keep declared fields and sequence indices; replace arbitrary mapping keys with a token."""
    current: object = root
    rendered: list[str] = []
    for part in location:
        current_schema = _resolve_reference(current, root)
        if isinstance(part, int):
            items = current_schema.get("items")
            prefix_items = current_schema.get("prefixItems")
            if isinstance(items, dict):
                rendered.append(str(part))
                current = items
            elif (
                isinstance(prefix_items, list)
                and 0 <= part < len(prefix_items)
                and isinstance(prefix_items[part], dict)
            ):
                rendered.append(str(part))
                current = prefix_items[part]
            else:
                rendered.append("<key>")
                additional = current_schema.get("additionalProperties")
                current = additional if isinstance(additional, dict) else {}
            continue

        properties = current_schema.get("properties")
        if isinstance(part, str) and isinstance(properties, dict) and part in properties:
            rendered.append(part)
            current = properties[part]
        else:
            rendered.append("<key>")
            additional = current_schema.get("additionalProperties")
            current = additional if isinstance(additional, dict) else {}
    return ".".join(rendered)


def _resolve_reference(value: object, root: dict[str, object]) -> dict[str, object]:
    """Resolve generated local JSON Schema references without following external URLs."""
    current = value
    visited: set[str] = set()
    while isinstance(current, dict):
        reference = current.get("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return current
        if reference in visited:
            return current
        visited.add(reference)
        target: object = root
        for segment in reference[2:].split("/"):
            if not isinstance(target, dict):
                return current
            target = target.get(segment.replace("~1", "/").replace("~0", "~"))
        if not isinstance(target, dict):
            return current
        current = target
    return {}
