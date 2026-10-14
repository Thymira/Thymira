"""Purpose-bounded Session conversation selectors for chat prompts."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from thymira.events import canonical_json, scrub_credentials

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from thymira.schemas.run import ConversationTurn
    from thymira.schemas.session_chat import StoredDiscussion
    from thymira.state.session_discussions import LocalDiscussionRepository

MAX_RELEVANT_DISCUSSION_TURNS = 2
MAX_RELEVANT_DISCUSSION_CHARS = 8_000
MAX_RUN_DISCUSSION_TURNS = 8
_WORD = re.compile(r"[a-z0-9]+")
_MIN_QUERY_TOKEN_LENGTH = 3
_STOP_WORDS = frozenset(
    {
        "a",
        "al",
        "algo",
        "algun",
        "alguna",
        "algunos",
        "ante",
        "aqui",
        "asi",
        "como",
        "con",
        "consultar",
        "cual",
        "cuando",
        "de",
        "del",
        "donde",
        "el",
        "ella",
        "en",
        "es",
        "esa",
        "ese",
        "esta",
        "este",
        "esto",
        "hay",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "mi",
        "mis",
        "muy",
        "no",
        "o",
        "para",
        "por",
        "que",
        "se",
        "sin",
        "sobre",
        "su",
        "sus",
        "un",
        "una",
        "uno",
        "y",
        "ya",
        "the",
        "this",
        "that",
        "where",
        "how",
        "can",
        "do",
        "does",
        "is",
        "are",
        "of",
        "to",
        "for",
        "in",
        "on",
        "and",
        "or",
        "existing",
        "generated",
        "puedo",
        "quiero",
        "explain",
        "tell",
        "me",
        "about",
        "please",
        "show",
        "find",
    }
)
_WORD_ALIASES = {
    "codigo": "code",
    "lineas": "code",
    "lines": "code",
    "script": "code",
    "scripts": "code",
    "source": "code",
    "artifacts": "artifact",
    "artefacto": "artifact",
    "artefactos": "artifact",
    "modelo": "model",
    "modelos": "model",
    "model": "model",
    "models": "model",
    "metrica": "metric",
    "metricas": "metric",
    "metrics": "metric",
    "grafica": "plot",
    "graficas": "plot",
    "png": "plot",
    "plots": "plot",
    "chart": "plot",
    "charts": "plot",
    "archivo": "file",
    "archivos": "file",
    "files": "file",
    "informe": "report",
    "informes": "report",
    "reports": "report",
}


class SessionDiscussionContextError(ValueError):
    """Relevant human context cannot fit without dropping or truncating a full message."""


def meaningful_session_words(value: str) -> set[str]:
    """Normalize English/Spanish query words and underscored artifact names for ranking."""
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(
        character for character in normalized if not unicodedata.combining(character)
    ).replace("_", " ")
    return {
        _WORD_ALIASES.get(word, word)
        for word in _WORD.findall(ascii_text)
        if word not in _STOP_WORDS and len(word) >= _MIN_QUERY_TOKEN_LENGTH
    }


def select_relevant_session_turns(
    turns: Sequence[ConversationTurn],
    question: str,
    requested_run_ids: set[str],
    meaningful_words: Callable[[str], set[str]],
    trace_text_by_run: Mapping[str, str],
) -> tuple[tuple[int, ConversationTurn], ...]:
    """Select cited Runs or the few most relevant recent turns, with a latest-turn fallback."""
    candidates = tuple(turns)
    if requested_run_ids:
        return tuple(
            (index, turn)
            for index, turn in enumerate(candidates)
            if turn.run.id in requested_run_ids
        )
    query = meaningful_words(question)
    if not query:
        return ((0, candidates[0]),) if candidates else ()
    scored: list[tuple[int, int, int, ConversationTurn]] = []
    for index, turn in enumerate(candidates):
        diagnostic = turn.terminal_diagnostic
        searchable = " ".join(
            part
            for part in (
                turn.user_message,
                turn.assistant_message or "",
                turn.run.status.value,
                turn.state.condition.value,
                diagnostic.summary if diagnostic else "",
                " ".join(diagnostic.details) if diagnostic else "",
                " ".join(diagnostic.next_steps) if diagnostic else "",
                trace_text_by_run.get(turn.run.id, ""),
            )
        )
        overlap = len(query & meaningful_words(searchable))
        if overlap:
            scored.append((overlap, -index, index, turn))
    if not scored:
        return ((0, candidates[0]),) if candidates else ()
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return tuple((item[2], item[3]) for item in scored[:MAX_RELEVANT_DISCUSSION_TURNS])


def build_relevant_discussion_history(
    repository: LocalDiscussionRepository,
    current: StoredDiscussion | None,
    question: str,
    meaningful_words: Callable[[str], set[str]],
    *,
    preserve_linked_work_clarification: bool = False,
) -> str:
    """Keep only recent pertinent turns and preserve each selected human message in full."""
    records = [
        item
        for item in repository.records()
        if item.discussion.status == "completed"
        and item.discussion.kind != "action"
        and (
            current is None
            or (
                item.discussion.ordinal < current.discussion.ordinal
                and item.actor == current.actor
                and item.project_id == current.project_id
            )
        )
    ]
    if not records:
        return ""
    query = meaningful_words(question)
    scored = [
        (
            len(
                query
                & meaningful_words(
                    f"{item.discussion.user_message} {item.discussion.assistant_message or ''}"
                )
            ),
            item.discussion.ordinal,
            item,
        )
        for item in records
    ]
    relevant = [item for score, _, item in scored if score > 0]
    linked_clarification = next(
        (
            item
            for item in reversed(records)
            if preserve_linked_work_clarification
            and item.discussion.kind == "clarification"
            and item.discussion.mode == "auto"
            and item.clarification_scope == "work"
        ),
        None,
    )
    if current is None and not query:
        selected = sorted(
            records,
            key=lambda item: item.discussion.ordinal,
        )[-MAX_RUN_DISCUSSION_TURNS:]
    elif relevant:
        selected = sorted(
            sorted(relevant, key=lambda item: item.discussion.ordinal, reverse=True)[
                : MAX_RELEVANT_DISCUSSION_TURNS - int(linked_clarification is not None)
            ],
            key=lambda item: item.discussion.ordinal,
        )
    elif not query or _is_short_confirmation(question):
        selected = [max(records, key=lambda item: item.discussion.ordinal)]
    else:
        selected = []
    if linked_clarification is not None and linked_clarification not in selected:
        selected.append(linked_clarification)
        selected.sort(key=lambda item: item.discussion.ordinal)
    if not selected:
        return ""
    lines = [
        canonical_json(
            {
                "kind": item.discussion.kind,
                "mode": item.discussion.mode,
                "clarification_scope": item.clarification_scope,
                "user": item.discussion.user_message,
                "read_only_explanation_not_audited_result": _assistant_context(item),
            }
        )
        for item in selected
    ]
    if sum(map(len, lines)) + max(0, len(lines) - 1) > MAX_RELEVANT_DISCUSSION_CHARS:
        raise SessionDiscussionContextError(
            "relevant discussion context exceeds its limit; no human message was shortened"
        )
    return scrub_credentials("\n".join(lines))


def evidence_selection_question(
    record: StoredDiscussion,
    repository: LocalDiscussionRepository,
    is_bare_confirmation: Callable[[str], bool],
) -> str:
    """Resolve a bare human confirmation against its latest cited evidence discussion."""
    message = record.discussion.user_message
    if not is_bare_confirmation(message):
        return message
    previous = next(
        (
            item
            for item in reversed(repository.records())
            if item.discussion.ordinal < record.discussion.ordinal
            and item.actor == record.actor
            and item.project_id == record.project_id
            and item.discussion.status == "completed"
        ),
        None,
    )
    if previous is None or previous.action_selected or previous.discussion.run_id is not None:
        return message
    is_evidence_turn = previous.clarification_scope == "evidence" and (
        previous.discussion.kind in {"answer", "clarification"}
    )
    if not is_evidence_turn and not (
        previous.discussion.kind == "answer" and previous.discussion.citations
    ):
        return message
    citations = previous.discussion.citations
    run_ids = sorted({citation.run_id for citation in citations if citation.run_id is not None})
    project_paths = sorted(
        {
            citation.path
            for citation in citations
            if citation.run_id is None
            and citation.path is not None
            and citation.path.startswith("project/")
        }
    )
    context = "\n".join(
        part
        for part in (
            f"Prior human question: {previous.discussion.user_message}",
            f"Recorded clarification: {previous.discussion.assistant_message or ''}",
            f"Previously cited Run IDs: {', '.join(run_ids)}" if run_ids else "",
            f"Previously cited project paths: {', '.join(project_paths)}" if project_paths else "",
            f"Current human message: {message}",
        )
        if part
    )
    if len(context) > MAX_RELEVANT_DISCUSSION_CHARS:
        raise SessionDiscussionContextError(
            "confirmation context exceeds its limit; no cited path or human message was shortened"
        )
    return scrub_credentials(context)


def _assistant_context(item: StoredDiscussion) -> str:
    """Keep clarification questions intact so a short human reply still has its referent."""
    answer = item.discussion.assistant_message or ""
    return answer if item.discussion.kind == "clarification" else answer[:2_000]


def _is_short_confirmation(question: str) -> bool:
    """Recognize only bare confirmations for the discussion-history fallback."""
    return question.strip().casefold() in {
        "yes",
        "yeah",
        "yep",
        "yup",
        "ok",
        "okay",
        "sure",
        "exactly",
        "correct",
        "right",
        "si",
        "sí",
        "claro",
        "exacto",
        "de acuerdo",
        "vale",
        "por supuesto",
        "confirmo",
    }
