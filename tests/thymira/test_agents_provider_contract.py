"""Offline checks for the effective LiteLLM tool and structured-output contracts."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel

from tests.thymira.test_agents_llm import _fake_response
from thymira.agents.llm.litellm_provider import LiteLLMProvider
from thymira.agents.request_ledger import RequestLedger
from thymira.core.graph.agent_catalog import default_thy_catalog
from thymira.events import verify_events
from thymira.mira.verification import FindingVerdict
from thymira.schemas import EventType, new_id
from thymira.state.events import LocalEventStore
from thymira.thy.models import AgentTask, PlanOutput, ThyAgentKind, ThyPhase
from thymira.thy.nodes.plan import _catalog_plan_output_type, _plannable_agent_names

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def real_litellm(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Callable[..., Any]]:
    """Load the installed LiteLLM schema converter without updating its model-cost map."""
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    litellm = importlib.import_module("litellm")
    converter_module = importlib.import_module("litellm.llms.base_llm.base_utils")
    return litellm, converter_module.type_to_response_format_param


def test_catalog_structured_schemas_reach_chat_adapter_and_pinned_strict_converter(
    real_litellm: tuple[Any, Callable[..., Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    litellm, type_to_response_format_param = real_litellm
    catalog = default_thy_catalog()
    planner_schema = _catalog_plan_output_type(_plannable_agent_names(catalog))
    plan = PlanOutput(
        tasks=(
            AgentTask(
                id="task-1",
                agent=ThyAgentKind.CODING,
                phase=ThyPhase.INSPECT,
                instruction="Inspect the registered data.",
            ),
        )
    )
    schemas_and_outputs: tuple[tuple[str, type[BaseModel], str], ...] = (
        (
            "CodeResult",
            catalog.output_schema("coding"),
            '{"stdout":"done","exit_code":0,"artifacts":[]}',
        ),
        (
            "EvaluationResult",
            catalog.output_schema("evaluation"),
            '{"summary":"complete","metrics_artifact_id":null}',
        ),
        ("PlanOutput", planner_schema, plan.model_dump_json()),
        (
            "FindingVerdict",
            FindingVerdict,
            '{"supported":null,"rationale":"The record is incomplete."}',
        ),
    )
    captured: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        captured.append(kwargs)
        output = schemas_and_outputs[len(captured) - 1][2]
        return _fake_response(content=output, model="gpt-test")

    monkeypatch.setattr(litellm, "completion", completion, raising=False)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda **_kwargs: True, raising=False)
    provider = LiteLLMProvider(model="openai/gpt-test")

    assert provider.provider_name == "openai"
    for index, (name, schema, _output) in enumerate(schemas_and_outputs):
        parsed, _response = provider.complete_structured(
            f"Return a valid {name} response.", schema=schema
        )
        assert isinstance(parsed, schema)
        effective_request = captured[index]
        assert effective_request["model"] == "openai/gpt-test"
        assert isinstance(effective_request["messages"], list)
        assert effective_request["response_format"] is schema

        serialized = type_to_response_format_param(effective_request["response_format"])
        assert isinstance(serialized, Mapping)
        assert serialized["type"] == "json_schema"
        json_schema = serialized["json_schema"]
        assert isinstance(json_schema, Mapping)
        assert json_schema["strict"] is True
        effective_schema = json_schema["schema"]
        assert isinstance(effective_schema, Mapping)
        _assert_strict_objects(effective_schema)
        if name == "FindingVerdict":
            assert _contains_nullable_boolean(effective_schema)


@pytest.mark.parametrize(
    ("supported", "parameter"),
    [
        (["max_completion_tokens", "max_tokens"], "max_completion_tokens"),
        (["max_tokens"], "max_tokens"),
    ],
)
def test_bounded_structured_request_passes_a_supported_native_output_cap(
    real_litellm: tuple[Any, Callable[..., Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    supported: list[str],
    parameter: str,
) -> None:
    litellm, _ = real_litellm
    captured: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return _fake_response(content='{"answer":"ok"}', model="gpt-test")

    monkeypatch.setattr(litellm, "completion", completion, raising=False)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda **_kwargs: True, raising=False)
    monkeypatch.setattr(
        litellm.utils,
        "get_supported_openai_params",
        lambda **_kwargs: supported,
    )
    run_id = new_id("run")
    event_log = LocalEventStore(tmp_path).open(run_id)
    provider = LiteLLMProvider(
        model="openai/gpt-test",
        request_ledger=RequestLedger(event_log),
        request_owner_id="test-owner",
    )

    parsed, _ = provider.complete_structured_bounded(
        "Return a valid answer.",
        schema=_BoundedAnswer,
        max_output_tokens=321,
    )

    assert parsed.answer == "ok"
    assert captured[0][parameter] == 321
    assert not ({"max_completion_tokens", "max_tokens"} - {parameter}) & captured[0].keys()
    events = event_log.events()
    request = next(event for event in events if event.type is EventType.MODEL_REQUEST_RECORDED)
    gateway = request.payload["gateway"]
    assert gateway["settings"][parameter] == captured[0][parameter]
    assert verify_events(events).valid


class _BoundedAnswer(BaseModel):
    answer: str


def _assert_strict_objects(schema: object) -> None:
    """Check that LiteLLM's strict converter closes every object and requires its properties."""
    if isinstance(schema, Mapping):
        if schema.get("type") == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", ())
            assert isinstance(properties, Mapping)
            assert schema.get("additionalProperties") is False
            assert set(required) == set(properties)
        for value in schema.values():
            _assert_strict_objects(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_strict_objects(value)


def _contains_nullable_boolean(schema: object) -> bool:
    """Return whether the serialized tree preserves the verdict's boolean-or-null union."""
    if isinstance(schema, Mapping):
        alternatives = schema.get("anyOf")
        if isinstance(alternatives, list) and {item.get("type") for item in alternatives} == {
            "boolean",
            "null",
        }:
            return True
        return any(_contains_nullable_boolean(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_contains_nullable_boolean(value) for value in schema)
    return False
