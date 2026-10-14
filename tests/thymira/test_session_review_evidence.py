"""Historical approval facts remain bound to the exact review that requested them."""

import pytest

from thymira.core.session_review_evidence import review_resolutions
from thymira.events import InMemoryEventLog
from thymira.schemas import Actor, ActorKind, EventType, new_id


@pytest.mark.parametrize("defect", ["different_decision", "automatic", "unauthenticated", "system"])
def test_unrelated_or_nonhuman_responses_do_not_resolve_a_human_review(defect):
    log = InMemoryEventLog(new_id("run"))
    decision = new_id("decision")
    subject = new_id("run")
    request = log.append(
        EventType.HUMAN_APPROVAL_REQUESTED,
        Actor.system(),
        {"decision_id": decision},
        subject_id=subject,
    )
    actor = (
        Actor.system()
        if defect == "system"
        else Actor(id="operator", kind=ActorKind.HUMAN, authenticated=defect != "unauthenticated")
    )
    log.append(
        EventType.HUMAN_APPROVAL,
        actor,
        {
            "decision_id": new_id("decision") if defect == "different_decision" else decision,
            "approved": True,
            "automatic": defect == "automatic",
        },
        subject_id=subject,
    )
    assert review_resolutions(log.events())[request.seq]["state"] == "no_recorded_human_response"


@pytest.mark.parametrize("key", ["decision_id", "policy_decision_id"])
def test_canonical_human_approval_without_automatic_flag_resolves_its_request(key):
    log = InMemoryEventLog(new_id("run"))
    decision = new_id("decision")
    subject = new_id("run")
    request = log.append(
        EventType.HUMAN_APPROVAL_REQUESTED,
        Actor.system(),
        {key: decision},
        subject_id=subject,
    )
    response = log.append(
        EventType.HUMAN_APPROVAL,
        Actor(id="operator", kind=ActorKind.HUMAN, authenticated=True),
        {key: decision, "approved": True},
        subject_id=subject,
    )
    assert review_resolutions(log.events())[request.seq] == {
        "state": "human_approved",
        "decision_id": decision,
        "response_seq": response.seq,
    }


def test_same_decision_response_for_different_subject_does_not_resolve_request():
    log = InMemoryEventLog(new_id("run"))
    decision = new_id("decision")
    subject = new_id("run")
    request = log.append(
        EventType.HUMAN_APPROVAL_REQUESTED,
        Actor.system(),
        {"decision_id": decision},
        subject_id=subject,
    )
    log.append(
        EventType.HUMAN_APPROVAL,
        Actor(id="reviewer", kind=ActorKind.HUMAN, authenticated=True),
        {"decision_id": decision, "approved": True},
        subject_id=new_id("run"),
    )

    assert review_resolutions(log.events())[request.seq]["state"] == "no_recorded_human_response"
