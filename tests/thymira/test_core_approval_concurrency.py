"""Concurrent approval requests serialize against one Run's pending review."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from tests.thymira.test_core_execution_review import _review_run
from thymira.core import RunApprovalError, RunEventLog, RunService, SessionService
from thymira.events import verify_events
from thymira.policies import Gate, pending_approvals
from thymira.schemas import Actor, EventType, RunStatus
from thymira.state import LocalRunStore, LocalSessionRepository, OwnerBusyError

if TYPE_CHECKING:
    from multiprocessing.connection import Connection

    from thymira.policies.gate import ApprovalRequest


def _contend_for_review_lock(root: str, run_id: str, sender: Connection) -> None:
    store = LocalRunStore(Path(root))
    try:
        with store.review_lock(run_id):
            sender.send("acquired")
    except OwnerBusyError:
        sender.send("busy")
    finally:
        sender.close()


def test_only_one_concurrent_human_answer_reaches_a_run_event_chain(tmp_path: Path) -> None:
    review = _review_run(tmp_path)
    decision_id = pending_approvals(review.log.events())[0].decision_id
    reviewer = Actor(kind="human", id="reviewer", authenticated=True)
    entered_approver = Event()
    release_approver = Event()

    def wait_then_approve(_request: ApprovalRequest) -> bool:
        entered_approver.set()
        if not release_approver.wait(timeout=10):
            raise TimeoutError("test approver was not released")
        return True

    approve_gate = Gate(
        review.gate.engine,
        RunEventLog(review.store, review.run.id),
        approver=wait_then_approve,
        human=reviewer,
    )
    second_store = LocalRunStore(tmp_path / "runs")
    second_service = RunService(
        second_store,
        SessionService(LocalSessionRepository(tmp_path / "sessions")),
    )
    reject_gate = Gate(
        review.gate.engine,
        RunEventLog(second_store, review.run.id),
        approver=lambda _request: False,
        human=reviewer,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        winner = executor.submit(
            review.service.resolve_approval,
            review.run.id,
            gate=approve_gate,
            actor=reviewer,
            expected_decision_id=decision_id,
        )
        assert entered_approver.wait(timeout=10)
        try:
            with pytest.raises(RunApprovalError, match="already being resolved"):
                second_service.resolve_approval(
                    review.run.id,
                    gate=reject_gate,
                    actor=reviewer,
                    expected_decision_id=decision_id,
                )
        finally:
            release_approver.set()

        resolved = winner.result(timeout=20)

    events = review.store.events(review.run.id)
    answers = [event for event in events if event.type is EventType.HUMAN_APPROVAL]
    assert resolved.status is RunStatus.COMPLETED
    assert len(answers) == 1
    assert answers[0].payload["decision_id"] == decision_id
    assert answers[0].payload["approved"] is True
    assert verify_events(events).valid


def test_review_lock_rejects_a_second_process(tmp_path: Path) -> None:
    review = _review_run(tmp_path)
    context = get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_contend_for_review_lock,
        args=(str(tmp_path / "runs"), review.run.id, sender),
    )

    with review.store.review_lock(review.run.id):
        process.start()
        sender.close()
        try:
            assert receiver.poll(10), "the child process did not report its lock result"
            assert receiver.recv() == "busy"
        finally:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
            receiver.close()

    assert process.exitcode == 0
