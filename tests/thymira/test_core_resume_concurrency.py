"""A plain resume cannot race publication of a human risk answer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import TYPE_CHECKING

import pytest

from tests.thymira.test_core_risk_interview import _complete_profile, _interview
from thymira.core import (
    MiraControlPlane,
    RunNotFoundError,
    RunNotResumableError,
    RunService,
    SessionService,
)
from thymira.core.risk_interview import RiskInterviewService
from thymira.events import verify_events
from thymira.policies import PolicyEngine, load_default_policy
from thymira.schemas import Actor, EventType, new_id
from thymira.state import LocalRunStore, LocalSessionRepository

if TYPE_CHECKING:
    from pathlib import Path

    from thymira.schemas import ActivityProfile, Id


class _Dispatcher:
    def __init__(self, store: LocalRunStore) -> None:
        self.store = store
        self.resumed: list[Id] = []

    def submit(self, run_id: Id) -> None:
        del run_id

    def resume(self, run_id: Id) -> None:
        # Reacquiring from the dispatcher proves callers released the publication lock first.
        with self.store.review_lock(run_id):
            self.resumed.append(run_id)


def test_resume_missing_run_keeps_the_public_not_found_error(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path / "runs")
    dispatcher = _Dispatcher(store)
    service = RunService(
        store,
        SessionService(LocalSessionRepository(tmp_path / "sessions")),
        dispatcher=dispatcher,
    )
    with pytest.raises(RunNotFoundError):
        service.resume(new_id("run"), actor=Actor.system())
    assert dispatcher.resumed == []


@pytest.mark.parametrize("pause_after_profile", [False, True])
def test_plain_resume_conflicts_while_an_answer_is_being_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pause_after_profile: bool
) -> None:
    run, interview, _, _, store = _interview(tmp_path, [])
    profile = _complete_profile(run).model_copy(update={"decision_effect": "undeclared"})
    question = interview.begin(run, profile).pending_question
    assert question is not None
    entered = Event()
    release = Event()
    record_profile = interview._record_profile

    def paused_publication(updated: ActivityProfile) -> None:
        if pause_after_profile:
            record_profile(updated)
        entered.set()
        if not release.wait(timeout=10):
            raise TimeoutError("the publication barrier was not released")
        if not pause_after_profile:
            record_profile(updated)

    monkeypatch.setattr(interview, "_record_profile", paused_publication)
    dispatcher = _Dispatcher(store)
    sessions = SessionService(LocalSessionRepository(tmp_path / "sessions"))
    service = RunService(store, sessions, dispatcher=dispatcher, risk_interview=interview)
    other_store = LocalRunStore(tmp_path / "runs")
    other_interview = RiskInterviewService(
        other_store, MiraControlPlane(other_store, PolicyEngine(load_default_policy()))
    )
    other_service = RunService(
        other_store, sessions, dispatcher=dispatcher, risk_interview=other_interview
    )
    actor = Actor(kind="human", id="researcher", authenticated=True)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(
            service.answer_risk_interview,
            run.id,
            answer="Only offline summaries; no decision about real people.",
            actor=actor,
            expected_question_id=question.event_id,
        )
        assert entered.wait(timeout=10)
        try:
            with pytest.raises(RunNotResumableError, match="already being resolved"):
                other_service.resume(run.id, actor=actor)
            assert dispatcher.resumed == []
        finally:
            release.set()
        result.result(timeout=20)

    events = store.events(run.id)
    assert dispatcher.resumed == [run.id]
    assert sum(event.type is EventType.ACTIVITY_PROFILE_ANSWERED for event in events) == 1
    assert interview.current_profile(run, profile).version == profile.version + 1
    assert verify_events(events).valid
