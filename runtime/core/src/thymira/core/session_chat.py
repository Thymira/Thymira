"""Persist Session questions and route genuine work into the existing Run publication seam."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.core.conversations import ConversationConflictError
from thymira.core.session_chat_budget import refuse_replay_after_dispatch
from thymira.core.session_chat_clarifications import (
    clarification_context,
    dataset_inventory,
    declared_dataset_names,
    interpretation_clarification_context,
    is_bare_confirmation,
    link_pending_work_reply,
    normalize_initial_continuation,
)
from thymira.core.session_chat_context import (
    discussion_history,
    server_terminal_fallback,
    session_evidence,
)
from thymira.core.session_chat_failure import safe_session_chat_failure
from thymira.core.session_chat_model import propose_reply, selected_model_from_log
from thymira.core.session_chat_read_loop import (
    propose_with_read_queries as _propose_with_read_queries,
)
from thymira.core.session_chat_selection import evidence_selection_question
from thymira.core.session_source_excerpt import source_excerpt_reply
from thymira.events import scrub_credentials, sha256_text
from thymira.schemas import (
    ModelWorkIntent,
    is_explanatory_model_request,
    model_work_appears_only_in_quoted_text,
    new_id,
    resolve_model_work_intent,
)
from thymira.schemas.session_chat import (
    SessionChatProposal,
    SessionChatRequest,
    SessionCitation,
    SessionDiscussion,
    StoredDiscussion,
)
from thymira.state import conversation_lock
from thymira.state.session_discussions import LocalDiscussionRepository, SessionProviderLog

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from thymira.agents.llm.base import LLMProvider
    from thymira.core.conversations import ConversationWorkspace
    from thymira.policies import Policy
    from thymira.schemas import Actor, Id, SessionConversation
    from thymira.state import EventStore


@dataclass
class SessionChat:
    """Serialize discussion publication independently of analytical Run execution."""

    workspace: ConversationWorkspace
    events: EventStore
    root: Path
    provider: LLMProvider | None = None
    policy: Policy | None = None

    def detail(self, session_id: Id, *, project_id: Id) -> SessionConversation:
        """Enforce existing project scope before loading any stored discussion."""
        detail = self.workspace.detail(session_id, project_id=project_id)
        records = LocalDiscussionRepository(self.root, session_id).records()
        if any(item.project_id != project_id for item in records):
            raise ValueError("discussion belongs to another project")
        return detail.model_copy(update={"discussions": tuple(item.discussion for item in records)})

    def send(
        self,
        session_id: Id,
        request: SessionChatRequest,
        *,
        actor: Actor,
        project_id: Id,
        project_workspace: Path,
        _identity: str | None = None,
    ) -> tuple[SessionDiscussion, bool, Id | None]:
        """Persist before inference; identical retries never repeat a dispatched model call."""
        request = request.model_copy(
            update={
                "message": scrub_credentials(request.message),
                "client_message_id": _identity or sha256_text(request.client_message_id),
            }
        )
        with conversation_lock(self.root, session_id):
            detail = self.detail(session_id, project_id=project_id)
            if not detail.session.conversation:
                raise ConversationConflictError("Session is not a conversation")
            repository = LocalDiscussionRepository(self.root, session_id)
            records = repository.records()
            record = next(
                (item for item in records if item.client_message_id == request.client_message_id),
                None,
            )
            created = record is None
            if record is None:
                record = StoredDiscussion(
                    session_id=session_id,
                    project_id=project_id,
                    actor=actor,
                    client_message_id=request.client_message_id,
                    discussion=SessionDiscussion(
                        id=new_id("turn"),
                        ordinal=len(records) + 1,
                        user_message=request.message,
                        mode=request.mode,
                        kind="action" if request.mode == "action" else "clarification",
                    ),
                    action_selected=request.mode == "action",
                )
                repository.save(record)
            elif (
                record.actor != actor
                or record.discussion.user_message != request.message
                or record.discussion.mode != request.mode
            ):
                raise ConversationConflictError("retry identity belongs to another message")
            if not detail.session.title:
                self.workspace.title_from_discussion(
                    session_id, request.message, project_id=project_id
                )
            if record.discussion.status != "pending":
                return record.discussion, False, record.discussion.run_id
            if not record.action_selected:
                record = self._interpret(record, detail, repository, project_workspace)
            if record.action_selected:
                datasets = dataset_inventory(project_workspace)
                intent = resolve_model_work_intent(
                    request.message,
                    record.clarification_context,
                    dataset_names=declared_dataset_names(datasets),
                )
                explanation_only = is_explanatory_model_request(request.message)
                quoted_model_work = model_work_appears_only_in_quoted_text(request.message)
                unlinked_confirmation = (
                    is_bare_confirmation(request.message)
                    and not record.clarification_context
                    and record.clarification_parent_id is None
                )
                if (
                    intent is ModelWorkIntent.CLARIFY
                    or explanation_only
                    or quoted_model_work
                    or unlinked_confirmation
                ):
                    clarification = _model_work_clarification(
                        request.message,
                        explanation_only=explanation_only,
                        quoted_model_work=quoted_model_work,
                        unlinked_confirmation=unlinked_confirmation,
                    )
                    result = record.model_copy(
                        update={
                            "action_selected": False,
                            "work_kind": "general",
                            "clarification_scope": clarification.clarification_scope,
                            "discussion": record.discussion.model_copy(
                                update={
                                    "kind": "clarification",
                                    "status": "completed",
                                    "assistant_message": clarification.message,
                                }
                            ),
                        }
                    )
                    repository.save(result)
                    return result.discussion, created, None
                record = record.model_copy(update={"work_kind": intent.value})
                run, _ = self.workspace.send(
                    session_id,
                    request.message,
                    f"chat-{record.discussion.id}",
                    actor=actor,
                    workspace=project_workspace,
                    project_id=project_id,
                    clarification_context=record.clarification_context,
                    work_kind=record.work_kind,
                )
                result = record.discussion.model_copy(
                    update={
                        "kind": "action",
                        "status": "completed",
                        "run_id": run.id,
                    }
                )
                repository.save(record.model_copy(update={"discussion": result}))
                return result, created, run.id
            return record.discussion, created, None

    def retry(
        self,
        session_id: Id,
        discussion_id: Id,
        *,
        actor: Actor,
        project_id: Id,
        project_workspace: Path,
    ) -> tuple[SessionDiscussion, bool, Id | None]:
        """Resume a pending publication by its public ID, preserving its original actor."""
        self.detail(session_id, project_id=project_id)
        records = LocalDiscussionRepository(self.root, session_id).records()
        record = next((item for item in records if item.discussion.id == discussion_id), None)
        if record is None:
            raise KeyError(discussion_id)
        return self.send(
            session_id,
            SessionChatRequest(
                message=record.discussion.user_message,
                client_message_id=record.client_message_id,
                mode=record.discussion.mode,
            ),
            actor=actor,
            project_id=project_id,
            project_workspace=project_workspace,
            _identity=record.client_message_id,
        )

    def _interpret(
        self,
        record: StoredDiscussion,
        detail: SessionConversation,
        repository: LocalDiscussionRepository,
        project_workspace: Path,
    ) -> StoredDiscussion:
        """A failed or interrupted explanation stays honest and cannot dispatch work."""
        log = SessionProviderLog(self.root, record.session_id, record.discussion.id)
        try:
            refuse_replay_after_dispatch(log)
            evidence_question = evidence_selection_question(
                record, repository, is_bare_confirmation
            )
            raw_evidence, raw_citations = session_evidence(
                detail, self.events, question=evidence_question
            )
            datasets = dataset_inventory(project_workspace)
            linked_context = interpretation_clarification_context(record, detail, repository)
            history = discussion_history(
                repository,
                record,
                evidence_question,
                preserve_linked_work_clarification=bool(linked_context),
            )
            proposal, evidence, citations = _propose_with_read_queries(
                detail.session,
                record.discussion,
                raw_evidence,
                raw_citations=raw_citations,
                history=history,
                log=log,
                provider=self.provider,
                datasets=datasets,
                detail=detail,
                project_workspace=project_workspace,
                runtime_root=self.root.parent,
                events=self.events,
                policy=self.policy,
                clarification_context=linked_context,
                evidence_question=evidence_question,
            )
            proposal = source_excerpt_reply(
                proposal, detail, citations, self.events, self.root.parent
            )
            if _needs_citation_repair(proposal, citations):
                proposal = propose_reply(
                    detail.session,
                    record.discussion,
                    evidence,
                    history,
                    log,
                    self.provider,
                    datasets,
                    citation_repair=proposal,
                    allowed_source_ids=tuple(sorted(citations)),
                    selected_model=selected_model_from_log(log),
                    clarification_context=linked_context,
                    evidence_question=evidence_question,
                )
                proposal = source_excerpt_reply(
                    proposal, detail, citations, self.events, self.root.parent
                )
                invalid_repair = (
                    proposal.kind != "answer"
                    or proposal.continues_clarification
                    or proposal.replaces_clarification
                    or _needs_citation_repair(proposal, citations)
                )
                if invalid_repair:
                    fallback = server_terminal_fallback(
                        detail, citations, record.discussion.user_message
                    )
                    if fallback is None:
                        raise ValueError("citation repair changed the read-only answer")  # noqa: TRY301
                    proposal = SessionChatProposal(
                        kind="answer",
                        message=fallback[0],
                        sources=fallback[1],
                    )
            explanation_only = is_explanatory_model_request(record.discussion.user_message)
            quoted_model_work = model_work_appears_only_in_quoted_text(
                record.discussion.user_message
            )
            proposal, record = _resolve_human_model_work_proposal(
                proposal,
                record,
                detail,
                repository,
                datasets,
                explanation_only=explanation_only,
                quoted_model_work=quoted_model_work,
            )
            if proposal.kind == "action":
                if record.discussion.mode == "ask":
                    raise ValueError("question mode cannot authorize work")  # noqa: TRY301
                result = record.model_copy(
                    update={
                        "action_selected": True,
                        "work_kind": proposal.work_kind,
                        "discussion": record.discussion.model_copy(update={"kind": "action"}),
                    }
                )
            else:
                if (
                    not proposal.message.strip()
                    or any(source not in citations for source in proposal.sources)
                    or (proposal.kind == "answer" and citations and not proposal.sources)
                ):
                    raise ValueError("answer has no valid evidence binding")  # noqa: TRY301
                result = record.model_copy(
                    update={
                        "clarification_scope": proposal.clarification_scope,
                        "discussion": record.discussion.model_copy(
                            update={
                                "kind": proposal.kind,
                                "status": "completed",
                                "assistant_message": scrub_credentials(proposal.message),
                                "citations": tuple(
                                    citations[source] for source in dict.fromkeys(proposal.sources)
                                ),
                            }
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001  # expose only closed, safe failure categories
            import traceback  # TEMP-DEBUG

            traceback.print_exc()  # TEMP-DEBUG
            result = record.model_copy(
                update={
                    "discussion": record.discussion.model_copy(
                        update={
                            "status": "failed",
                            "error": safe_session_chat_failure(exc),
                        }
                    )
                }
            )
        repository.save(result)
        return result


def _resolve_human_model_work_proposal(
    proposal: SessionChatProposal,
    record: StoredDiscussion,
    detail: SessionConversation,
    repository: LocalDiscussionRepository,
    datasets: str,
    *,
    explanation_only: bool,
    quoted_model_work: bool,
) -> tuple[SessionChatProposal, StoredDiscussion]:
    """Bind replies and canonical work authorization to the human conversation chain."""
    if explanation_only or quoted_model_work:
        if proposal.kind == "action":
            proposal = _model_work_clarification(
                record.discussion.user_message,
                explanation_only=explanation_only,
                quoted_model_work=quoted_model_work,
            )
        elif proposal.kind == "clarification":
            proposal = proposal.model_copy(
                update={
                    "clarification_scope": "evidence",
                    "continues_clarification": False,
                    "replaces_clarification": False,
                }
            )
    proposal = normalize_initial_continuation(proposal, record, detail, repository)
    proposal, recovered_context = link_pending_work_reply(
        proposal, record, detail, repository, datasets
    )
    if proposal.continues_clarification:
        if proposal.kind == "answer" or record.discussion.mode != "auto":
            raise ValueError("only Auto work can continue a clarification")
        context, parent_id = (
            recovered_context
            if recovered_context is not None
            else clarification_context(record, detail, repository)
        )
        record = record.model_copy(
            update={
                "clarification_context": context,
                "clarification_parent_id": parent_id,
            }
        )
    human_intent = resolve_model_work_intent(
        record.discussion.user_message,
        record.clarification_context,
        dataset_names=declared_dataset_names(datasets),
    )
    unlinked_confirmation = (
        is_bare_confirmation(record.discussion.user_message)
        and not record.clarification_context
        and record.clarification_parent_id is None
    )
    if human_intent is ModelWorkIntent.CLARIFY:
        proposal = _model_work_clarification(
            record.discussion.user_message,
            explanation_only=explanation_only,
            quoted_model_work=quoted_model_work,
            unlinked_confirmation=unlinked_confirmation,
        )
    elif proposal.kind == "action":
        canonical_work_kind = {
            ModelWorkIntent.TRAIN_NEW: "train_new",
            ModelWorkIntent.EVALUATE_EXISTING: "evaluate_existing",
        }.get(human_intent, "general")
        proposal = proposal.model_copy(update={"work_kind": canonical_work_kind})
    return proposal, record


def _model_work_clarification(
    message: str,
    *,
    explanation_only: bool = False,
    quoted_model_work: bool = False,
    unlinked_confirmation: bool = False,
) -> SessionChatProposal:
    """Persist a concrete question when human model intent cannot authorize a Run."""
    spanish = bool(
        re.search(
            r"[¿¡áéíóúñ]|\b(?:quiero|necesito|entrena|entrenar|modelo|clasificador|"
            r"eval[uú]a|evaluar|haz|realiza|crea|explica)\b",
            message,
            re.IGNORECASE,
        )
    )
    if unlinked_confirmation:
        text = (
            "Esta confirmación no está vinculada a una petición pendiente. Indica la tarea "
            "completa que quieres realizar."
            if spanish
            else "This confirmation is not linked to pending work. Please state the full task you "
            "want performed."
        )
    elif explanation_only:
        text = (
            "Esto parece una pregunta conceptual. ¿Quieres una explicación en el chat o una tarea "
            "analítica concreta? No se inició ningún Run."
            if spanish
            else (
                "This reads as a conceptual question. Do you want a chat explanation or a "
                "specific analytical task? No Run was started."
            )
        )
    elif quoted_model_work:
        text = (
            "El texto citado por sí solo no autoriza trabajo. Reformula la tarea con tus propias "
            "palabras e indica el dataset y la columna objetivo."
            if spanish
            else "Quoted model instructions alone do not authorize work. Restate the task in your "
            "own words and name the dataset and target column."
        )
    else:
        text = (
            "¿Quieres entrenar un modelo nuevo o evaluar uno existente? Para entrenarlo, indica el "
            "dataset y la columna objetivo; para evaluarlo, identifica el modelo y las métricas."
            if spanish
            else "Do you want to train a new model or evaluate an existing one? For training, name "
            "the dataset and target column; for evaluation, identify the model and metrics."
        )
    return SessionChatProposal(
        kind="clarification",
        message=text,
        clarification_scope=(
            "evidence" if explanation_only or quoted_model_work or unlinked_confirmation else "work"
        ),
    )


def _needs_citation_repair(
    proposal: SessionChatProposal, citations: Mapping[str, SessionCitation]
) -> bool:
    """Require an answer to cite at least one available record before publication."""
    return (
        proposal.kind == "answer"
        and bool(citations)
        and (not proposal.sources or any(source not in citations for source in proposal.sources))
    )
