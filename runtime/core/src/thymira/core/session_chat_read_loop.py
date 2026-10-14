"""Execute bounded read-only SessionChat lookups and continue the interpretation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.core import session_chat_model
from thymira.core.project_inputs import THYMIRA_DIRECTORY
from thymira.core.session_chat_budget import (
    MAX_SESSION_MODEL_REQUESTS,
    SESSION_INPUT_ESTIMATE_MARGIN,
    SESSION_MIN_OUTPUT_TOKENS,
    SessionChatBudgetError,
    session_usage_snapshot,
)
from thymira.core.session_chat_compaction import build_budgeted_compact_request_surface
from thymira.core.session_chat_context import frame_session_evidence
from thymira.core.session_chat_model import selected_model_from_log
from thymira.core.session_chat_read_recovery import propose_with_read_recovery
from thymira.core.session_file_location import file_location_reply
from thymira.core.session_read_tools import SessionReadRecord, execute_session_read
from thymira.events import canonical_json, scrub_credentials
from thymira.schemas.session_chat import MAX_SESSION_READ_QUERIES

if TYPE_CHECKING:
    from pathlib import Path

    from thymira.agents.llm.base import LLMProvider
    from thymira.core.session_chat_compaction import CompactRequestSurface
    from thymira.policies import Policy
    from thymira.schemas import Session, SessionConversation
    from thymira.schemas.session_chat import SessionChatProposal, SessionCitation, SessionDiscussion
    from thymira.state import EventStore
    from thymira.state.session_discussions import SessionProviderLog


def propose_with_read_queries(
    session: Session,
    discussion: SessionDiscussion,
    raw_evidence: str,
    *,
    raw_citations: dict[str, SessionCitation],
    history: str,
    log: SessionProviderLog,
    provider: LLMProvider | None,
    datasets: str,
    detail: SessionConversation,
    project_workspace: Path,
    runtime_root: Path,
    events: EventStore,
    policy: Policy | None,
    clarification_context: tuple[str, ...],
    evidence_question: str,
) -> tuple[SessionChatProposal, str, dict[str, SessionCitation]]:
    """Resolve at most four server-executed read requests before returning a user reply."""
    raw_records = [line for line in raw_evidence.splitlines() if line.strip()]
    citations = dict(raw_citations)
    evidence, visible_citations = frame_session_evidence("\n".join(raw_records), citations)
    proposal, evidence, visible_citations = propose_with_read_recovery(
        session,
        discussion,
        evidence,
        history,
        log,
        provider,
        datasets,
        raw_records=raw_records,
        citations=citations,
        allowed_source_ids=tuple(sorted(visible_citations)),
        clarification_context=clarification_context,
        evidence_question=evidence_question,
    )
    query_count = 0
    limit_error_sent = False

    def compact_surface_builder(
        current_records: list[str],
        current_citations: dict[str, SessionCitation],
        *,
        force_answer_only: bool = False,
    ) -> CompactRequestSurface:
        snapshot = session_usage_snapshot(log)
        remaining_requests = MAX_SESSION_MODEL_REQUESTS - snapshot.requests
        surface_answer_only = force_answer_only or remaining_requests <= 1
        model = selected_model_from_log(log)
        return build_budgeted_compact_request_surface(
            discussion,
            current_records,
            current_citations,
            history,
            question=evidence_question,
            answer_only=surface_answer_only,
            clarification_context=clarification_context,
            evidence_question=evidence_question,
            model=model,
            available_tokens=snapshot.available_tokens,
            estimate_input_tokens=session_chat_model.estimate_structured_input_tokens,
        )

    while proposal.kind == "lookup":
        request = proposal.lookup
        if request is None:
            raise ValueError("lookup proposal omitted its read request")
        snapshot = session_usage_snapshot(log)
        if snapshot.requests >= MAX_SESSION_MODEL_REQUESTS:
            raise SessionChatBudgetError(
                "interpretation",
                reason="no model request slot remains for a final answer after this lookup",
                used_tokens=snapshot.tokens,
                available_tokens=snapshot.available_tokens,
                request_count=snapshot.requests,
                usage_unknown=snapshot.usage_unknown,
            )
        final_surface = compact_surface_builder(raw_records, citations, force_answer_only=True)
        final_minimum = (
            (final_surface.estimated_input_tokens or 0)
            + SESSION_INPUT_ESTIMATE_MARGIN
            + SESSION_MIN_OUTPUT_TOKENS
        )
        if snapshot.usage_unknown or final_minimum > snapshot.available_tokens:
            raise SessionChatBudgetError(
                "interpretation",
                reason="the remaining balance cannot fit a final answer after this lookup",
                used_tokens=snapshot.tokens,
                available_tokens=snapshot.available_tokens,
                estimated_input_tokens=final_surface.estimated_input_tokens,
                projected_tokens=final_minimum,
                request_count=snapshot.requests,
                usage_unknown=snapshot.usage_unknown,
            )
        location_records: list[tuple[str, SessionReadRecord]] = []
        if query_count >= MAX_SESSION_READ_QUERIES:
            if limit_error_sent:
                raise ValueError("Session read query limit was exceeded")
            raw_records.append(
                canonical_json(
                    {
                        "tool_error_id": "read_query_limit",
                        "tool_error": {
                            "operation": request.operation,
                            "message": "The maximum number of read queries has been reached.",
                        },
                    }
                )
            )
            limit_error_sent = True
        else:
            if policy is None:
                raise ValueError("Session read policy is unavailable")
            outcome = execute_session_read(
                request,
                detail=detail,
                # API callers pass ProjectResolution.workspace (.thymira); in-process callers
                # may already pass the project root. Match dataset_inventory's normalization.
                project_root=(
                    project_workspace.parent
                    if project_workspace.name == THYMIRA_DIRECTORY
                    else project_workspace
                ),
                runtime_root=runtime_root,
                events=events,
                policy=policy,
                log=log,
            )
            query_count += 1
            if outcome.error is not None:
                error_id = f"read_error_{query_count}"
                raw_records.append(
                    canonical_json(
                        {
                            "tool_error_id": error_id,
                            "tool_error": {
                                "operation": request.operation,
                                "message": outcome.error,
                            },
                        }
                    )
                )
            else:
                new_records: list[str] = []
                new_citations: dict[str, SessionCitation] = {}
                for index, result in enumerate(outcome.records, start=1):
                    source = (
                        f"q{query_count}"
                        if len(outcome.records) == 1
                        else f"q{query_count}_{index}"
                    )
                    value = {"source": source, **result.value}
                    new_records.append(scrub_credentials(canonical_json(value)))
                    new_citations[source] = result.citation
                    location_records.append((source, result))
                raw_records.extend(new_records)
                citations.update(new_citations)
        compact_surface = compact_surface_builder(raw_records, citations)
        if (
            location_reply := file_location_reply(
                discussion.user_message,
                location_records,
                compact_surface.evidence.citations,
                has_linked_clarification=bool(clarification_context),
            )
        ) is not None:
            return (
                location_reply,
                compact_surface.evidence.evidence,
                compact_surface.evidence.citations,
            )
        proposal, evidence, visible_citations = propose_with_read_recovery(
            session,
            discussion,
            compact_surface.evidence.evidence,
            history,
            log,
            provider,
            datasets,
            raw_records=raw_records,
            citations=citations,
            allowed_source_ids=compact_surface.evidence.allowed_source_ids,
            clarification_context=clarification_context,
            evidence_question=evidence_question,
            selected_model=selected_model_from_log(log),
            compact_surface=compact_surface,
            compact_surface_builder=compact_surface_builder,
        )
    return proposal, evidence, visible_citations
