"""Authenticated HTTP contracts for exact-review risk clarification and durable answers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from tests.thymira.api_support import api_credential, authenticated_client
from thymira.agents.llm import ScriptedProvider
from thymira.api import BearerTokenRegistry, build_default_deps, create_app, principal_for_role
from thymira.core import RunController, RunTransitionKind, default_activity_profile
from thymira.policies import RiskProfile
from thymira.schemas import Decision, EventType, Run, new_id

if TYPE_CHECKING:
    from pathlib import Path

    from thymira.schemas import Id

pytestmark = pytest.mark.integration


class _Dispatcher:
    """Observe dispatch without any external model or background execution."""

    def __init__(self) -> None:
        self.resumed: list[Id] = []

    def submit(self, run_id: Id) -> None:
        """Accept a submission for the fixture's manually composed initial review."""
        del run_id

    def resume(self, run_id: Id) -> None:
        """Record the continuation requested by a successful answer."""
        self.resumed.append(run_id)


@pytest.fixture
def pending_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("THYMIRA_MODEL_FAST", "THYMIRA_MODEL_STANDARD", "THYMIRA_MODEL_FRONTIER"):
        monkeypatch.setenv(name, "test-model")
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    workspace = tmp_path / "workspace"
    config = workspace / ".thymira" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project:\n  name: interview\n  domain: research\n", encoding="utf-8")
    roles = ("admin", "data-scientist", "risk-officer", "viewer")
    tokens = {role: api_credential(role)[0] for role in roles}
    resolver = BearerTokenRegistry(
        {token: principal_for_role(f"{role}-actor", role) for role, token in tokens.items()}
    )
    dispatcher = _Dispatcher()
    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        dispatcher=dispatcher,
        principal_resolver=resolver,
        provider=ScriptedProvider([{"sufficient": True, "reason": "Concrete research boundary."}]),
    )
    app = create_app(deps)
    clients = {role: authenticated_client(app, token) for role, token in tokens.items()}
    created = clients["admin"].post("/runs", json={"prompt": "Summarize synthetic data."})
    assert created.status_code == 201
    run = Run.model_validate(created.json())
    RunController(deps.run_store).advance(run.id, RunTransitionKind.START)
    profile = default_activity_profile(run).model_copy(
        update={
            "version": 2,
            "purpose": "Offline research summary",
            "affected_population": "Synthetic records only",
            "decision_effect": "No decision about a real person",
            "autonomy": "No deployment",
            "human_oversight": "Researcher reads every summary",
            "jurisdiction": "EU",
            "data_categories": ("synthetic",),
            "sensitive_attributes": (),
            "potential_consequences": ("Incorrect research summary",),
        }
    )
    deps.risk_interview.begin(run, profile)
    decision = deps.gate_factory(run.id).authorize_execution(
        RiskProfile(risk_level="medium", needs_human_review=True), ()
    )
    assert decision.decision is Decision.REQUIRE_HUMAN_REVIEW
    RunController(deps.run_store).park_for_review(run.id, decision, resume_from="execution_start")
    return clients, app, deps, dispatcher, run, decision.id


@pytest.mark.parametrize("role", ["viewer", "risk-officer"])
def test_risk_clarification_requires_write_without_changing_review(pending_review, role):
    clients, app, deps, _, run, decision_id = pending_review
    before = deps.run_store.events(run.id)
    body = {"field": "decision_effect", "expected_decision_id": decision_id}
    endpoint = f"/runs/{run.id}/risk-interview/clarify"
    assert clients[role].post(endpoint, json=body).status_code == 403
    assert TestClient(app).post(endpoint, json=body).status_code == 401
    assert deps.run_store.events(run.id) == before


def test_risk_clarification_rejects_stale_identity_and_non_fact_fields(pending_review):
    clients, _, deps, _, run, _ = pending_review
    before = deps.run_store.events(run.id)
    endpoint = f"/runs/{run.id}/risk-interview/clarify"
    stale = clients["admin"].post(
        endpoint, json={"field": "decision_effect", "expected_decision_id": new_id("decision")}
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "risk_review_changed"
    invalid = clients["admin"].post(
        endpoint, json={"field": "risk_level", "expected_decision_id": new_id("decision")}
    )
    assert invalid.status_code == 422
    assert deps.run_store.events(run.id) == before


@pytest.mark.parametrize("question_id", [None, "event_stale"])
def test_corrected_fact_answer_requires_the_displayed_question(pending_review, question_id):
    clients, _, deps, dispatcher, run, decision_id = pending_review
    endpoint = f"/runs/{run.id}/risk-interview"
    response = clients["data-scientist"].post(
        f"{endpoint}/clarify",
        json={"field": "decision_effect", "expected_decision_id": decision_id},
    )
    assert response.status_code == 202
    before = deps.run_store.events(run.id)
    rejected = clients["data-scientist"].post(
        endpoint, json={"answer": "Offline research only", "expected_question_id": question_id}
    )
    assert rejected.status_code == 409
    assert deps.run_store.events(run.id) == before
    assert dispatcher.resumed == []


def test_clarification_answer_bounds_and_revision_survive_api_reconstruction(
    pending_review, tmp_path
):
    clients, _, deps, dispatcher, run, decision_id = pending_review
    endpoint = f"/runs/{run.id}/risk-interview"
    request = {"field": "decision_effect", "expected_decision_id": decision_id}
    first = clients["data-scientist"].post(f"{endpoint}/clarify", json=request)
    assert first.status_code == 202
    question = first.json()["pending_question"]
    duplicate = clients["data-scientist"].post(f"{endpoint}/clarify", json=request)
    assert duplicate.status_code == 202
    assert duplicate.json()["pending_question"]["event_id"] == question["event_id"]
    before = deps.run_store.events(run.id)
    too_long = clients["data-scientist"].post(
        endpoint, json={"answer": "x" * 8001, "expected_question_id": question["event_id"]}
    )
    assert too_long.status_code == 422
    assert deps.run_store.events(run.id) == before

    answer = "This analysis has no effect on real people; researchers review synthetic summaries."
    accepted = clients["data-scientist"].post(
        endpoint, json={"answer": answer, "expected_question_id": question["event_id"]}
    )
    assert accepted.status_code == 202
    assert accepted.json()["profile"]["version"] == first.json()["profile"]["version"] + 1
    assert accepted.json()["profile"]["decision_effect"] == answer
    assert dispatcher.resumed == [run.id]
    assert all(
        event.type is not EventType.HUMAN_APPROVAL for event in deps.run_store.events(run.id)
    )
    replay = clients["data-scientist"].post(
        endpoint, json={"answer": answer, "expected_question_id": question["event_id"]}
    )
    assert replay.status_code == 409
    assert dispatcher.resumed == [run.id]

    token, credential = api_credential("viewer")
    assert deps.project_resolution is not None
    fresh = build_default_deps(
        tmp_path / "runtime",
        workspace=tmp_path / "workspace",
        dispatcher=_Dispatcher(),
        principal_resolver=credential,
    )
    reloaded = authenticated_client(create_app(fresh), token).get(endpoint)
    assert reloaded.status_code == 200
    assert reloaded.json()["profile"]["decision_effect"] == answer
    assert reloaded.json()["pending_question"] is None
