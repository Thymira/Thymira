"""Scope-neutral types shared by the provider ledger and its adapters."""

from dataclasses import dataclass

from typing_extensions import TypeVar

from thymira.events import EventLog
from thymira.events.provider_evidence import ProviderEvidence, ProviderEvidenceLog
from thymira.schemas import ProviderRequest

LogT_co = TypeVar("LogT_co", bound=ProviderEvidenceLog, default=EventLog, covariant=True)


@dataclass(frozen=True, slots=True)
class RequestLease:
    """The durable request identity returned immediately before provider dispatch."""

    request: ProviderRequest
    event: ProviderEvidence
