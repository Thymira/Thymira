"""Narrow, read-only recovery for one recognized malformed glob proposal."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from thymira.core.session_chat_compaction import visible_error_ids
from thymira.core.session_chat_context import frame_session_evidence
from thymira.core.session_chat_model import propose_reply, selected_model_from_log
from thymira.core.session_chat_read_validation import RecoverableEmptyGlobProposalError
from thymira.events import canonical_json

if TYPE_CHECKING:
    from collections.abc import Callable

    from thymira.agents.llm.base import LLMProvider
    from thymira.core.session_chat_compaction import CompactRequestSurface
    from thymira.schemas import Session
    from thymira.schemas.session_chat import SessionChatProposal, SessionCitation, SessionDiscussion
    from thymira.state.session_discussions import SessionProviderLog


def propose_with_read_recovery(
    session: Session,
    discussion: SessionDiscussion,
    evidence: str,
    history: str,
    log: SessionProviderLog,
    provider: LLMProvider | None,
    datasets: str,
    *,
    raw_records: list[str],
    citations: dict[str, SessionCitation],
    allowed_source_ids: tuple[str, ...],
    clarification_context: tuple[str, ...],
    evidence_question: str,
    selected_model: str | None = None,
    compact_surface: CompactRequestSurface | None = None,
    compact_surface_builder: Callable[
        [list[str], dict[str, SessionCitation]], CompactRequestSurface
    ]
    | None = None,
) -> tuple[SessionChatProposal, str, dict[str, SessionCitation]]:
    """Retry one approved recovery shape with a fresh lookup-only proposal schema."""
    try:
        proposal = propose_reply(
            session,
            discussion,
            evidence,
            history,
            log,
            provider,
            datasets,
            allowed_source_ids=allowed_source_ids,
            clarification_context=clarification_context,
            evidence_question=evidence_question,
            selected_model=selected_model,
            compact_surface=compact_surface,
        )
    except RecoverableEmptyGlobProposalError:
        if _empty_glob_recovery_used(raw_records):
            raise
        error_id = "read_validation_error_1"
        raw_records.append(
            canonical_json(
                {
                    "tool_error_id": error_id,
                    "tool_error": {
                        "operation": "glob",
                        "code": RecoverableEmptyGlobProposalError.code,
                        "message": RecoverableEmptyGlobProposalError.guidance,
                    },
                }
            )
        )
        if compact_surface_builder is None:
            corrected_evidence, visible_citations = frame_session_evidence(
                "\n".join(raw_records), citations
            )
            if error_id not in visible_error_ids(corrected_evidence) or not set(
                allowed_source_ids
            ).issubset(visible_citations):
                raise ValueError("the read correction error did not fit bounded evidence") from None
            corrected_surface = None
        else:
            corrected_surface = compact_surface_builder(raw_records, citations)
            corrected_evidence = corrected_surface.evidence.evidence
            visible_citations = corrected_surface.evidence.citations
            if not any(
                _record_has_error(line, error_id) for line in corrected_surface.evidence.records
            ):
                raise ValueError("the read correction error did not fit compact evidence") from None
        corrected = propose_reply(
            session,
            discussion,
            corrected_evidence,
            history,
            log,
            provider,
            datasets,
            allowed_source_ids=tuple(sorted(visible_citations)),
            selected_model=selected_model_from_log(log),
            read_only_correction=True,
            clarification_context=clarification_context,
            evidence_question=evidence_question,
            compact_surface=corrected_surface,
        )
        return corrected, corrected_evidence, visible_citations
    else:
        visible_citations = (
            compact_surface.evidence.citations
            if compact_surface
            else {source: citations[source] for source in allowed_source_ids if source in citations}
        )
        return proposal, evidence, visible_citations


def _record_has_error(line: str, error_id: str) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(record, dict) and record.get("tool_error_id") == error_id


def _empty_glob_recovery_used(records: list[str]) -> bool:
    """Keep the single correction allowance across every model call in this discussion."""
    for record in records:
        try:
            value = json.loads(record)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and isinstance(value.get("tool_error"), dict)
            and value["tool_error"].get("code") == RecoverableEmptyGlobProposalError.code
        ):
            return True
    return False
