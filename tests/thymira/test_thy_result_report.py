"""Task summaries describe required work without claiming every failed attempt was repaired."""

from thymira.events import InMemoryEventLog
from thymira.schemas import Actor, EventType, Task, TaskStatus, new_id
from thymira.thy.agents.coding import CodeResult
from thymira.thy.models import AgentTask, ThyAgentKind, ThyPhase
from thymira.thy.result_report import _work_summary


def test_completed_task_summary_preserves_failed_attempt_without_claiming_repair() -> None:
    run_id = new_id("run")
    task = Task(
        id=new_id("task"),
        run_id=run_id,
        agent_id=new_id("agent"),
        objective="Publish the planned report.",
        status=TaskStatus.COMPLETED,
        summary=CodeResult(stdout="done", exit_code=0).model_dump_json(),
    )
    planned = AgentTask(
        id="report",
        agent=ThyAgentKind.CODING,
        phase=ThyPhase.EXECUTE,
        instruction="Publish the planned report.",
    )
    log = InMemoryEventLog(run_id)
    log.append(
        EventType.TOOL_COMPLETED,
        actor=Actor.system(),
        payload={
            "task_id": task.id,
            "tool": "write_file",
            "status": "FAILED",
            "error": "an unrelated exploratory output was invalid",
        },
    )

    lines, _ = _work_summary(
        ((planned, task),),
        log.events(),
        {},
        run_id,
        [],
        [],
    )

    assert any("required task work was subsequently verified as complete" in line for line in lines)
    assert any("Individual attempts remain in the run evidence." in line for line in lines)
    assert not any("recovered at the task level" in line for line in lines)
