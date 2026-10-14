"""Production checkpoint scheduling with real durable ownership and transition evidence."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from thymira.core import LifecycleGraphExecutor, LiveOwnerContext, RunController, RunTransitionKind
from thymira.core.runtime_identity import capture_runtime_identity
from thymira.events import verify_events
from thymira.schemas import Actor, EventType, ExecutionOutcomeKind, Run, Session, WorkItem, new_id
from thymira.state import (
    LocalLifecycleRepository,
    LocalRunStore,
    LocalSessionRepository,
    StaleOwnerError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from thymira.core.dispatch import GraphFactory


def test_control_apply_schedules_stopped_plan_checkpoint_under_live_owner(tmp_path: Path) -> None:
    """A canonical plan continuation schedules THY without losing the fenced writer."""
    store = LocalRunStore(tmp_path / "runs")
    repository = LocalLifecycleRepository(
        tmp_path,
        run_store=store,
        session_repository=LocalSessionRepository(tmp_path / "repositories"),
    )
    session = Session(id=new_id("session"), project_id=new_id("project"), client="test")
    run = Run(
        id=new_id("run"),
        session_id=session.id,
        project_id=session.project_id,
        prompt="Resume an already approved plan.",
    )
    work = WorkItem(run_id=run.id, ordinal=0, kind="control.apply", idempotency_key="plan-resume")
    publication = repository.commit_publication(repository.prepare_publication(session, run, work))
    publication.close()
    token = repository.acquire_owner(run.id, "plan-resume-worker")
    assert token is not None
    owner = repository.run_handle(token)
    controller = RunController(store, writer=owner.append_transition)
    controller.advance(run.id, RunTransitionKind.START)
    controller.pause_owned(run.id)
    controller.advance(run.id, RunTransitionKind.RESUME, payload={"resume_from": "plan"})
    context = LiveOwnerContext()
    calls: list[str] = []

    class StoppedGraph:
        """Expose the graph scheduling seam while using the real owner for every write."""

        def get_state(self, config: object) -> SimpleNamespace:
            assert config == {"configurable": {"thread_id": run.id}}
            assert context.for_run(run.id) is owner
            return SimpleNamespace(values={"run_id": run.id}, next=())

        def update_state(self, config: object, values: object, *, as_node: str) -> None:
            assert config == {"configurable": {"thread_id": run.id}}
            assert values == {}
            assert as_node == "execution_gate"
            live = context.for_run(run.id)
            assert live is owner
            live.append(EventType.AGENT_MESSAGE, Actor.system(), {"scheduled_from": as_node})
            calls.append("schedule")

        def invoke(self, state: object, config: object, *, durability: str) -> dict[str, object]:
            assert state is None
            assert config == {"configurable": {"thread_id": run.id}}
            assert durability == "sync"
            assert context.for_run(run.id) is owner
            calls.append("invoke")
            return {}

    def factory(_run: Run) -> StoppedGraph:
        assert context.for_run(run.id) is owner
        return StoppedGraph()

    executor = LifecycleGraphExecutor(
        repository,
        store,
        cast("GraphFactory", factory),
        context,
        runtime_identity=capture_runtime_identity(
            source_roots=(), modules=(), schemas=(), catalog_snapshot={}, distributions=()
        ),
    )
    try:
        result = executor.execute(work, owner)
    finally:
        owner.close()

    assert result.kind is ExecutionOutcomeKind.SUCCEEDED
    assert calls == ["schedule", "invoke"]
    events = store.events(run.id)
    assert any(event.payload.get("scheduled_from") == "execution_gate" for event in events)
    assert verify_events(events).valid
    with pytest.raises(StaleOwnerError):
        owner.append(EventType.AGENT_MESSAGE, Actor.system(), {"forged": True})
