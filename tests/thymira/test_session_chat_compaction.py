"""Compact read-only proposal surfaces retain whole evidence groups and strict contracts."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from thymira.schemas import Session
from thymira.schemas.session_chat import SessionCitation, SessionDiscussion
from thymira.state.session_discussions import SessionProviderLog


@pytest.fixture
def recovery_boundary(tmp_path):
    session = Session(id="session_" + "1" * 32, project_id="project_" + "1" * 32, client="test")
    discussion = SessionDiscussion(
        id="discussion_" + "2" * 32,
        ordinal=1,
        user_message="Where is the manifest?",
        mode="ask",
    )
    log = SessionProviderLog(tmp_path / "discussions", session.id, discussion.id)
    return session, discussion, log


def test_compact_evidence_keeps_the_latest_query_batch_whole_and_latest_error() -> None:
    from thymira.core.session_chat_compaction import select_compact_evidence

    records = [
        json.dumps({"source": "r0", "status": "COMPLETED", "run_id": "run_1"}),
        json.dumps({"source": "q1_1", "path": "project/a.csv"}),
        json.dumps({"source": "q1_2", "path": "project/b.csv"}),
        json.dumps({"tool_error_id": "read_error_1", "tool_error": {"message": "denied"}}),
        json.dumps({"source": "q2_1", "path": "project/manifest.json"}),
        json.dumps({"source": "q2_2", "path": "project/plot.png"}),
    ]
    citations = {
        source: SessionCitation(path=f"project/{source}", label=source)
        for source in ("q1_1", "q1_2", "q2_1", "q2_2")
    }
    run_id = "run_" + "1" * 32
    records[0] = json.dumps({"source": "r0", "status": "COMPLETED", "run_id": run_id})
    citations["r0"] = SessionCitation(run_id=run_id, label="Run status")

    selected = select_compact_evidence(records, citations, question="Where is the plot?")

    assert {json.loads(line).get("source") for line in selected.records} >= {
        "q2_1",
        "q2_2",
        "r0",
    }
    assert any(json.loads(line).get("tool_error_id") == "read_error_1" for line in selected.records)
    assert "q1_1" not in selected.citations
    assert "q1_2" not in selected.citations
    assert set(selected.citations) == {"r0", "q2_1", "q2_2"}


def test_compact_evidence_keeps_older_relevant_query_batch_when_exact_fit_allows_it() -> None:
    from thymira.core.session_chat_compaction import select_compact_evidence

    records = [
        json.dumps({"source": "q1_1", "path": "project/manifest.json"}),
        json.dumps({"source": "q1_2", "path": "project/manifest.sha256"}),
        json.dumps({"source": "q2_1", "path": "project/manifest-copy.json"}),
        json.dumps({"source": "q2_2", "path": "project/manifest-copy.sha256"}),
    ]
    citations = {
        source: SessionCitation(path=f"project/{source}", label=source)
        for source in ("q1_1", "q1_2", "q2_1", "q2_2")
    }

    selected = select_compact_evidence(
        records,
        citations,
        question="Where is the project manifest?",
        accept_candidate=lambda _candidate: True,
    )

    assert {json.loads(line)["source"] for line in selected.records} == {
        "q1_1",
        "q1_2",
        "q2_1",
        "q2_2",
    }
    assert set(selected.citations) == {"q1_1", "q1_2", "q2_1", "q2_2"}


def test_compact_read_recovery_returns_only_citations_in_the_visible_frame(
    monkeypatch: pytest.MonkeyPatch,
    recovery_boundary,
) -> None:
    from thymira.core import session_chat_read_recovery
    from thymira.core.session_chat_compaction import (
        build_compact_request_surface,
        select_compact_evidence,
    )

    raw_records = [
        json.dumps({"source": "q1", "path": "project/old-notes.txt"}),
        json.dumps({"source": "q2", "path": "project/manifest.json"}),
    ]
    citations = {
        "q1": SessionCitation(path="project/old-notes.txt", label="Earlier read"),
        "q2": SessionCitation(path="project/manifest.json", label="Latest read"),
    }
    evidence = select_compact_evidence(
        raw_records,
        citations,
        question="Where is the manifest?",
        accept_candidate=lambda candidate: set(candidate.citations) <= {"q2"},
    )
    session, discussion, log = recovery_boundary
    compact_surface = build_compact_request_surface(
        discussion,
        evidence,
        "",
        answer_only=False,
        clarification_context=(),
        evidence_question=discussion.user_message,
    )
    monkeypatch.setattr(
        session_chat_read_recovery,
        "propose_reply",
        lambda *args, **kwargs: object(),
    )

    _, _, returned_citations = session_chat_read_recovery.propose_with_read_recovery(
        session,
        discussion,
        compact_surface.evidence.evidence,
        "",
        log,
        None,
        "",
        raw_records=raw_records,
        citations=citations,
        allowed_source_ids=compact_surface.evidence.allowed_source_ids,
        clarification_context=(),
        evidence_question=discussion.user_message,
        compact_surface=compact_surface,
    )

    assert set(returned_citations) == {"q2"}


def test_initial_read_recovery_returns_only_the_supplied_visible_citations(
    monkeypatch,
    recovery_boundary,
) -> None:
    from thymira.core import session_chat_read_recovery

    session, discussion, log = recovery_boundary
    citations = {
        "r0": SessionCitation(path="project/visible.txt", label="Visible"),
        "r1": SessionCitation(path="project/omitted.txt", label="Omitted"),
    }
    monkeypatch.setattr(
        session_chat_read_recovery, "propose_reply", lambda *args, **kwargs: object()
    )

    _, _, visible = session_chat_read_recovery.propose_with_read_recovery(
        session,
        discussion,
        "bounded evidence",
        "",
        log,
        None,
        "",
        raw_records=[],
        citations=citations,
        allowed_source_ids=("r0",),
        clarification_context=(),
        evidence_question="Where is the file?",
    )

    assert set(visible) == {"r0"}


def test_initial_read_correction_refuses_a_partially_framed_error(
    monkeypatch,
    recovery_boundary,
) -> None:
    from thymira.agents import frame_untrusted
    from thymira.core import session_chat_read_recovery
    from thymira.core.session_chat_context import MAX_EVIDENCE_CHARS, frame_session_evidence
    from thymira.core.session_chat_read_validation import RecoverableEmptyGlobProposalError

    session, discussion, log = recovery_boundary
    overhead = len(frame_untrusted("", label="verified-session-evidence"))
    quoted_error_id = "read_validation_error_1"
    empty_record = json.dumps({"source": "r0", "text": quoted_error_id})
    record = json.dumps(
        {
            "source": "r0",
            "text": quoted_error_id
            + "x" * (MAX_EVIDENCE_CHARS - overhead - len(empty_record) - 100),
        }
    )
    citations = {"r0": SessionCitation(path="project/visible.txt", label="Visible")}
    evidence, visible = frame_session_evidence(record, citations)
    assert set(visible) == {"r0"}

    def rejected_proposal(*args, **kwargs):
        raise RecoverableEmptyGlobProposalError

    monkeypatch.setattr(session_chat_read_recovery, "propose_reply", rejected_proposal)
    monkeypatch.setattr(
        session_chat_read_recovery, "selected_model_from_log", lambda log: "test-model"
    )

    with pytest.raises(ValueError, match="correction error did not fit"):
        session_chat_read_recovery.propose_with_read_recovery(
            session,
            discussion,
            evidence,
            "",
            log,
            None,
            "",
            raw_records=[record],
            citations=citations,
            allowed_source_ids=("r0",),
            clarification_context=(),
            evidence_question="Where is the file?",
        )


def test_compact_evidence_omits_an_entire_oversized_page_and_keeps_its_safe_error() -> None:
    from thymira.core.session_chat_compaction import select_compact_evidence

    records = [
        json.dumps({"source": "q1", "path": "project/manifest.json"}),
        json.dumps({"source": "q2_1", "path": "project/large-a.csv", "text": "A" * 100}),
        json.dumps({"source": "q2_2", "path": "project/large-b.csv", "text": "B" * 100}),
        json.dumps(
            {
                "tool_error_id": "read_context_2",
                "tool_error": {
                    "message": "The complete result did not fit; no partial result was supplied."
                },
            }
        ),
    ]
    citations = {
        "q1": SessionCitation(path="project/manifest.json", label="Earlier page"),
        "q2_1": SessionCitation(path="project/large-a.csv", label="Large result A"),
        "q2_2": SessionCitation(path="project/large-b.csv", label="Large result B"),
    }

    selected = select_compact_evidence(
        records,
        citations,
        question="Where is the manifest?",
        omitted_query_numbers=frozenset({2}),
    )

    selected_sources = {
        record["source"]
        for line in selected.records
        if isinstance((record := json.loads(line)).get("source"), str)
    }
    assert selected_sources == {"q1"}
    assert any(
        json.loads(line).get("tool_error_id") == "read_context_2" for line in selected.records
    )
    assert set(selected.citations) == {"q1"}


def test_budgeted_answer_omits_an_oversized_latest_page_as_a_whole() -> None:
    from thymira.core.session_chat_compaction import build_budgeted_compact_request_surface
    from thymira.schemas.session_chat import SessionDiscussion

    discussion = SessionDiscussion(
        id="discussion_" + "3" * 32,
        ordinal=1,
        user_message="Where is the manifest?",
        mode="ask",
    )
    raw_records = [
        json.dumps({"source": "q1", "path": "project/manifest.json"}),
        json.dumps(
            {
                "source": "q2_1",
                "path": "project/large-a.csv",
                "text": "A" * 200,
            }
        ),
        json.dumps(
            {
                "source": "q2_2",
                "path": "project/large-b.csv",
                "text": "B" * 200,
            }
        ),
    ]
    citations = {
        "q1": SessionCitation(path="project/manifest.json", label="Earlier result"),
        "q2_1": SessionCitation(path="project/large-a.csv", label="Large result A"),
        "q2_2": SessionCitation(path="project/large-b.csv", label="Large result B"),
    }

    def estimate(_model, prompt, _system, _schema) -> int:
        return 500 if "large-a.csv" in prompt or "large-b.csv" in prompt else 50

    surface = build_budgeted_compact_request_surface(
        discussion,
        raw_records,
        citations,
        "",
        question=discussion.user_message,
        answer_only=True,
        clarification_context=(),
        evidence_question=discussion.user_message,
        model="test-model",
        available_tokens=900,
        estimate_input_tokens=estimate,
    )

    assert surface.answer_only is True
    assert surface.schema.model_json_schema()["properties"]["kind"]["const"] == "answer"
    assert set(surface.evidence.citations) == {"q1"}
    assert '"source":"q2_1"' not in surface.evidence.evidence
    assert '"source":"q2_2"' not in surface.evidence.evidence
    assert '"tool_error_id":"read_context_2"' in surface.evidence.evidence
    assert any('"tool_error_id":"read_context_2"' in line for line in raw_records)


def test_budgeted_answer_reuses_the_surface_that_passed_the_final_fit_check() -> None:
    from thymira.core.session_chat_compaction import build_budgeted_compact_request_surface
    from thymira.schemas.session_chat import SessionDiscussion

    discussion = SessionDiscussion(
        id="discussion_" + "4" * 32,
        ordinal=1,
        user_message="Where is the manifest?",
        mode="ask",
    )
    records = [json.dumps({"source": "q1", "path": "project/manifest.json"})]
    citations = {"q1": SessionCitation(path="project/manifest.json", label="Manifest")}
    estimated_prompts: list[str] = []

    def estimate(_model, prompt, _system, _schema) -> int:
        estimated_prompts.append(prompt)
        return 4_515 if len(estimated_prompts) == 1 else 4_520

    surface = build_budgeted_compact_request_surface(
        discussion,
        records,
        citations,
        "",
        question=discussion.user_message,
        answer_only=True,
        clarification_context=(),
        evidence_question=discussion.user_message,
        model="test-model",
        available_tokens=5_157,
        estimate_input_tokens=estimate,
    )

    assert surface.estimated_input_tokens == 4_515
    assert surface.estimated_input_tokens + 384 + 256 <= 5_157
    assert set(surface.evidence.citations) == {"q1"}
    assert len(estimated_prompts) == 1
    assert surface.prompt == estimated_prompts[0]


def test_compact_wire_schema_keeps_dynamic_sources_and_canonical_unused_lookup_behavior() -> None:
    from thymira.core.session_chat_compaction import compact_proposal_schema

    answer_schema = compact_proposal_schema(("q2", "r0"), answer_only=True)
    answer_shape = answer_schema.model_json_schema()
    assert answer_shape["properties"]["kind"]["const"] == "answer"
    assert "lookup" not in answer_shape["properties"]
    assert answer_shape["properties"]["sources"]["items"]["enum"] == ["q2", "r0"]

    answer = answer_schema.model_validate(
        {
            "kind": "answer",
            "message": "The file was listed.",
            "sources": ["q2"],
            "lookup": {
                "operation": "glob",
                "path": "project/*.png",
                "pattern": "",
            },
        }
    )
    assert answer.to_session_chat_proposal().sources == ("q2",)
    with pytest.raises(ValidationError):
        answer_schema.model_validate(
            {"kind": "answer", "message": "Unsupported citation.", "sources": ["q1"]}
        )
    with pytest.raises(ValidationError):
        answer_schema.model_validate({"kind": "action", "message": "Run work."})


def test_compact_clarification_conversion_is_scoped_to_evidence() -> None:
    from thymira.core.session_chat_compaction import compact_proposal_schema

    continuation_schema = compact_proposal_schema(("r0",), answer_only=False)
    assert continuation_schema.model_json_schema()["properties"]["kind"]["enum"] == [
        "answer",
        "clarification",
        "lookup",
    ]
    proposal = continuation_schema.model_validate(
        {"kind": "clarification", "message": "Which file do you mean?", "sources": ["r0"]}
    )
    converted = continuation_schema.to_session_chat_proposal(proposal)
    assert converted.clarification_scope == "evidence"
    assert converted.work_kind == "general"
    assert converted.continues_clarification is False
    assert converted.replaces_clarification is False


def test_compact_excerpt_rejects_a_source_outside_its_visible_frame() -> None:
    from thymira.core.session_chat_compaction import compact_proposal_schema

    schema = compact_proposal_schema(("q2",), answer_only=False)
    with pytest.raises(ValidationError):
        schema.model_validate(
            {
                "kind": "answer",
                "message": "Quote the source.",
                "sources": ["q2"],
                "source_excerpt": {"source": "q1", "contains": "def main"},
            }
        )


def test_compact_excerpt_preserves_a_valid_visible_source_request() -> None:
    from thymira.core.session_chat_compaction import compact_proposal_schema

    schema = compact_proposal_schema(("q2",), answer_only=False)
    proposal = schema.model_validate(
        {
            "kind": "answer",
            "message": "Quote the source.",
            "sources": ["q2"],
            "source_excerpt": {"source": "q2", "contains": "def main"},
        }
    ).to_session_chat_proposal()

    assert proposal.source_excerpt is not None
    assert proposal.source_excerpt.source == "q2"
    assert proposal.source_excerpt.contains == "def main"


def test_compact_evidence_refuses_a_partially_visible_mandatory_error() -> None:
    from thymira.core.session_chat_compaction import select_compact_evidence

    error = json.dumps({"tool_error_id": "read_error_1", "tool_error": {"message": "x" * 20_000}})
    with pytest.raises(ValueError, match="complete latest read evidence"):
        select_compact_evidence([error], {}, question="Where is the plot?")


def test_compact_request_surface_preserves_complete_human_context_without_assistant_prose() -> None:
    from thymira.core.session_chat_compaction import (
        build_compact_request_surface,
        select_compact_evidence,
    )
    from thymira.schemas.session_chat import SessionDiscussion

    user_history = "The previous user message contains an important restriction."
    history = json.dumps(
        {
            "kind": "clarification",
            "mode": "ask",
            "clarification_scope": "evidence",
            "user": user_history,
            "read_only_explanation_not_audited_result": "Assistant prose is removable.",
        }
    )
    evidence = select_compact_evidence(
        [json.dumps({"source": "q1", "path": "project/manifest.json"})],
        {"q1": SessionCitation(path="project/manifest.json", label="Project file")},
        question="Where is the manifest?",
    )
    discussion = SessionDiscussion(
        id="discussion_" + "1" * 32,
        ordinal=2,
        user_message="Show me the requested project file.",
        mode="ask",
    )

    surface = build_compact_request_surface(
        discussion,
        evidence,
        history,
        answer_only=False,
        clarification_context=("Do not expose private source data.",),
        evidence_question="Where is the manifest?",
    )

    assert user_history in surface.prompt
    assert "Do not expose private source data." in surface.prompt
    assert "Where is the manifest?" in surface.prompt
    assert discussion.user_message in surface.prompt
    assert "Assistant prose is removable." not in surface.prompt
    assert "project/manifest.json" in surface.prompt
    assert surface.schema.model_json_schema()["properties"]["sources"]["items"]["enum"] == ["q1"]
