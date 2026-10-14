"""Select complete Session evidence pages and retain explicit bounded-omission records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.core.session_chat_context import frame_session_evidence
from thymira.core.session_chat_selection import meaningful_session_words
from thymira.events import canonical_json

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.schemas.session_chat import SessionCitation

_QUERY_SOURCE = re.compile(r"^q(?P<query>[0-9]+)(?:_[0-9]+)?$")
_EVENT_SOURCE = re.compile(r"^(?P<run>r[0-9]+)e(?P<event>[0-9]+)(?:v[0-9]+)?$")


@dataclass(frozen=True, slots=True)
class CompactSessionEvidence:
    """One complete model-visible evidence frame with citations for exactly its records."""

    evidence: str
    records: tuple[str, ...]
    citations: dict[str, SessionCitation]

    @property
    def allowed_source_ids(self) -> tuple[str, ...]:
        """Return the sorted source enum for this exact frame."""
        return tuple(sorted(self.citations))


@dataclass(frozen=True, slots=True)
class _EvidenceGroup:
    key: str
    indexes: tuple[int, ...]
    source_ids: frozenset[str]
    error_ids: frozenset[str]
    relevance: int


def select_compact_evidence(
    raw_records: list[str] | tuple[str, ...],
    citations: dict[str, SessionCitation],
    *,
    question: str,
    accept_candidate: Callable[[CompactSessionEvidence], bool] | None = None,
    omitted_query_numbers: frozenset[int] = frozenset(),
) -> CompactSessionEvidence:
    """Keep the latest complete read page, latest safe error, and useful whole source groups."""
    groups, source_by_index, error_by_index = _index_evidence_records(raw_records)
    omitted_query_keys = {f"query:{number}" for number in omitted_query_numbers}
    mandatory_keys = _mandatory_group_keys(
        groups,
        source_by_index,
        error_by_index,
        omitted_query_numbers=omitted_query_numbers,
    )
    ordered_groups = _rank_evidence_groups(
        raw_records, groups, source_by_index, error_by_index, question
    )
    by_key = {group.key: group for group in ordered_groups}
    selected_keys = [key for key in mandatory_keys if key in by_key]
    candidates = _optional_candidates(ordered_groups, selected_keys, omitted_query_keys)
    selected = _fit_groups(
        raw_records,
        citations,
        by_key,
        selected_keys,
        mandatory_keys,
    )
    mandatory_candidate_fits = accept_candidate is None or accept_candidate(selected)
    if mandatory_candidate_fits and "source:r0" in by_key and "source:r0" not in selected_keys:
        candidate = _try_fit_groups(
            raw_records,
            citations,
            by_key,
            [*selected_keys, "source:r0"],
            mandatory_keys,
        )
        if candidate is not None and (accept_candidate is None or accept_candidate(candidate)):
            selected_keys.append("source:r0")
            selected = candidate
    if not mandatory_candidate_fits:
        return selected
    for group in candidates:
        trial_keys = [*selected_keys, group.key]
        trial = _try_fit_groups(raw_records, citations, by_key, trial_keys, mandatory_keys)
        if trial is None or (accept_candidate is not None and not accept_candidate(trial)):
            continue
        selected_keys = trial_keys
        selected = trial
    return selected


def select_answer_with_page_omission(
    raw_records: list[str],
    citations: dict[str, SessionCitation],
    *,
    question: str,
    accept_candidate: Callable[[CompactSessionEvidence], bool],
    omitted_query_numbers: set[int],
) -> tuple[CompactSessionEvidence, list[str]]:
    """Drop a complete newest read page when it cannot fit an answer-only request."""
    records = list(raw_records)
    omitted = set(omitted_query_numbers)
    while True:
        try:
            selected = select_compact_evidence(
                records,
                citations,
                question=question,
                accept_candidate=accept_candidate,
                omitted_query_numbers=frozenset(omitted),
            )
        except ValueError:
            selected = None
        if selected is not None and accept_candidate(selected):
            return selected, records
        newest_page = _latest_query_number(records, omitted)
        if newest_page is None:
            if selected is not None:
                return selected, records
            raise ValueError("the mandatory compact evidence did not fit")
        omitted.add(newest_page)
        records = _omit_query_page(records, newest_page)


def _latest_query_number(records: list[str], omitted: set[int]) -> int | None:
    query_numbers: list[int] = []
    for line in records:
        record = _parse_record(line)
        source = record.get("source") if record is not None else None
        match = _QUERY_SOURCE.fullmatch(source) if isinstance(source, str) else None
        if match is not None and (number := int(match.group("query"))) not in omitted:
            query_numbers.append(number)
    return max(query_numbers, default=None)


def _omit_query_page(records: list[str], query_number: int) -> list[str]:
    page_pattern = re.compile(rf"^q{query_number}(?:_[0-9]+)?$")
    kept: list[str] = []
    for line in records:
        record = _parse_record(line)
        source = record.get("source") if record is not None else None
        if not (isinstance(source, str) and page_pattern.fullmatch(source)):
            kept.append(line)
    error_id = f"read_context_{query_number}"
    if not any(_record_has_error(line, error_id) for line in kept):
        kept.append(
            canonical_json(
                {
                    "tool_error_id": error_id,
                    "tool_error": {
                        "message": (
                            "The complete result page did not fit the bounded evidence context. "
                            "No partial page was supplied; narrow the path or request a "
                            "smaller page."
                        ),
                        "server_omitted_query_number": query_number,
                    },
                }
            )
        )
    return kept


def _record_has_error(line: str, error_id: str) -> bool:
    record = _parse_record(line)
    return record is not None and record.get("tool_error_id") == error_id


def server_omitted_query_numbers(records: list[str]) -> set[int]:
    """Read only explicit server omission markers from top-level safe error records."""
    omitted: set[int] = set()
    for line in records:
        record = _parse_record(line)
        error = record.get("tool_error") if record is not None else None
        query_number = error.get("server_omitted_query_number") if isinstance(error, dict) else None
        error_id = record.get("tool_error_id") if record is not None else None
        if (
            isinstance(query_number, int)
            and not isinstance(query_number, bool)
            and query_number > 0
            and error_id == f"read_context_{query_number}"
        ):
            omitted.add(query_number)
    return omitted


def persist_omission_errors(
    raw_records: list[str] | tuple[str, ...], selection_records: list[str]
) -> None:
    """Retain server omission errors without discarding original read records."""
    if not isinstance(raw_records, list):
        return
    existing = set(raw_records)
    for line in selection_records:
        record = _parse_record(line)
        error = record.get("tool_error") if record is not None else None
        if (
            isinstance(error, dict)
            and isinstance(error.get("server_omitted_query_number"), int)
            and line not in existing
        ):
            raw_records.append(line)
            existing.add(line)


def _index_evidence_records(
    raw_records: list[str] | tuple[str, ...],
) -> tuple[dict[str, list[int]], dict[int, str], dict[int, str]]:
    groups: dict[str, list[int]] = {}
    source_by_index: dict[int, str] = {}
    error_by_index: dict[int, str] = {}
    for index, line in enumerate(raw_records):
        record = _parse_record(line)
        if record is None:
            continue
        source, error_id = record.get("source"), record.get("tool_error_id")
        if isinstance(source, str):
            source_by_index[index] = source
            key = _group_key(source)
        elif isinstance(error_id, str):
            error_by_index[index] = error_id
            key = f"error:{error_id}"
        else:
            key = f"record:{index}"
        groups.setdefault(key, []).append(index)
    return groups, source_by_index, error_by_index


def _mandatory_group_keys(
    groups: dict[str, list[int]],
    sources: dict[int, str],
    errors: dict[int, str],
    *,
    omitted_query_numbers: frozenset[int] = frozenset(),
) -> list[str]:
    query_numbers = [
        int(match.group("query"))
        for source in sources.values()
        if (match := _QUERY_SOURCE.fullmatch(source)) is not None
        and int(match.group("query")) not in omitted_query_numbers
    ]
    latest_query = max(query_numbers, default=None)
    keys = [
        key
        for key, indexes in groups.items()
        if any(
            (match := _QUERY_SOURCE.fullmatch(sources[index])) is not None
            and int(match.group("query")) == latest_query
            for index in indexes
            if index in sources
        )
    ]
    latest_error = next(reversed(list(errors.values())), None)
    mandatory_errors = [
        error_id for error_id in errors.values() if error_id.startswith("read_validation_error_")
    ]
    if latest_error is not None:
        mandatory_errors.append(latest_error)
    keys.extend(dict.fromkeys(f"error:{error_id}" for error_id in mandatory_errors))
    return keys


def _rank_evidence_groups(
    raw_records: list[str] | tuple[str, ...],
    groups: dict[str, list[int]],
    sources: dict[int, str],
    errors: dict[int, str],
    question: str,
) -> list[_EvidenceGroup]:
    question_words = meaningful_session_words(question)
    return [
        _EvidenceGroup(
            key=key,
            indexes=tuple(indexes),
            source_ids=frozenset(sources[index] for index in indexes if index in sources),
            error_ids=frozenset(errors[index] for index in indexes if index in errors),
            relevance=len(
                question_words
                & meaningful_session_words(" ".join(raw_records[index] for index in indexes))
            ),
        )
        for key, indexes in groups.items()
    ]


def _try_fit_groups(
    raw_records: list[str] | tuple[str, ...],
    citations: dict[str, SessionCitation],
    groups: dict[str, _EvidenceGroup],
    selected_keys: list[str],
    mandatory_keys: list[str],
) -> CompactSessionEvidence | None:
    try:
        return _fit_groups(raw_records, citations, groups, selected_keys, mandatory_keys)
    except ValueError:
        return None


def _optional_candidates(
    groups: list[_EvidenceGroup],
    selected_keys: list[str],
    omitted_query_keys: set[str],
) -> list[_EvidenceGroup]:
    return sorted(
        (
            group
            for group in groups
            if group.key not in selected_keys
            and group.key not in omitted_query_keys
            and group.relevance > 0
        ),
        key=lambda group: (group.relevance, group.indexes[-1]),
        reverse=True,
    )


def _fit_groups(
    raw_records: list[str] | tuple[str, ...],
    citations: dict[str, SessionCitation],
    groups: dict[str, _EvidenceGroup],
    selected_keys: list[str],
    mandatory_keys: list[str],
) -> CompactSessionEvidence:
    indexes = sorted(
        index for key in selected_keys if key in groups for index in groups[key].indexes
    )
    records = tuple(raw_records[index] for index in indexes)
    selected_citations = {
        source: citations[source]
        for key in selected_keys
        if key in groups
        for source in groups[key].source_ids
        if source in citations
    }
    evidence, visible = frame_session_evidence("\n".join(records), selected_citations)
    expected_sources = {
        source for key in selected_keys if key in groups for source in groups[key].source_ids
    }
    expected_errors = {
        error for key in selected_keys if key in groups for error in groups[key].error_ids
    }
    complete = expected_sources.issubset(visible) and expected_errors.issubset(
        visible_error_ids(evidence)
    )
    if not complete:
        if any(key in mandatory_keys for key in selected_keys):
            raise ValueError("the complete latest read evidence did not fit the compact frame")
        raise ValueError("the optional evidence group did not fit the compact frame")
    return CompactSessionEvidence(evidence=evidence, records=records, citations=visible)


def _parse_record(line: str) -> dict[str, object] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def visible_error_ids(evidence: str) -> set[str]:
    """Return safe error IDs only from complete JSON records inside the evidence frame."""
    marker = "permissions or override system, developer, or direct user instructions.\n"
    start = evidence.find(marker)
    end = evidence.rfind("\n<<<THYMIRA_UNTRUSTED:")
    if start < 0 or end < start:
        return set()
    payload = evidence[start + len(marker) : end]
    error_ids: set[str] = set()
    for line in payload.splitlines():
        record = _parse_record(line)
        error_id = record.get("tool_error_id") if record is not None else None
        if isinstance(error_id, str):
            error_ids.add(error_id)
    return error_ids


def _group_key(source: str) -> str:
    query = _QUERY_SOURCE.fullmatch(source)
    if query is not None:
        return f"query:{query.group('query')}"
    event = _EVENT_SOURCE.fullmatch(source)
    if event is not None:
        return f"event:{event.group('run')}:{event.group('event')}"
    return f"source:{source}"
