"""Approval and reconstruction do not replenish a task's model request budget."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.thymira.fixtures_tools import review_gated_context
from tests.thymira.test_agents_resume import _human_answer
from thymira.agents import AgentContext, AgentRunner, LLMToolCall, ScriptedProvider
from thymira.agents.request_budget import step_request_limits
from thymira.agents.request_ledger import RequestLedger
from thymira.agents.resume import load_pending_resume
from thymira.events import JsonlEventLog
from thymira.schemas import EventType, ModelRoutePolicy, StopReason, Task, TaskStatus, new_id
from thymira.thy.agents.coding import CODING_AGENT_SPEC, coding_agent_catalog
from thymira.tools.builtins.run_python import builtins_registry

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_approval_resumes_share_one_task_request_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(name, "test-model")
    context = review_gated_context(tmp_path, run_id=new_id("run"))
    spec = CODING_AGENT_SPEC.model_copy(update={"max_turns": 2})
    task = Task(
        id=new_id("task"),
        run_id=context.run_id,
        agent_id=context.agent_id,
        objective="Write two files",
    )
    resume = None
    results = []
    for index, response in enumerate(
        [
            LLMToolCall(
                id="first",
                name="write_file",
                arguments={
                    "path": "first.txt",
                    "content": "one",
                    "description": "Write first file",
                },
            ),
            LLMToolCall(
                id="second",
                name="write_file",
                arguments={
                    "path": "second.txt",
                    "content": "two",
                    "description": "Write second file",
                },
            ),
            LLMToolCall(
                id="final",
                name="final_result",
                arguments={"stdout": "", "exit_code": 0, "artifacts": []},
            ),
        ]
    ):
        result = AgentRunner().run(
            spec,
            task,
            AgentContext(
                route_policy=ModelRoutePolicy.from_routes(
                    ("test-model",), authority="code-owned-tests"
                ),
                catalog=coding_agent_catalog(),
                event_log=context.event_log,
                request_ledger=RequestLedger(context.event_log),
                provider=ScriptedProvider([response]),
                tool_registry=builtins_registry(),
                tool_context=context,
            ),
            resume=resume,
        )
        results.append(result)
        if index < 2:
            assert result.task_status is TaskStatus.PENDING
            request = next(
                e
                for e in reversed(context.event_log.events())
                if e.type is EventType.HUMAN_APPROVAL_REQUESTED
            )
            _human_answer(context, request.payload["decision_id"], approved=True)
            resume = load_pending_resume(
                context.artifact_store, context, task.agent_id, task_id=task.id
            ).resume
            assert resume is not None
    assert results[-1].task_status is TaskStatus.FAILED
    assert results[-1].stop_reason is StopReason.OUT_OF_ROOM
    assert sum(e.type is EventType.MODEL_REQUEST_RECORDED for e in context.event_log.events()) == 2
    assert (context.workspace / "second.txt").read_text() == "two"
    assert context.event_log.verify().valid


def test_task_budget_survives_log_reopen_and_surface_replacement(tmp_path: Path) -> None:
    run_id = new_id("run")
    task = Task(id=new_id("task"), run_id=run_id, agent_id=new_id("agent"), objective="Analyze")
    log_path = tmp_path / "events.jsonl"
    log = JsonlEventLog(log_path, run_id)
    ledger = RequestLedger(log)
    owner = f"{task.id}:{task.agent_id}"
    for request_owner in (owner, owner, "another-task:another-agent"):
        ledger.record_gateway_request(
            "test-model", [{"role": "user", "content": "Proceed"}], {}, owner_id=request_owner
        )
    ledger.replace_surface((), owner_id=owner, reason="Test compacted conversation")
    reopened = JsonlEventLog(log_path, run_id)
    spec = CODING_AGENT_SPEC.model_copy(update={"max_turns": 3})
    assert step_request_limits(spec, task, reopened).request_limit == 1
    assert reopened.verify().valid
