"""Approval bindings reject changed plans and recover the actual Gate request."""

from __future__ import annotations

from copy import deepcopy

import pytest

from thymira.agents.llm.scripted import ScriptedProvider
from thymira.core.plan_review import (
    PlanReviewError,
    unparked_plan_review_from_events,
    validate_plan_review_request,
    validate_plan_review_scope,
)
from thymira.events import InMemoryEventLog, verify_events
from thymira.policies import Gate, PolicyEngine, load_policy_stack
from thymira.schemas import EventType, ModelRoutePolicy, Run, new_id
from thymira.thy.agents import full_agent_catalog
from thymira.thy.models import (
    AgentTask,
    PlanOutput,
    ThyAgentKind,
    ThyPhase,
    ThyPlanReview,
    ThyState,
)
from thymira.thy.nodes.plan import plan_node


@pytest.fixture
def pending_review(monkeypatch: pytest.MonkeyPatch) -> tuple[Run, InMemoryEventLog, ThyPlanReview]:
    """Generate a real normalized candidate and durable request through the production Plan node."""
    for variable in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(variable, "test-model")
    run = Run(
        id=new_id("run"),
        project_id=new_id("project"),
        session_id=new_id("session"),
        prompt="Describe the dataset without training a model.",
    )
    log = InMemoryEventLog(run.id)
    proposed = PlanOutput(
        tasks=(
            AgentTask(
                id="profile",
                agent=ThyAgentKind.DATA,
                phase=ThyPhase.EXECUTE,
                instruction="Describe the dataset shape and missingness.",
            ),
        )
    )
    node = plan_node(
        full_agent_catalog(),
        Gate(PolicyEngine(load_policy_stack()), log),
        log,
        provider=ScriptedProvider([proposed]),
        route_policy=ModelRoutePolicy.from_routes(("test-model",), authority="code-owned-tests"),
    )
    result = ThyState.model_validate(node(ThyState(run=run)))
    assert result.error is None
    assert result.plan == ()
    assert result.pending_plan_review is not None
    assert verify_events(log.events()).valid
    return run, log, result.pending_plan_review


def test_request_before_park_recovers_the_exact_candidate(pending_review) -> None:
    run, log, original = pending_review
    recovered = unparked_plan_review_from_events(log.events(), run.id)

    assert recovered == original
    assert not any(event.type is EventType.RUN_TRANSITIONED for event in log.events())
    assert not any(event.type is EventType.HUMAN_APPROVAL for event in log.events())
    assert recovered is not None
    request = validate_plan_review_request(log.events(), recovered)
    assert request.payload["plan"] == original.candidate.model_dump(mode="json")


def test_changed_candidate_cannot_reuse_the_original_request(pending_review) -> None:
    _run, log, review = pending_review
    changed_task = review.candidate.tasks[0].model_copy(update={"instruction": "Train a model."})
    changed = review.model_copy(update={"candidate": PlanOutput(tasks=(changed_task,))})

    with pytest.raises(PlanReviewError, match="exact candidate"):
        validate_plan_review_request(log.events(), changed)


@pytest.mark.parametrize("field", ["candidate_sha256", "scope_sha256"])
def test_changed_request_digest_does_not_authorize_the_candidate(
    pending_review, field: str
) -> None:
    _run, log, review = pending_review
    events = log.events()
    request = next(event for event in events if event.event_id == review.request_event_id)
    payload = deepcopy(request.payload)
    payload["plan_review"][field] = "0" * 64
    corrupted = request.model_copy(update={"payload": payload})
    altered = [corrupted if event.event_id == request.event_id else event for event in events]

    with pytest.raises(PlanReviewError, match="digest"):
        validate_plan_review_request(altered, review)


@pytest.mark.parametrize("change", ["run", "work", "policy"])
def test_review_cannot_move_to_changed_run_work_or_policy(pending_review, change: str) -> None:
    run, _log, review = pending_review
    policy_sha256 = review.decision.policy_sha256
    if change == "run":
        run = run.model_copy(update={"id": new_id("run")})
    elif change == "work":
        run = run.model_copy(update={"prompt": "Train a credit decision model."})
    else:
        policy_sha256 = "0" * 64

    with pytest.raises(PlanReviewError, match=r"scope changed|current Run policy"):
        validate_plan_review_scope(review, run, None, None, policy_sha256=policy_sha256)


def test_request_without_its_gate_decision_cannot_release_the_candidate(pending_review) -> None:
    _run, log, review = pending_review
    events = [event for event in log.events() if event.type is not EventType.POLICY_DECISION]

    with pytest.raises(PlanReviewError, match="matching Gate decision"):
        validate_plan_review_request(events, review)
