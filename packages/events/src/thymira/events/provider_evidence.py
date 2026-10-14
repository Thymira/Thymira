"""The provider ledger's narrow append seam for Run or Session evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from thymira.schemas import Actor, Event, EventType
from thymira.schemas.session_chat import SessionModelEvent

if TYPE_CHECKING:
    from collections.abc import Sequence

ProviderEvidence = Event | SessionModelEvent


class ProviderEvidenceLog(Protocol):
    """Only evidence operations used by RequestLedger; no Run transition authority."""

    def events(self) -> Sequence[ProviderEvidence]:
        """Return the scope's verified records in order."""
        ...

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
    ) -> ProviderEvidence:
        """Publish one record before dispatch or after a provider outcome."""
        ...
