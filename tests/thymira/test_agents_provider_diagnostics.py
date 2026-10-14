"""Safe allowlisted diagnostics at the LiteLLM error boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from thymira.agents.failure_outcome import AgentEndReason, model_failure_details
from thymira.agents.llm import LLMCallError
from thymira.agents.llm.provider_diagnostics import extract_provider_diagnostics


def _set_exception_attributes(error: BaseException, **attributes: object) -> None:
    for name, value in attributes.items():
        setattr(error, name, value)


def test_provider_diagnostics_read_only_allowlisted_fields_from_error_and_headers() -> None:
    error = RuntimeError("private provider body and prompt must not be copied")
    _set_exception_attributes(
        error,
        status_code=422,
        body={
            "error": {
                "code": "unsupported_parameter",
                "param": "response_format.json_schema.schema",
                "message": "private response content",
            }
        },
    )
    _set_exception_attributes(
        error,
        response=SimpleNamespace(
            headers={"X-Request-Id": "req-header-42", "Authorization": "secret"}
        ),
    )

    diagnostics = extract_provider_diagnostics(error)

    assert diagnostics.status == 422
    assert diagnostics.code == "unsupported_parameter"
    assert diagnostics.param == "response_format.json_schema.schema"
    assert diagnostics.request_id == "req-header-42"
    assert diagnostics.summary() == (
        "status=422; code=unsupported_parameter; "
        "parameter=response_format.json_schema.schema; request_id=req-header-42"
    )


def test_provider_diagnostics_scrub_credentials_before_validating_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-provider-diagnostic-secret"  # noqa: S105  # deterministic test credential
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    error = RuntimeError(f"private body {secret}")
    _set_exception_attributes(
        error,
        request_id=f"req-{secret}-tail",
        status_code="400",
        code="invalid_request_error",
        param=f"tools.{secret}.parameters",
    )

    diagnostics = extract_provider_diagnostics(error)

    assert diagnostics.status == 400
    assert diagnostics.code == "invalid_request_error"
    assert diagnostics.param is None
    assert diagnostics.request_id is None
    assert secret not in (diagnostics.summary() or "")


def test_provider_diagnostics_reject_untrusted_or_oversized_fields_then_use_known_header() -> None:
    error = RuntimeError("private_provider_body must not become a request identifier")
    response = SimpleNamespace(headers={"X-Request-Id": "req-header-safe-42"})
    _set_exception_attributes(
        error,
        status_code="1234",
        code="c" * 49,
        param=f"tools.{('x' * 160)}",
        request_id="private_provider_body",
        x_request_id="r" * 97,
        response=response,
    )

    diagnostics = extract_provider_diagnostics(error)

    assert diagnostics.status is None
    assert diagnostics.code is None
    assert diagnostics.param is None
    assert diagnostics.request_id == "req-header-safe-42"
    assert "private_provider_body" not in (diagnostics.summary() or "")


@pytest.mark.parametrize(
    "request_id",
    [
        "req_provider-42",
        "550e8400-e29b-41d4-a716-446655440000",
    ],
)
def test_provider_diagnostics_accept_provider_request_id_shapes(request_id: str) -> None:
    error = RuntimeError("provider failure")
    _set_exception_attributes(error, request_id=request_id)

    assert extract_provider_diagnostics(error).request_id == request_id


def test_failure_consumer_revalidates_diagnostics_and_ignores_arbitrary_message() -> None:
    error = LLMCallError(
        "private provider body must not be copied",
        provider_diagnostics={
            "status": "400",
            "code": "arbitrary remote text",
            "param": "user supplied text",
            "request_id": "private id with spaces",
        },
    )

    details = model_failure_details(error)

    assert details["end_reason"] is AgentEndReason.PROVIDER_ERROR
    assert details["reason"].endswith("Provider response details: status=400.")
    assert "private provider body" not in details["reason"]
    assert "arbitrary remote text" not in details["reason"]
    assert "user supplied text" not in details["reason"]
