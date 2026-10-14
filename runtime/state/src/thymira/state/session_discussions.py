"""Private atomic records and independently chained provider evidence for session chat."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thymira.events import canonical_json, scrub_credentials_value, sha256_text
from thymira.schemas import Actor, EventType, new_id
from thymira.schemas.session_chat import SessionModelEvent, StoredDiscussion
from thymira.state.repositories import _JsonRepository

if TYPE_CHECKING:
    from pathlib import Path


class LocalDiscussionRepository(_JsonRepository):
    """Persist one retry identity per record beneath an exact Session scope."""

    def __init__(self, root: Path, session_id: str) -> None:
        if not session_id.startswith("session_") or not session_id[8:].isalnum():
            raise ValueError("invalid Session scope")
        super().__init__(root, session_id)
        self.session_id = session_id

    def save(self, record: StoredDiscussion) -> None:
        """Replace one full record; the caller holds the Session discussion OS lock."""
        if record.session_id != self.session_id:
            raise ValueError("discussion belongs to another Session")
        self._write(record.discussion.id, record.to_json_dict())

    def records(self) -> tuple[StoredDiscussion, ...]:
        """Read private records in canonical publication order, refusing mixed scopes."""
        records = tuple(StoredDiscussion.model_validate(item) for item in self._read_all())
        if any(item.session_id != self.session_id for item in records):
            raise ValueError("discussion belongs to another Session")
        ordinals = [item.discussion.ordinal for item in records]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("duplicate discussion ordinal")
        return tuple(sorted(records, key=lambda item: item.discussion.ordinal))


class SessionProviderLog:
    """Reuse RequestLedger without minting a fictitious Run or altering analytical evidence."""

    def __init__(self, root: Path, session_id: str, discussion_id: str) -> None:
        self.repository = _JsonRepository(root, "model-evidence")
        self.session_id = session_id
        self.discussion_id = discussion_id

    def events(self) -> list[SessionModelEvent]:
        """Verify both the hash chain and ownership before returning any evidence."""
        saved = self.repository._read(self.discussion_id)  # noqa: SLF001
        if saved is None:
            return []
        raw = saved.get("events")
        if not isinstance(raw, list):
            raise TypeError("invalid Session model evidence")
        previous = "0" * 64
        result = []
        for index, item in enumerate(raw):
            event = SessionModelEvent.model_validate(item)
            digest = sha256_text(canonical_json(event.model_dump(mode="json", exclude={"hash"})))
            if (
                event.session_id != self.session_id
                or event.discussion_id != self.discussion_id
                or event.seq != index
                or event.prev_hash != previous
                or event.hash != digest
            ):
                raise ValueError("Session provider evidence failed verification")
            previous = event.hash
            result.append(event)
        return result

    def append(
        self,
        type: EventType,  # noqa: A002
        actor: Actor,
        payload: dict[str, Any] | None = None,
        *,
        subject_id: str | None = None,
        producer: str = "thymira.events",
        producer_version: str = "0.3",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> SessionModelEvent:
        """Atomically publish a chained record; an outer OS lock serializes writers."""
        events = self.events()
        event = SessionModelEvent(
            event_id=new_id("event"),
            session_id=self.session_id,
            discussion_id=self.discussion_id,
            seq=len(events),
            type=type,
            actor=actor,
            producer=producer,
            producer_version=producer_version,
            subject_id=subject_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=scrub_credentials_value(payload or {}),
            prev_hash=events[-1].hash if events else "0" * 64,
            hash="0" * 64,
        )
        event = event.model_copy(
            update={
                "hash": sha256_text(canonical_json(event.model_dump(mode="json", exclude={"hash"})))
            }
        )
        self.repository._write(  # noqa: SLF001
            self.discussion_id, {"events": [item.to_json_dict() for item in (*events, event)]}
        )
        return event
