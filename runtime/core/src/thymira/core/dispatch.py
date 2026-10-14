"""Execution dispatch seams for running the composed runtime graph."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from thymira.core.execution_review import (
    INTERVIEW_LIMIT_RESUME_TARGET,
    PLAN_RESUME_TARGET,
    RISK_UNCERTAINTY_RESUME_TARGET,
)
from thymira.core.graph.state import initial_runtime_state
from thymira.core.runtime_identity import (
    RuntimeIdentity,
    process_runtime_identity,
    runtime_attachment_payload,
)
from thymira.observability import run_trace
from thymira.schemas import Actor, EventSurface, EventType, Id, Run

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from langchain_core.runnables import RunnableConfig
    from langgraph.graph.state import CompiledStateGraph
    from pydantic import BaseModel

    from thymira.observability import Handle
    from thymira.schemas import Event
    from thymira.state import LocalRunStore

type GraphFactory = Callable[[Run], "CompiledStateGraph"]

# The durable worker's exactly-once guarantee (RA-CORE-11) requires a completed node's checkpoint
# to be durable *before* the next node runs; otherwise a crash at the node boundary resumes from an
# earlier checkpoint and re-executes the completed node (re-running its experiments/artifacts).
# LangGraph's default ``"async"`` durability persists checkpoints on a background thread without
# waiting, leaving exactly that window. ``"sync"`` makes the Pregel loop block on each checkpoint
# write, so a redelivered task always resumes from the last *committed* boundary.
_DURABILITY: Final = "sync"


@runtime_checkable
class ExecutionDispatcher(Protocol):
    """Submit or resume one persisted Run through an execution implementation."""

    def submit(self, run_id: Id) -> None:
        """Submit ``run_id`` for execution."""
        ...

    def resume(self, run_id: Id) -> None:
        """Resume ``run_id`` from its latest durable checkpoint."""
        ...


class ExecutionDispatchError(RuntimeError):
    """Raised when an inline execution cannot complete its graph invocation."""


def schedule_stopped_checkpoint(
    graph: CompiledStateGraph,
    config: RunnableConfig,
    run_id: Id,
    events: Sequence[Event],
) -> None:
    """Schedule the canonical continuation when a durable checkpoint has no next node."""
    snapshot = graph.get_state(config)
    if not snapshot.values:
        raise ValueError(f"run {run_id}: no resumable checkpoint exists")
    if not snapshot.next:
        graph.update_state(config, {}, as_node=_resume_node_from_events(events))


def _resume_node_from_events(events: Sequence[Event]) -> str:
    """Select the stopped graph node from the latest canonical resume transition."""
    for event in reversed(events):
        if event.type is not EventType.RUN_TRANSITIONED:
            continue
        if event.payload.get("command") != "resume":
            continue
        resume_from = event.payload.get("resume_from")
        if resume_from in {
            "information",
            INTERVIEW_LIMIT_RESUME_TARGET,
            RISK_UNCERTAINTY_RESUME_TARGET,
        }:
            # These reviews re-enter the interview body before continuing to the Gate.
            return "start"
        if resume_from in {"execution", "execution_start", PLAN_RESUME_TARGET}:
            # A plan review re-enters the existing execution-gate anchor to seed its approved plan.
            return "execution_gate"
        return "gate"
    return "gate"


def _append_runtime_attachment(
    store: LocalRunStore,
    run: Run,
    identity: RuntimeIdentity,
    schemas: Sequence[type[BaseModel]] | None,
) -> None:
    """Record which process generation actually entered this Run's graph."""
    events = store.events(run.id)
    started = next((event for event in events if event.type is EventType.RUN_STARTED), None)
    if started is None or started.hash is None:
        raise ValueError(f"run {run.id} has no hash-chained initial run.started event")
    store.append(
        run.id,
        EventType.RUNTIME_ATTACHED,
        Actor.system(),
        runtime_attachment_payload(
            identity,
            schemas=schemas,
            run_started_seq=started.seq,
            run_started_hash=started.hash,
        ),
        expected_version=None,
        subject_id=run.id,
        producer="thymira.core",
        producer_version="0.1",
        correlation_id=identity.generation_id,
        surface=EventSurface.LOG_ONLY,
    )


class InlineDispatcher:
    """Compile and invoke one Run's composition graph in the current process.

    The graph factory is supplied by the composition root so this seam does not construct THY,
    MIRA or Gate dependencies itself. A future queue-backed dispatcher can replace this class
    without changing the ``RunService`` call site.
    """

    def __init__(
        self,
        store: LocalRunStore,
        graph_factory: GraphFactory,
        *,
        runtime_identity: RuntimeIdentity | None = None,
        runtime_schemas: Sequence[type[BaseModel]] | None = None,
    ) -> None:
        self._store = store
        self._graph_factory = graph_factory
        self._runtime_identity = (
            process_runtime_identity() if runtime_identity is None else runtime_identity
        )
        self._runtime_schemas = runtime_schemas

    def submit(self, run_id: Id) -> None:
        """Run the persisted graph synchronously and let it persist lifecycle evidence."""
        try:
            run = self._store.get(run_id)
            graph = self._graph_factory(run)
            _append_runtime_attachment(
                self._store, run, self._runtime_identity, self._runtime_schemas
            )
            with _trace(run) as trace:
                final = graph.invoke(
                    initial_runtime_state(run),
                    {"configurable": {"thread_id": run.id}},
                    durability=_DURABILITY,
                )
                _record_outcome(trace, final)
        except Exception as exc:  # the execution boundary: any crash becomes a recorded run.failed
            # The cause travels on the message: `RunService.fail` records only `str(exc)`, and a
            # `run.failed` that says why is what the CLI, the API and MIRA read (bug-hunt C2).
            #
            # Every exception, not a chosen few: a THY defect raising `AttributeError`, `KeyError`
            # or `TypeError` used to escape this boundary, so `RunService` never recorded a
            # terminal event and the Run stayed RUNNING forever on an append-only log that can
            # never be corrected. `BaseException` (`KeyboardInterrupt`, `SystemExit`) still
            # propagates, and the cause is kept on the raised error.
            raise ExecutionDispatchError(f"run {run_id}: inline execution failed: {exc}") from exc

    def resume(self, run_id: Id) -> None:
        """Continue the persisted graph from the checkpoint owned by ``run_id``.

        Wrapped exactly like ``submit`` (bug-hunt: the two were asymmetric -- a resume-time
        ``ThyExecutionError`` or any other failure used to propagate raw instead of becoming a
        recorded ``RUN_FAILED``, leaving the Run a zombie with no terminal event).
        """
        try:
            run = self._store.get(run_id)
            graph = self._graph_factory(run)
            config: RunnableConfig = {"configurable": {"thread_id": run.id}}
            schedule_stopped_checkpoint(graph, config, run_id, self._store.events(run_id))
            _append_runtime_attachment(
                self._store, run, self._runtime_identity, self._runtime_schemas
            )
            with _trace(run, resumed=True) as trace:
                final = graph.invoke(None, config, durability=_DURABILITY)
                _record_outcome(trace, final)
        except Exception as exc:  # the execution boundary: any crash becomes a recorded run.failed
            # The cause travels on the message, exactly as in `submit` (bug-hunt C2), and the
            # catch is the same width for the same reason: on this path the human's approval is
            # already recorded and already spent, so an escaping `AttributeError` would leave a
            # Run nobody can resume and nobody can answer again.
            raise ExecutionDispatchError(f"run {run_id}: inline resume failed: {exc}") from exc

    def _resume_node(self, run_id: Id) -> str:
        """Select the stopped graph node from the recorded resume cause."""
        return _resume_node_from_events(self._store.events(run_id))


def _trace(run: Run, *, resumed: bool = False) -> AbstractContextManager[Handle]:
    """Open the Run's trace around the graph invocation, not around enqueueing it.

    The root belongs at the execution boundary: a dispatcher that only queues work would
    otherwise close an empty trace and leave the real observations parentless. The same wrap
    applies unchanged to a queue-backed dispatcher's own `graph.invoke`.

    The workspace commit goes on as the trace's `version`, so "did this regress after the last
    deploy" is a comparison in Langfuse rather than a correlation done by hand against
    `events.jsonl`. It is absent outside a git repository, and then simply not set.
    """
    return run_trace(
        run_id=run.id,
        session_id=run.session_id,
        project_id=run.project_id,
        prompt=run.prompt,
        resumed=resumed,
        version=run.git_commit,
    )


def _record_outcome(trace: Handle, final: object) -> None:
    """Put the Run's own result on the trace root, so the trace list is readable at a glance.

    Without this the root carries the prompt and nothing else, and a reviewer scanning the trace
    list would have to open every Run and walk down to the Gate to learn how it ended. The
    decision is read defensively: the graph returns a state mapping, and a shape this does not
    recognise is simply not reported rather than raising inside a dispatcher.
    """
    if not isinstance(final, Mapping):
        return
    decision = final.get("decision")
    findings = final.get("findings") or ()
    outcome: dict[str, object] = {"findings": len(findings)}
    # The graph returns `RuntimeState.model_dump(mode="python")`, so the decision arrives as a
    # plain mapping rather than a `PolicyDecision`; both shapes are read so a future change to
    # the dump mode cannot silently blank the trace's Output column.
    verdict = _field(decision, "decision")
    outcome["decision"] = getattr(verdict, "value", verdict)
    if reason := _field(decision, "reason"):
        outcome["reason"] = reason
    trace.update(output=outcome)


def _field(source: object, name: str) -> object:
    """Read one field from either a mapping or an object, or `None` when it is absent."""
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


__all__ = ["ExecutionDispatchError", "ExecutionDispatcher", "GraphFactory", "InlineDispatcher"]
