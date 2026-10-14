"""Tool-capable structured retry feedback and exhausted-turn behavior."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, Tool

from tests.thymira.fixtures_tools import FakeTool, review_gated_context
from thymira.agents import (
    AgentCatalog,
    AgentContext,
    AgentEndReason,
    AgentRunner,
    AgentSpec,
    LLMToolCall,
    RunUsage,
    ScriptedProvider,
)
from thymira.agents.llm.base import LLMMessage, LLMResponse, LLMToolDefinition
from thymira.agents.llm.routing import Role
from thymira.agents.model_binding import routed_model
from thymira.agents.request_ledger import RequestLedger
from thymira.events import InMemoryEventLog
from thymira.mira.checks import replay_request_ledger
from thymira.policies import ToolCapability
from thymira.schemas import EventType, ModelRoutePolicy, Task, TaskStatus, new_id
from thymira.thy.agents.coding import CodeResult
from thymira.thy.agents.evaluation import EvaluationResult
from thymira.tools import ToolRegistry

TEST_ROUTE_POLICY = ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests")


class _MappingResult(BaseModel):
    """Output fixture whose dynamic keys and values must not enter retry feedback."""

    metrics: dict[str, int]


@pytest.fixture(autouse=True)
def _configured_test_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind this provider seam to an explicit offline model route."""
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")


class _MeteredScriptedProvider(ScriptedProvider):
    """Attach deterministic nonzero usage to successful and invalid provider responses."""

    def complete_turn(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMToolDefinition] = (),
        parallel_tool_calls: bool | None = None,
    ) -> LLMResponse:
        response = super().complete_turn(
            messages, tools=tools, parallel_tool_calls=parallel_tool_calls
        )
        self.calls[-1]["messages"] = [message.model_dump(mode="json") for message in messages]
        return response.model_copy(update={"input_tokens": 7, "output_tokens": 3, "cost_usd": 0.04})


@pytest.mark.parametrize(
    (
        "schema",
        "invalid_arguments",
        "expected_output",
        "field",
        "error_code",
        "private_markers",
    ),
    [
        pytest.param(
            CodeResult,
            {"stdout": "complete"},
            CodeResult(stdout="complete", exit_code=0),
            "exit_code",
            "missing",
            (),
            id="code-result-missing-field",
        ),
        pytest.param(
            CodeResult,
            {"stdout": "complete", "exit_code": "wrong-type-marker"},
            CodeResult(stdout="complete", exit_code=0),
            "exit_code",
            "int_parsing",
            ("wrong-type-marker",),
            id="code-result-wrong-type",
        ),
        pytest.param(
            EvaluationResult,
            {"metrics_artifact_id": None},
            EvaluationResult(summary="Evaluation completed", metrics_artifact_id=None),
            "summary",
            "missing",
            (),
            id="evaluation-result-missing-field",
        ),
        pytest.param(
            _MappingResult,
            {"metrics": {"private_mapping_key_marker": "synthetic-credential-marker"}},
            _MappingResult(metrics={"corrected": 1}),
            "metrics.<key>",
            "int_parsing",
            ("private_mapping_key_marker", "synthetic-credential-marker"),
            id="dynamic-mapping-key-and-value-are-omitted",
        ),
    ],
)
def test_tool_capable_structured_retry_preserves_field_feedback(
    schema: type[BaseModel],
    invalid_arguments: dict[str, object],
    expected_output: BaseModel,
    field: str,
    error_code: str,
    private_markers: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-credential-marker")
    provider = _MeteredScriptedProvider(
        [
            LLMToolCall(
                id="invalid-result",
                name="final_result",
                arguments=invalid_arguments,
            ),
            LLMToolCall(
                id="valid-result",
                name="final_result",
                arguments=expected_output.model_dump(mode="json"),
            ),
        ]
    )
    log = InMemoryEventLog(new_id("run"))
    ledger = RequestLedger(log)
    usage = RunUsage()
    model = routed_model(
        Role.AGENT,
        "code",
        log,
        provider=provider,
        output_schema=schema,
        request_ledger=ledger,
        usage=usage,
        route_policy=TEST_ROUTE_POLICY,
    )

    def noop() -> str:
        return "unused"

    result = Agent(
        model=model,
        tools=[Tool(noop, name="noop")],
        output_type=schema,
        retries=1,
    ).run_sync("Finish the task.")

    assert result.output == expected_output
    assert len(provider.calls) == 2
    assert usage.requests == 2
    assert usage.input_tokens == 14
    assert usage.output_tokens == 6
    assert usage.cost_usd == pytest.approx(0.08)
    outgoing_retry_messages = provider.calls[1]["messages"]
    outgoing_retry_text = json.dumps(outgoing_retry_messages, sort_keys=True)
    assert field in outgoing_retry_text
    assert error_code in outgoing_retry_text
    for private_marker in private_markers:
        assert private_marker not in outgoing_retry_text
    replay = replay_request_ledger(tuple(log.events()))
    assert len(replay.requests) == 2
    persisted_feedback = "\n".join(
        message.content for message in replay.requests[1].gateway.messages
    )
    assert field in persisted_feedback
    assert error_code in persisted_feedback
    encoded_retry_request = json.dumps(
        [message.model_dump(mode="json") for message in replay.requests[1].gateway.messages],
        sort_keys=True,
    )
    for private_marker in private_markers:
        assert private_marker not in encoded_retry_request
    assert replay.responses[0].message is not None
    source_arguments = replay.responses[0].message.tool_calls[0].arguments
    if "synthetic-credential-marker" in private_markers:
        assert source_arguments == {
            "metrics": {"private_mapping_key_marker": "[REDACTED:CREDENTIAL]"}
        }
    else:
        assert source_arguments == invalid_arguments


def test_agent_runner_settles_repeated_invalid_tool_result_as_failed(
    tmp_path: Path,
) -> None:
    log = InMemoryEventLog(new_id("run"))
    tool_context = review_gated_context(tmp_path, run_id=log.run_id, log=log)
    task = Task(
        id=new_id("task"),
        run_id=log.run_id,
        agent_id=tool_context.agent_id,
        objective="Return a CodeResult.",
    )
    spec = AgentSpec(
        name="coding",
        role=Role.AGENT,
        task_kinds=("code",),
        tool_allowlist=("noop",),
        max_turns=2,
        max_depth=1,
        system_prompt_ref="coding",
        output_schema_ref="thymira.thy.agents.coding:CodeResult",
    )
    catalog = AgentCatalog(
        (spec,),
        system_prompts={"coding": "Return the execution result."},
        output_schemas={"coding": CodeResult},
    )
    usage = RunUsage()
    provider = _MeteredScriptedProvider(
        [
            LLMToolCall(id="invalid-first", name="final_result", arguments={"stdout": "first"}),
            LLMToolCall(id="invalid-second", name="final_result", arguments={"stdout": "second"}),
        ]
    )
    ledger = RequestLedger(log)

    result = AgentRunner().run(
        spec,
        task,
        AgentContext(
            catalog=catalog,
            event_log=log,
            provider=provider,
            tool_registry=ToolRegistry(
                (FakeTool("noop", ToolCapability(id="noop", external_effects=())),)
            ),
            tool_context=tool_context,
            request_ledger=ledger,
            route_policy=TEST_ROUTE_POLICY,
            usage=usage,
        ),
    )

    assert result.task_status is TaskStatus.FAILED
    assert result.output is None
    assert len(provider.calls) == 2
    assert usage.requests == 2
    assert usage.input_tokens == 14
    assert usage.output_tokens == 6
    assert usage.cost_usd == pytest.approx(0.08)
    retry_request = json.dumps(provider.calls[1]["messages"], sort_keys=True)
    assert "exit_code" in retry_request
    assert "missing" in retry_request
    replay = replay_request_ledger(tuple(log.events()))
    assert len(replay.requests) == 2
    persisted_retry = json.dumps(
        [message.model_dump(mode="json") for message in replay.requests[1].gateway.messages],
        sort_keys=True,
    )
    assert "exit_code" in persisted_retry
    assert "missing" in persisted_retry
    events = log.events()
    assert not any(event.type is EventType.TOOL_STARTED for event in events)
    assert not any(event.type is EventType.HUMAN_APPROVAL_REQUESTED for event in events)
    completed = next(event for event in events if event.type is EventType.AGENT_COMPLETED)
    assert completed.payload["status"] == TaskStatus.FAILED.value
    assert completed.payload["end_reason"] == AgentEndReason.MAX_TURNS.value
