"""Real agent/provider/tool contracts with an offline LiteLLM gateway boundary."""

from __future__ import annotations

import traceback
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from tests.thymira.fixtures_tools import human_approved_context
from tests.thymira.test_agents_llm import (
    AuthenticationError,
    BadRequestError,
    Timeout,
    _fake_response,
)
from tests.thymira.test_agents_llm import (
    fake_litellm as fake_litellm,  # noqa: PLC0414  # pytest fixture re-export
)
from thymira.agents import AgentContext, AgentRunner, LiteLLMProvider
from thymira.agents.delegation import Delegator
from thymira.agents.llm import LLMCallError
from thymira.agents.usage import RunUsage
from thymira.schemas import EventType, ModelRoutePolicy, Task, TaskStatus, new_id
from thymira.thy.agents.coding import CODING_AGENT_SPEC, coding_agent_catalog
from thymira.tools.builtins.python_arguments import RunPythonOutputArtifact
from thymira.tools.builtins.run_python import builtins_registry

if TYPE_CHECKING:
    import types
    from pathlib import Path


def _set_exception_attributes(error: BaseException, **attributes: object) -> None:
    for name, value in attributes.items():
        setattr(error, name, value)


def _set_fake_response_cost(fake_litellm: types.ModuleType, cost_usd: float = 0.0125) -> None:
    fake_litellm.__dict__["model_cost"] = {"gpt-test": {}}
    fake_litellm.__dict__["completion_cost"] = lambda **_kwargs: cost_usd


@pytest.fixture(autouse=True)
def configured_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "gpt-test")


def _context(tmp_path: Path, provider: LiteLLMProvider) -> AgentContext:
    context = human_approved_context(tmp_path, run_id=new_id("run"))
    return AgentContext(
        route_policy=ModelRoutePolicy.from_routes(("gpt-test",), authority="code-owned-tests"),
        catalog=coding_agent_catalog(),
        event_log=context.event_log,
        provider=provider,
        tool_registry=builtins_registry(),
        tool_context=context,
    )


def test_coding_schema_reaches_gateway_without_root_combinators(
    tmp_path: Path, fake_litellm: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = []

    def completion(**kwargs: Any) -> Any:
        captured.extend(kwargs["tools"])
        for tool in kwargs["tools"]:
            schema = tool["function"]["parameters"]
            if set(schema) & {"oneOf", "anyOf", "allOf", "not"}:
                raise BadRequestError("invalid function parameters: root composition")
        return _fake_response(content='{"stdout":"","exit_code":0,"artifacts":[]}')

    monkeypatch.setattr(fake_litellm, "completion", completion, raising=False)
    context = _context(tmp_path, LiteLLMProvider(model="gpt-test"))
    task = Task(
        id=new_id("task"),
        run_id=context.event_log.run_id,
        agent_id=new_id("agent"),
        objective="Describe the available code tools",
    )
    result = AgentRunner().run(CODING_AGENT_SPEC, task, context)

    assert result.task_status is TaskStatus.COMPLETED
    python_tool = next(t for t in captured if t["function"]["name"] == "run_python")
    tool_by_name = {tool["function"]["name"]: tool for tool in captured}
    python_schema = python_tool["function"]["parameters"]
    assert {"code", "source_artifact_id"} <= set(python_schema["properties"])
    assert {"output_artifacts", "output_manifest_path"} <= set(python_schema["properties"])
    assert "reuse_artifact_id" in str(python_schema)
    assert {"read_file", "write_file", "final_result"} <= set(tool_by_name)
    assert "strict" not in python_tool["function"]
    assert "output_declarations" not in str(python_schema)
    assert "publication_progress" not in str(python_schema)
    schema_nodes = [python_schema]
    while schema_nodes:
        node = schema_nodes.pop()
        if isinstance(node, dict):
            schema_nodes.extend(value for value in node.values() if isinstance(value, (dict, list)))
            properties = node.get("properties")
            if isinstance(properties, dict) and "reuse_artifact_id" in properties:
                reuse_parent, reuse_schema = node, properties["reuse_artifact_id"]
                break
        elif isinstance(node, list):
            schema_nodes.extend(node)
    else:
        pytest.fail("effective run_python request omitted the reuse_artifact_id schema")
    assert "reuse_artifact_id" not in reuse_parent.get("required", ())
    assert isinstance(reuse_schema, dict)
    assert {
        item.get("type") for item in reuse_schema.get("anyOf", ()) if isinstance(item, dict)
    } >= {"string", "null"}
    assert RunPythonOutputArtifact.model_validate({"path": "report.txt"}).reuse_artifact_id is None
    assert (
        RunPythonOutputArtifact.model_validate(
            {"path": "report.txt", "reuse_artifact_id": None}
        ).reuse_artifact_id
        is None
    )
    assert RunPythonOutputArtifact.model_validate(
        {"path": "report.txt", "reuse_artifact_id": new_id("artifact")}
    ).reuse_artifact_id
    assert context.event_log.verify().valid


@pytest.mark.parametrize("error_type", [BadRequestError, AuthenticationError, Timeout])
@pytest.mark.parametrize("with_tools", [True, False])
def test_provider_failure_settles_once_with_provider_diagnostic(
    tmp_path: Path,
    fake_litellm: types.ModuleType,
    error_type: type[Exception],
    with_tools: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def completion(**kwargs: Any) -> Any:
        del kwargs
        raise error_type("private-provider-body-must-not-be-recorded")

    monkeypatch.setattr(fake_litellm, "completion", completion, raising=False)
    context = _context(tmp_path, LiteLLMProvider(model="gpt-test"))
    spec = (
        CODING_AGENT_SPEC
        if with_tools
        else CODING_AGENT_SPEC.model_copy(update={"tool_allowlist": ()})
    )
    settlement = Delegator(context).delegate("thy", spec, "Create the requested heatmap", depth=0)
    events = context.event_log.events()
    completed = [e for e in events if e.type is EventType.AGENT_COMPLETED]
    requests = [e for e in events if e.type is EventType.MODEL_REQUEST_RECORDED]

    assert len(requests) == 1
    assert settlement.task.status is TaskStatus.FAILED
    assert completed[-1].payload["end_reason"] == "provider_error"
    assert "provider" in (settlement.task.error or "").lower()
    assert "max_turns" not in (settlement.task.error or "")
    assert "private-provider-body" not in str([e.payload for e in events])
    assert not any(e.type is EventType.TOOL_STARTED for e in events)
    assert context.event_log.verify().valid


def test_structured_capability_refusal_terminates_before_gateway_dispatch(
    tmp_path: Path, fake_litellm: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(fake_litellm, "supports_response_schema", lambda **_kwargs: False)
    monkeypatch.setattr(
        fake_litellm,
        "completion",
        lambda **kwargs: gateway_calls.append(kwargs),
        raising=False,
    )
    context = _context(tmp_path, LiteLLMProvider(model="gpt-test"))
    spec = CODING_AGENT_SPEC.model_copy(update={"tool_allowlist": ()})

    settlement = Delegator(context).delegate("thy", spec, "Return a code result", depth=0)
    events = context.event_log.events()
    completed = [event for event in events if event.type is EventType.AGENT_COMPLETED]

    assert settlement.task.status is TaskStatus.FAILED
    assert completed[-1].payload["end_reason"] == "provider_error"
    assert gateway_calls == []
    assert not any(event.type is EventType.TOOL_STARTED for event in events)
    assert context.event_log.verify().valid


def test_provider_failure_keeps_only_allowlisted_remote_diagnostics(
    tmp_path: Path,
    fake_litellm: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-provider-test-secret"  # noqa: S105  # deterministic test credential
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    remote_error = BadRequestError(f"private response body contains {secret}")
    _set_exception_attributes(
        remote_error,
        status_code=400,
        code="invalid_request_error",
        param="tools[0].function.parameters",
        request_id="req-provider-safe-42",
        body={"error": {"code": "invalid_request_error", "message": f"private {secret}"}},
    )

    def completion(**kwargs: Any) -> Any:
        del kwargs
        raise remote_error

    monkeypatch.setattr(fake_litellm, "completion", completion, raising=False)

    with pytest.raises(LLMCallError) as direct_error:
        LiteLLMProvider(model="gpt-test").complete("Create the requested heatmap")
    remote_traceback = "".join(traceback.format_exception(direct_error.value))
    assert direct_error.value.__cause__ is None
    assert secret not in remote_traceback
    assert "private response body" not in remote_traceback

    context = _context(tmp_path, LiteLLMProvider(model="gpt-test"))
    settlement = Delegator(context).delegate(
        "thy", CODING_AGENT_SPEC, "Create the requested heatmap", depth=0
    )
    events = context.event_log.events()
    completed = [event for event in events if event.type is EventType.AGENT_COMPLETED]
    observable = f"{settlement.task.error} {completed[-1].payload}"

    assert "status=400" in observable
    assert "code=invalid_request_error" in observable
    assert "parameter=tools[0].function.parameters" in observable
    assert "request_id=req-provider-safe-42" in observable
    assert secret not in observable
    assert "private response body" not in observable
    assert "private " not in observable
    assert completed[-1].payload["end_reason"] == "provider_error"
    assert context.event_log.verify().valid


@pytest.mark.parametrize(
    "invalid_arguments",
    [
        pytest.param('{"secret":"sk-malformed-private-secret"', id="malformed-json"),
        pytest.param('["sk-malformed-private-secret"]', id="non-object-json"),
    ],
)
def test_malformed_returned_tool_arguments_are_charged_before_bounded_repair(
    tmp_path: Path,
    fake_litellm: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    invalid_arguments: str,
) -> None:
    secret = "sk-malformed-private-secret"  # noqa: S105  # deterministic test credential
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    _set_fake_response_cost(fake_litellm)
    responses = [
        _fake_response(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="malformed-call",
                    function=SimpleNamespace(name="run_python", arguments=invalid_arguments),
                )
            ],
        ),
        _fake_response(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id="final-result",
                    function=SimpleNamespace(
                        name="final_result",
                        arguments='{"stdout":"recovered","exit_code":0,"artifacts":[]}',
                    ),
                )
            ],
        ),
    ]
    received: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        received.append(kwargs)
        return responses[len(received) - 1]

    monkeypatch.setattr(fake_litellm, "completion", completion, raising=False)
    context = _context(tmp_path, LiteLLMProvider(model="gpt-test"))
    usage = RunUsage()
    context.usage = usage
    task = Task(
        id=new_id("task"),
        run_id=context.event_log.run_id,
        agent_id=new_id("agent"),
        objective="Return a code result without running tools",
    )

    result = AgentRunner().run(CODING_AGENT_SPEC, task, context)
    events = context.event_log.events()
    response_events = [event for event in events if event.type is EventType.MODEL_RESPONSE_CHUNK]
    tool_starts = [event for event in events if event.type is EventType.TOOL_STARTED]
    completed = [event for event in events if event.type is EventType.AGENT_COMPLETED]

    assert result.task_status is TaskStatus.COMPLETED, {
        "usage": {
            "requests": usage.requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        },
        "agent_completion": [
            event.payload for event in events if event.type is EventType.AGENT_COMPLETED
        ],
    }
    assert len(received) == 2
    assert usage.requests == 2
    assert usage.input_tokens == 10
    assert usage.output_tokens == 6
    assert usage.cost_usd == pytest.approx(0.025)
    assert completed[-1].payload["cost_usd"] == pytest.approx(0.025)
    assert len(response_events) == 2
    assert response_events[0].payload["terminal"]["outcome"] == "parse_failure"
    assert response_events[0].payload["terminal"]["error_code"] == "tool_call_parse_failure"
    second_request = str(received[1]["messages"])
    assert "The model returned malformed tool-call arguments." in second_request
    assert "Return valid JSON object arguments for the requested tools." in second_request
    assert secret not in second_request
    assert tool_starts == []
    assert not any(event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in events)
    assert context.event_log.verify().valid


def test_malformed_returned_tool_arguments_stop_at_max_turns_and_charge_each_response(
    tmp_path: Path, fake_litellm: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_fake_response_cost(fake_litellm)
    responses = [
        _fake_response(
            content=None,
            tool_calls=[
                SimpleNamespace(
                    id=f"malformed-call-{index}",
                    function=SimpleNamespace(name="run_python", arguments="{invalid-json"),
                )
            ],
        )
        for index in range(2)
    ]
    received: list[dict[str, Any]] = []

    def completion(**kwargs: Any) -> Any:
        received.append(kwargs)
        return responses[len(received) - 1]

    monkeypatch.setattr(fake_litellm, "completion", completion, raising=False)
    context = _context(tmp_path, LiteLLMProvider(model="gpt-test"))
    usage = RunUsage()
    context.usage = usage
    task = Task(
        id=new_id("task"),
        run_id=context.event_log.run_id,
        agent_id=new_id("agent"),
        objective="Return a code result without running tools",
    )

    result = AgentRunner().run(
        CODING_AGENT_SPEC.model_copy(update={"max_turns": 2}),
        task,
        context,
    )
    events = context.event_log.events()
    response_events = [event for event in events if event.type is EventType.MODEL_RESPONSE_CHUNK]
    completed = [event for event in events if event.type is EventType.AGENT_COMPLETED]

    assert result.task_status is TaskStatus.FAILED
    assert len(received) == 2
    assert usage.requests == 2
    assert usage.input_tokens == 10
    assert usage.output_tokens == 6
    assert usage.cost_usd == pytest.approx(0.025)
    assert len(response_events) == 2
    assert all(event.payload["terminal"]["outcome"] == "parse_failure" for event in response_events)
    assert completed[-1].payload["end_reason"] == "max_turns"
    assert not any(event.type is EventType.TOOL_STARTED for event in events)
    assert not any(event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in events)
    assert completed[-1].payload["cost_usd"] == pytest.approx(0.025)
    assert context.event_log.verify().valid
