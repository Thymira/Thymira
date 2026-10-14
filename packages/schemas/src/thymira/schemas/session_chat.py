"""Session discussion contracts, deliberately separate from analytical Run lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import Field, model_validator

from thymira.schemas.actor import Actor
from thymira.schemas.base import ThymiraModel, utc_now
from thymira.schemas.conversation import ClarificationContext
from thymira.schemas.enums import EventType
from thymira.schemas.ids import Id

ChatMode = Literal["auto", "ask", "action"]
WorkKind = Literal["general", "evaluate_existing", "train_new"]
ClarificationScope = Literal["work", "evidence"]
SessionReadOperation = Literal["list_files", "read_file", "glob", "grep"]
SESSION_READ_OPERATIONS: tuple[SessionReadOperation, ...] = get_args(SessionReadOperation)
MAX_SESSION_READ_QUERIES = 4


class SessionChatRequest(ThymiraModel):
    """A retryable user message with an optional explicit interpretation."""

    message: str = Field(min_length=1, max_length=16_000)
    client_message_id: str = Field(min_length=1, max_length=128)
    mode: ChatMode = "auto"


class SessionReadRequest(ThymiraModel):
    """One bounded, read-only query over the Session's virtual evidence tree."""

    operation: SessionReadOperation
    path: str = Field(default="", max_length=1024)
    pattern: str | None = Field(default=None, max_length=500)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=32)

    @model_validator(mode="before")
    @classmethod
    def drop_unused_pattern(cls, data: object) -> object:
        """Ignore a pattern a structured-output model filled in for an operation that has none."""
        if (
            isinstance(data, dict)
            and data.get("operation") in {"list_files", "read_file"}
            and data.get("pattern") is not None
        ):
            return {**data, "pattern": None}
        return data

    @model_validator(mode="after")
    def validate_operation_arguments(self) -> SessionReadRequest:
        """Require only the arguments used by each fixed operation."""
        if self.operation in {"glob", "grep"} and not self.pattern:
            raise ValueError(f"{self.operation} requires a non-empty literal pattern")
        if self.operation in {"list_files", "read_file"} and self.pattern is not None:
            raise ValueError(f"{self.operation} does not accept a pattern")
        return self


class SessionCitation(ThymiraModel):
    """A server-resolved reference into this Session's verified evidence."""

    run_id: Id | None = None
    seq: int | None = Field(default=None, ge=0)
    path: str | None = Field(default=None, min_length=1, max_length=1024)
    label: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_reference(self) -> SessionCitation:
        """Bind a citation to an actual Run event or a server-resolved virtual path."""
        if self.run_id is None and self.path is None:
            raise ValueError("a Session citation requires a Run or path reference")
        return self


class SessionDiscussion(ThymiraModel):
    """A persisted explanation, clarification or reference to governed work."""

    id: Id
    ordinal: int = Field(ge=1)
    user_message: str = Field(min_length=1, max_length=16_000)
    mode: ChatMode
    kind: Literal["answer", "clarification", "action"] = "clarification"
    status: Literal["pending", "completed", "failed"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    assistant_message: str | None = Field(default=None, max_length=6000)
    run_id: Id | None = None
    citations: tuple[SessionCitation, ...] = Field(default=(), max_length=12)
    error: str | None = Field(default=None, max_length=500)


class StoredDiscussion(ThymiraModel):
    """Private actor/retry bindings never exposed as a client-owned transcript."""

    session_id: Id
    project_id: Id
    actor: Actor
    client_message_id: str = Field(min_length=1, max_length=128)
    discussion: SessionDiscussion
    action_selected: bool = False
    work_kind: WorkKind = "general"
    clarification_scope: ClarificationScope = "work"
    clarification_context: ClarificationContext = ()
    clarification_parent_id: Id | None = None


class SourceExcerptRequest(ThymiraModel):
    """Select registered source text for a bounded, server-rendered read-only quotation."""

    source: str = Field(min_length=1, max_length=80)
    contains: str = Field(default="", max_length=160)


class SessionChatProposal(ThymiraModel):
    """An LLM interpretation; only code can publish work through RunService."""

    kind: Literal["answer", "clarification", "action", "lookup"]
    message: str = Field(max_length=6000)
    sources: tuple[str, ...] = Field(default=(), max_length=12)
    work_kind: WorkKind = "general"
    clarification_scope: ClarificationScope = "work"
    lookup: SessionReadRequest | None = None
    source_excerpt: SourceExcerptRequest | None = None
    continues_clarification: bool = False
    replaces_clarification: bool = Field(
        default=False,
        description=(
            "True only when the current user explicitly abandons the pending work request "
            "and supplies a different request. Never true for an answer or confirmation."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def drop_unused_fields(cls, data: object) -> object:
        """Ignore placeholders a structured-output model fills in for fields its kind never uses.

        A lookup is internal and never published, so it carries neither sources nor an excerpt;
        every other kind carries no read request. Dropping them grants no authority.
        """
        if not isinstance(data, dict):
            return data
        if data.get("kind") == "lookup":
            return {**data, "sources": (), "source_excerpt": None}
        return {**data, "lookup": None}

    @model_validator(mode="after")
    def validate_lookup_proposal(self) -> SessionChatProposal:
        """Keep lookup proposals internal and mutually exclusive with published replies."""
        if (self.kind == "lookup") != (self.lookup is not None):
            raise ValueError("only a lookup proposal may carry a read request")
        if self.kind == "lookup" and (self.sources or self.source_excerpt is not None):
            raise ValueError("a lookup proposal cannot cite sources or request a source excerpt")
        return self


class SessionModelEvent(ThymiraModel):
    """A hash-linked provider evidence record owned by a Session discussion, never a Run."""

    event_id: Id
    session_id: Id
    discussion_id: Id
    seq: int = Field(ge=0)
    type: EventType
    ts: datetime = Field(default_factory=utc_now)
    actor: Actor
    producer: str
    producer_version: str
    subject_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
