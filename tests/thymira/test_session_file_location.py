"""Conservative server replies for explicit file-location questions."""

from __future__ import annotations

import pytest

from thymira.core.session_file_location import file_location_reply
from thymira.core.session_read_tools import SessionReadRecord
from thymira.schemas.session_chat import SessionCitation


def _file_record(
    path: str,
    *,
    source: str = "q1",
    operation: str = "list_files",
    kind: str | None = "file",
    citation_path: str | None = None,
    citation_run_id: str | None = None,
    offset: int = 0,
    returned_count: int = 1,
    truncated: bool = False,
) -> tuple[str, SessionReadRecord]:
    run_id = path.split("/")[1] if path.startswith("runs/") else None
    citation = SessionCitation(
        run_id=citation_run_id if citation_run_id is not None else run_id,
        path=citation_path if citation_path is not None else path,
        label="Test file",
    )
    value: dict[str, object] = {
        "operation": operation,
        "path": path,
        "offset": offset,
        "returned_count": returned_count,
        "truncated": truncated,
        "next_offset": 1 if truncated else None,
    }
    if kind is not None:
        value["kind"] = kind
    if operation == "glob":
        value["scan_complete"] = True
    return source, SessionReadRecord(value=value, citation=citation)


def _citations(*records: tuple[str, SessionReadRecord]) -> dict[str, SessionCitation]:
    return {source: record.citation for source, record in records}


@pytest.mark.parametrize(
    "question",
    ["¿Dónde están los PNG de la run?", "Where are the .png files?"],
)
def test_simple_explicit_extension_location_is_recognized(question):
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile/roc.png")

    proposal = file_location_reply(question, [record], _citations(record))

    assert proposal is not None
    assert proposal.kind == "answer"
    assert "roc.png" in proposal.message
    assert proposal.sources == (record[0],)


@pytest.mark.parametrize(
    "question",
    [
        "Why are the .png files missing?",
        "Where are the .png files and what do they contain?",
        "Where is .png mentioned in the report?",
        "Find the .png files for me.",
        "Where are the profile files?",
        "Where are the .pý files?",
        "¿Dónde están los PNG de la run que falló?",
    ],
)
def test_explanatory_compound_or_ambiguous_question_stays_on_qa_path(question):
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile/roc.png")

    assert file_location_reply(question, [record], _citations(record)) is None


def test_location_reply_stays_on_qa_path_when_clarification_context_adds_constraints():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile/roc.png")

    assert (
        file_location_reply(
            "Where are the .png files?",
            [record],
            _citations(record),
            has_linked_clarification=True,
        )
        is None
    )


def test_requested_other_extension_does_not_match_png_results():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile/roc.png")

    assert file_location_reply("Where are the .pdf files?", [record], _citations(record)) is None


def test_ascii_filename_extension_comparison_is_case_insensitive():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile/ROC.PNG")

    proposal = file_location_reply("Where are the .png files?", [record], _citations(record))

    assert proposal is not None
    assert record[0] in proposal.sources


def test_unknown_bare_word_is_not_treated_as_a_literal_extension():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile.plot")

    assert file_location_reply("Where are the profile files?", [record], _citations(record)) is None


def test_non_ascii_extension_cannot_normalize_into_a_different_literal_extension():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile.py")

    assert file_location_reply("Where are the .pý files?", [record], _citations(record)) is None


def test_unicode_filename_suffix_cannot_casefold_into_a_requested_extension():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile.ß")

    assert file_location_reply("Where are the .ss files?", [record], _citations(record)) is None


def test_directories_and_zero_match_glob_scopes_are_not_file_locations():
    directory = _file_record(
        "runs/run_" + "a" * 32 + "/artifacts/profile.png",
        kind="directory",
    )
    zero_match = _file_record(
        "runs/run_" + "a" * 32 + "/artifacts/profile.png",
        operation="glob",
        kind=None,
        returned_count=0,
    )

    assert (
        file_location_reply("Where are the .png files?", [directory], _citations(directory)) is None
    )
    assert (
        file_location_reply("Where are the .png files?", [zero_match], _citations(zero_match))
        is None
    )


@pytest.mark.parametrize(
    ("citation_path", "citation_run_id"),
    [
        ("runs/run_" + "b" * 32 + "/artifacts/profile/roc.png", None),
        (None, "run_" + "b" * 32),
    ],
)
def test_path_and_run_citation_must_match_the_returned_record(citation_path, citation_run_id):
    record = _file_record(
        "runs/run_" + "a" * 32 + "/artifacts/profile/roc.png",
        citation_path=citation_path,
        citation_run_id=citation_run_id,
    )

    assert file_location_reply("Where are the .png files?", [record], _citations(record)) is None


def test_paginated_or_truncated_result_is_labeled_as_a_page():
    record = _file_record(
        "runs/run_" + "a" * 32 + "/artifacts/profile/roc.png",
        offset=10,
        truncated=True,
    )

    proposal = file_location_reply("Where are the .png files?", [record], _citations(record))

    assert proposal is not None
    assert "returned page only" in proposal.message
    assert "more matches may exist" in proposal.message


def test_reply_is_bounded_to_twelve_paths_and_escapes_backtick_in_filenames():
    run_id = "run_" + "a" * 32
    records = [
        _file_record(f"runs/{run_id}/artifacts/profile/plot-{index}.png", source=f"q1_{index}")
        for index in range(13)
    ]
    backtick = _file_record(f"runs/{run_id}/artifacts/profile/`plot`.png", source="q1_0")
    records[0] = backtick

    proposal = file_location_reply("Where are the .png files?", records, _citations(*records))

    assert proposal is not None
    assert len(proposal.sources) == 12
    assert "returned page only" in proposal.message
    assert "``runs/" in proposal.message
    assert records[-1][0] not in proposal.sources


def test_known_credential_value_in_path_is_not_reintroduced_in_a_server_reply(monkeypatch):
    credential_value = "thymira-test-sensitive-credential-827361"
    monkeypatch.setenv("THYMIRA_TEST_API_KEY", credential_value)
    record = _file_record(f"runs/run_{'a' * 32}/artifacts/profile/api_key={credential_value}.png")

    assert file_location_reply("Where are the .png files?", [record], _citations(record)) is None


def test_server_message_respects_schema_limit_without_splitting_a_path():
    run_id = "run_" + "a" * 32
    records = [
        _file_record(
            f"runs/{run_id}/artifacts/profile/{index}-{'x' * 700}.png",
            source=f"q1_{index}",
        )
        for index in range(12)
    ]

    proposal = file_location_reply("Where are the .png files?", records, _citations(*records))

    assert proposal is not None
    assert len(proposal.message) <= 6000
    assert "returned page only" in proposal.message
    assert len(proposal.sources) < len(records)


def test_control_characters_stay_inside_a_literal_code_path():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile/line\nbreak.png")

    proposal = file_location_reply("Where are the .png files?", [record], _citations(record))

    assert proposal is not None
    assert r"line\nbreak.png" in proposal.message


def test_spanish_location_question_gets_a_spanish_server_reply():
    record = _file_record("runs/run_" + "a" * 32 + "/artifacts/profile/roc.png")

    proposal = file_location_reply("¿Dónde están los PNG de la run?", [record], _citations(record))

    assert proposal is not None
    assert proposal.message.startswith("Archivos ubicados en esta consulta:")
