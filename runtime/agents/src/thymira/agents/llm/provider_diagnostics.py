"""Extract bounded, non-secret provider details from gateway failures."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from thymira.events import scrub_credentials

_REMOTE_CODES = frozenset(
    {
        "api_error",
        "authentication_error",
        "context_length_exceeded",
        "insufficient_quota",
        "invalid_api_key",
        "invalid_request_error",
        "model_not_found",
        "not_found",
        "overloaded_error",
        "permission_denied",
        "rate_limit_exceeded",
        "server_error",
        "unsupported_parameter",
        "unsupported_value",
    }
)
_PARAMETER_ROOTS = frozenset(
    {
        "functions",
        "logit_bias",
        "max_completion_tokens",
        "max_tokens",
        "messages",
        "metadata",
        "n",
        "parallel_tool_calls",
        "reasoning_effort",
        "response_format",
        "seed",
        "stop",
        "stream",
        "temperature",
        "tool_choice",
        "tools",
        "top_p",
    }
)
_PARAMETER_PATH = re.compile(
    r"^(?P<root>[a-z_]+)(?:\.(?:[A-Za-z_][A-Za-z0-9_-]{0,47})|"
    r"\[(?:[0-9]{1,3}|[A-Za-z_][A-Za-z0-9_-]{0,31})\]){0,12}$"
)
_REQUEST_ID = re.compile(
    r"^(?:req[-_][A-Za-z0-9][A-Za-z0-9._:-]{0,91}|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
    re.IGNORECASE,
)
_CREDENTIAL_LIKE_ID = re.compile(r"(?i)(?:\bsk-|\brk-|\bpk-|bearer|api[_-]?key|token)")
_MIN_HTTP_STATUS = 100
_MAX_HTTP_STATUS = 599
_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "anthropic-request-id",
    "x-goog-request-id",
    "x-ms-request-id",
)


@dataclass(frozen=True, slots=True)
class ProviderDiagnostics:
    """Only the four bounded fields safe to carry beyond the gateway adapter."""

    status: int | None = None
    code: str | None = None
    param: str | None = None
    request_id: str | None = None

    def summary(self) -> str | None:
        """Render only validated fields, or return ``None`` when no safe details exist."""
        details: list[str] = []
        if self.status is not None:
            details.append(f"status={self.status}")
        if self.code is not None:
            details.append(f"code={self.code}")
        if self.param is not None:
            details.append(f"parameter={self.param}")
        if self.request_id is not None:
            details.append(f"request_id={self.request_id}")
        return "; ".join(details) or None


def extract_provider_diagnostics(error: BaseException) -> ProviderDiagnostics:
    """Extract and revalidate allowlisted fields without reading arbitrary error text."""
    attributes = _attributes(error)
    response = attributes.get("response")
    response_attributes = _attributes(response)
    body = _mapping(attributes.get("body")) or _mapping(response_attributes.get("body"))
    remote_error = _mapping(body.get("error"))
    supplied = attributes.get("provider_diagnostics")
    supplied_fields = _diagnostic_fields(supplied)

    status = _pick_status(
        attributes.get("status_code"),
        response_attributes.get("status_code"),
        remote_error.get("status_code"),
        remote_error.get("status"),
        supplied_fields.get("status"),
    )
    code = _pick_code(
        attributes.get("code"),
        remote_error.get("code"),
        remote_error.get("type"),
        supplied_fields.get("code"),
    )
    param = _pick_param(
        attributes.get("param"),
        remote_error.get("param"),
        supplied_fields.get("param"),
    )
    request_id = _pick_request_id(
        attributes.get("request_id"),
        attributes.get("x_request_id"),
        remote_error.get("request_id"),
        supplied_fields.get("request_id"),
        *_header_request_ids(attributes.get("headers")),
        *_header_request_ids(response_attributes.get("headers")),
    )
    return ProviderDiagnostics(status=status, code=code, param=param, request_id=request_id)


def _diagnostic_fields(value: object) -> Mapping[str, object]:
    """Read only named fields from a typed diagnostic or a mapping for consumer revalidation."""
    if isinstance(value, ProviderDiagnostics):
        return {
            "status": value.status,
            "code": value.code,
            "param": value.param,
            "request_id": value.request_id,
        }
    return _mapping(value)


def _attributes(value: object) -> Mapping[str, object]:
    """Return an object's own attributes, excluding computed properties and methods."""
    try:
        return vars(value)
    except TypeError:
        return {}


def _mapping(value: object) -> Mapping[str, object]:
    """Return a mapping only when its keys are strings."""
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _scrubbed_string(value: object, *, limit: int) -> str | None:
    """Scrub known credentials before applying the field's length bound."""
    if not isinstance(value, str):
        return None
    scrubbed = scrub_credentials(value)
    if not scrubbed or len(scrubbed) > limit:
        return None
    return scrubbed


def _pick_status(*values: object) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            rendered = scrub_credentials(str(value))
        else:
            rendered = _scrubbed_string(value, limit=3)
        if rendered is None or not rendered.isdecimal():
            continue
        status = int(rendered)
        if _MIN_HTTP_STATUS <= status <= _MAX_HTTP_STATUS:
            return status
    return None


def _pick_code(*values: object) -> str | None:
    for value in values:
        code = _scrubbed_string(value, limit=48)
        if code is not None and code in _REMOTE_CODES:
            return code
    return None


def _pick_param(*values: object) -> str | None:
    for value in values:
        param = _scrubbed_string(value, limit=160)
        if param is None:
            continue
        match = _PARAMETER_PATH.fullmatch(param)
        if match is not None and match.group("root") in _PARAMETER_ROOTS:
            return param
    return None


def _pick_request_id(*values: object) -> str | None:
    for value in values:
        request_id = _scrubbed_string(value, limit=96)
        if (
            request_id is not None
            and _REQUEST_ID.fullmatch(request_id) is not None
            and _CREDENTIAL_LIKE_ID.search(request_id) is None
        ):
            return request_id
    return None


def _header_request_ids(headers: object) -> tuple[object, ...]:
    """Select values from known provider request-id headers only."""
    if not isinstance(headers, Mapping):
        return ()
    selected: dict[str, object] = {}
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() in _REQUEST_ID_HEADERS:
            selected.setdefault(key.lower(), value)
    return tuple(selected.get(name) for name in _REQUEST_ID_HEADERS if name in selected)
