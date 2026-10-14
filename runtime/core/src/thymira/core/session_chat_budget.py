"""Durable request accounting and pre-dispatch token projections for SessionChat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.agents.usage import UsageLimitExceededError, UsageLimits
from thymira.schemas import Actor, EventType
from thymira.schemas.session_chat import MAX_SESSION_READ_QUERIES

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from thymira.agents.llm.base import LLMResponse
    from thymira.state.session_discussions import SessionProviderLog

MAX_SESSION_MODEL_REQUESTS = MAX_SESSION_READ_QUERIES + 2
MAX_SESSION_MODEL_TOKENS = 16_000
SESSION_INPUT_ESTIMATE_MARGIN = 384
SESSION_MAX_OUTPUT_TOKENS = 1_600
SESSION_MIN_OUTPUT_TOKENS = 256
SESSION_REPAIR_DRAFT_ALLOWANCE_TOKENS = 384
_SESSION_USAGE_LIMITS = UsageLimits(
    max_requests=MAX_SESSION_MODEL_REQUESTS,
    max_tokens=MAX_SESSION_MODEL_TOKENS,
    max_tool_calls=0,
)


class SessionChatBudgetError(UsageLimitExceededError):
    """A SessionChat request cannot be safely prepared or settled within its turn budget."""

    def __init__(
        self,
        purpose: str,
        *,
        reason: str,
        used_tokens: int,
        available_tokens: int,
        estimated_input_tokens: int | None = None,
        projected_tokens: int | None = None,
        request_count: int = 0,
        usage_unknown: bool = False,
    ) -> None:
        self.purpose = purpose
        self.reason = reason
        self.used_tokens = used_tokens
        self.available_tokens = available_tokens
        self.estimated_input_tokens = estimated_input_tokens
        self.projected_tokens = projected_tokens
        self.request_count = request_count
        self.usage_unknown = usage_unknown
        message = (
            f"{purpose} request budget refused: {reason}; {used_tokens} tokens used, "
            f"{available_tokens} available"
        )
        if estimated_input_tokens is not None:
            message += f", input estimate {estimated_input_tokens}"
        if projected_tokens is not None:
            message += f", projected request {projected_tokens}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SessionUsageSnapshot:
    """Fold request and usage events without treating a missing measurement as free."""

    requests: int
    tokens: int
    usage_unknown: bool

    @property
    def available_tokens(self) -> int:
        """Return the remaining allowance, or zero when any dispatched usage is unknown."""
        if self.usage_unknown:
            return 0
        return max(0, MAX_SESSION_MODEL_TOKENS - self.tokens)


@dataclass(frozen=True, slots=True)
class PreparedSessionRequest:
    """The input estimate, safety margin and output cap approved before one dispatch."""

    purpose: str
    model: str
    requests_before: int
    tokens_before: int
    available_tokens: int
    estimated_input_tokens: int
    margin_tokens: int
    output_limit_tokens: int
    projected_tokens: int
    repair_projection_tokens: int = 0
    reserved_for_repair_tokens: int = 0
    finalization_reserve_tokens: int = 0


def _inclusive_usage_lower_bound(
    input_tokens: int, output_tokens: int, cached_input_tokens: int, reasoning_output_tokens: int
) -> int:
    """Count each reported subset as a lower bound when inclusive totals are unreliable."""
    return max(input_tokens, cached_input_tokens) + max(output_tokens, reasoning_output_tokens)


def session_usage_snapshot(  # noqa: PLR0912, PLR0915  # each branch validates durable usage provenance
    log: SessionProviderLog, *, ignore_unsettled_request_ids: frozenset[str] = frozenset()
) -> SessionUsageSnapshot:
    """Rebuild durable request count and inclusive provider usage from the Session event chain."""
    events = log.events()
    requests: list[tuple[str, int]] = []
    for event in events:
        if event.type is not EventType.MODEL_REQUEST_RECORDED:
            continue
        request_id = event.subject_id
        if not isinstance(request_id, str) or not request_id:
            request_id = event.payload.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("Session provider request has no durable request ID")
        if any(existing == request_id for existing, _ in requests):
            raise ValueError("Session provider request ID is duplicated")
        requests.append((request_id, event.seq))

    usage_for: dict[str, tuple[int, bool]] = {}
    usage_unknown = False
    tokens = 0
    for event in events:
        if event.type is not EventType.SESSION_MODEL_USAGE:
            continue
        payload = event.payload
        request_id = payload.get("request_id")
        if request_id is None:
            # Legacy usage records did not carry a request ID. Bind each to its nearest earlier
            # not-yet-settled request while still counting requests from RequestLedger events.
            request_id = next(
                (
                    candidate
                    for candidate, seq in reversed(requests)
                    if seq < event.seq and candidate not in usage_for
                ),
                None,
            )
        if not isinstance(request_id, str) or request_id not in {item[0] for item in requests}:
            raise ValueError("Session model usage is not linked to a recorded request")
        if request_id in usage_for:
            raise ValueError("Session request usage was charged more than once")
        input_tokens = payload.get("input_tokens")
        output_tokens = payload.get("output_tokens")
        cached_input_tokens = payload.get("cached_input_tokens", 0)
        reasoning_output_tokens = payload.get("reasoning_output_tokens", 0)
        if (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or input_tokens < 0
            or not isinstance(output_tokens, int)
            or isinstance(output_tokens, bool)
            or output_tokens < 0
            or not isinstance(cached_input_tokens, int)
            or isinstance(cached_input_tokens, bool)
            or cached_input_tokens < 0
            or not isinstance(reasoning_output_tokens, int)
            or isinstance(reasoning_output_tokens, bool)
            or reasoning_output_tokens < 0
        ):
            raise ValueError("Session model usage evidence is invalid")
        status = payload.get("usage_status", "measured")
        if status == "unknown":
            reserved = payload.get("conservative_reserved_tokens")
            if not isinstance(reserved, int) or isinstance(reserved, bool) or reserved < 0:
                raise ValueError("unknown Session usage lacks a conservative reservation")
            observed = _inclusive_usage_lower_bound(
                input_tokens,
                output_tokens,
                cached_input_tokens,
                reasoning_output_tokens,
            )
            # The raw counts may be incomplete, but they are still a hard lower bound. The
            # reservation already represents the remaining balance, so use the larger value
            # rather than adding those possibly overlapping counts twice.
            tokens += max(reserved, observed)
            usage_unknown = True
            usage_for[request_id] = (reserved, True)
        elif status in {"measured", "scripted_estimate", "scripted_explicit"}:
            charged = input_tokens + output_tokens
            tokens += charged
            usage_for[request_id] = (charged, False)
        else:
            raise ValueError("Session model usage status is invalid")

    settled = set(usage_for)
    if any(
        request_id not in settled and request_id not in ignore_unsettled_request_ids
        for request_id, _ in requests
    ):
        # Recovery will append an explicit unknown-usage charge before refusing any new request.
        usage_unknown = True
        tokens = max(tokens, MAX_SESSION_MODEL_TOKENS)
    return SessionUsageSnapshot(len(requests), tokens, usage_unknown)


def prepare_session_request(
    log: SessionProviderLog,
    model: str,
    prompt: str,
    system: str,
    schema: type[BaseModel],
    purpose: str,
    estimate_input_tokens: Callable[[str, str, str, type[BaseModel]], int],
    *,
    repair_projection_tokens: int = 0,
    finalization_reserve_tokens: int = 0,
) -> PreparedSessionRequest:
    """Reserve a request only when its input, margin and useful output fit the durable balance."""
    snapshot = session_usage_snapshot(log)
    if snapshot.requests >= MAX_SESSION_MODEL_REQUESTS:
        raise SessionChatBudgetError(
            purpose,
            reason=f"the turn already used its {MAX_SESSION_MODEL_REQUESTS} requests",
            used_tokens=snapshot.tokens,
            available_tokens=snapshot.available_tokens,
            request_count=snapshot.requests,
            usage_unknown=snapshot.usage_unknown,
        )
    if snapshot.usage_unknown:
        raise SessionChatBudgetError(
            purpose,
            reason="a previous provider request has unknown token usage",
            used_tokens=snapshot.tokens,
            available_tokens=0,
            request_count=snapshot.requests,
            usage_unknown=True,
        )
    try:
        estimated_input = estimate_input_tokens(model, prompt, system, schema)
    except Exception as exc:  # missing local tokenizer support cannot be free budget
        raise SessionChatBudgetError(
            purpose,
            reason="the local input estimate is unavailable",
            used_tokens=snapshot.tokens,
            available_tokens=snapshot.available_tokens,
            request_count=snapshot.requests,
        ) from exc
    if (
        not isinstance(estimated_input, int)
        or isinstance(estimated_input, bool)
        or estimated_input < 0
    ):
        raise ValueError("Session input estimator returned an invalid token count")
    available = snapshot.available_tokens
    if (
        not isinstance(repair_projection_tokens, int)
        or isinstance(repair_projection_tokens, bool)
        or repair_projection_tokens < 0
    ):
        raise ValueError("Session repair projection must be a non-negative token count")
    if (
        not isinstance(finalization_reserve_tokens, int)
        or isinstance(finalization_reserve_tokens, bool)
        or finalization_reserve_tokens < 0
    ):
        raise ValueError("Session finalization reserve must be a non-negative token count")
    minimum_projection = (
        estimated_input
        + SESSION_INPUT_ESTIMATE_MARGIN
        + SESSION_MIN_OUTPUT_TOKENS
        + finalization_reserve_tokens
    )
    if minimum_projection > available:
        raise SessionChatBudgetError(
            purpose,
            reason=(
                "context and safety margin leave less than the minimum useful output allowance"
                if finalization_reserve_tokens == 0
                else (
                    "context leaves insufficient room for this request and its reserved "
                    "final answer"
                )
            ),
            used_tokens=snapshot.tokens,
            available_tokens=available,
            estimated_input_tokens=estimated_input,
            projected_tokens=minimum_projection,
            request_count=snapshot.requests,
        )
    spare_after_minimum = available - minimum_projection
    reserve_repair = (
        repair_projection_tokens
        if repair_projection_tokens > 0 and repair_projection_tokens <= spare_after_minimum
        else 0
    )
    output_limit = min(
        SESSION_MAX_OUTPUT_TOKENS,
        available
        - estimated_input
        - SESSION_INPUT_ESTIMATE_MARGIN
        - finalization_reserve_tokens
        - reserve_repair,
    )
    projected = estimated_input + SESSION_INPUT_ESTIMATE_MARGIN + output_limit
    return PreparedSessionRequest(
        purpose=purpose,
        model=model,
        requests_before=snapshot.requests,
        tokens_before=snapshot.tokens,
        available_tokens=available,
        estimated_input_tokens=estimated_input,
        margin_tokens=SESSION_INPUT_ESTIMATE_MARGIN,
        output_limit_tokens=output_limit,
        projected_tokens=projected,
        repair_projection_tokens=repair_projection_tokens,
        reserved_for_repair_tokens=reserve_repair,
        finalization_reserve_tokens=finalization_reserve_tokens,
    )


def request_ids_without_usage(log: SessionProviderLog) -> tuple[str, ...]:
    """Return dispatched request IDs without a linked durable usage settlement."""
    events = log.events()
    request_ids = [
        (event.subject_id, event.seq)
        for event in events
        if event.type is EventType.MODEL_REQUEST_RECORDED and event.subject_id
    ]
    settled: set[str] = set()
    for event in events:
        if event.type is not EventType.SESSION_MODEL_USAGE:
            continue
        request_id = event.payload.get("request_id")
        if not isinstance(request_id, str):
            request_id = next(
                (
                    candidate
                    for candidate, seq in reversed(request_ids)
                    if seq < event.seq and candidate not in settled
                ),
                None,
            )
        if isinstance(request_id, str):
            settled.add(request_id)
    return tuple(request_id for request_id, _ in request_ids if request_id not in settled)


def _record_usage(
    log: SessionProviderLog, response: LLMResponse, prepared: PreparedSessionRequest
) -> None:
    """Durably charge trustworthy usage, or reserve the remaining balance as unknown."""
    request_ids = request_ids_without_usage(log)
    if not request_ids:
        raise ValueError("provider response has no unsettled durable request")
    request_id = request_ids[-1]
    source = response.metadata.get("session_chat_usage_source")
    explicitly_unknown = (
        response.metadata.get("usage_known") is False
        or response.metadata.get("session_chat_usage_source") == "unknown"
        or response.cached_input_tokens > response.input_tokens
        or response.reasoning_output_tokens > response.output_tokens
    )
    reliable = not explicitly_unknown and (
        response.metadata.get("usage_known") is True
        or source in {"scripted_local_estimate", "scripted_explicit"}
    )
    if not reliable:
        _record_unknown_usage(
            log,
            request_id,
            prepared.purpose,
            model=response.model,
            observed_input_tokens=response.input_tokens,
            observed_output_tokens=response.output_tokens,
            cached_input_tokens=response.cached_input_tokens,
            reasoning_output_tokens=response.reasoning_output_tokens,
        )
        snapshot = session_usage_snapshot(log)
        raise SessionChatBudgetError(
            prepared.purpose,
            reason=(
                "provider returned no reliable input and output usage; remaining balance reserved"
            ),
            used_tokens=snapshot.tokens,
            available_tokens=0,
            estimated_input_tokens=prepared.estimated_input_tokens,
            projected_tokens=prepared.projected_tokens,
            request_count=snapshot.requests,
            usage_unknown=True,
        )
    usage_status = (
        "scripted_estimate"
        if source == "scripted_local_estimate"
        else "scripted_explicit"
        if source == "scripted_explicit"
        else "measured"
    )
    log.append(
        EventType.SESSION_MODEL_USAGE,
        Actor.system(),
        {
            "request_id": request_id,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cached_input_tokens": response.cached_input_tokens,
            "reasoning_output_tokens": response.reasoning_output_tokens,
            "cost_usd": response.cost_usd,
            "scope": "session_discussion",
            "usage_status": usage_status,
            "usage_source": "provider" if usage_status == "measured" else source,
            "estimated_input_tokens": prepared.estimated_input_tokens,
            "input_estimate_margin_tokens": prepared.margin_tokens,
            "output_limit_tokens": prepared.output_limit_tokens,
            "projected_tokens": prepared.projected_tokens,
            "repair_projection_tokens": prepared.repair_projection_tokens,
            "reserved_for_repair_tokens": prepared.reserved_for_repair_tokens,
            "reserved_for_finalization_tokens": prepared.finalization_reserve_tokens,
            "estimation_note": (
                "Local token estimate covers submitted messages and separately serialized schema; "
                "remote tokenizer, schema serialization, and provider accounting can differ."
            ),
            "limits": _SESSION_USAGE_LIMITS.to_json_dict(),
        },
    )
    snapshot = session_usage_snapshot(log)
    if response.output_tokens > prepared.output_limit_tokens:
        raise SessionChatBudgetError(
            prepared.purpose,
            reason="provider returned output above the effective response cap",
            used_tokens=snapshot.tokens,
            available_tokens=max(0, MAX_SESSION_MODEL_TOKENS - snapshot.tokens),
            estimated_input_tokens=prepared.estimated_input_tokens,
            projected_tokens=prepared.projected_tokens,
            request_count=snapshot.requests,
        )
    if snapshot.tokens > MAX_SESSION_MODEL_TOKENS:
        raise SessionChatBudgetError(
            prepared.purpose,
            reason=f"measured provider usage exceeded {MAX_SESSION_MODEL_TOKENS} tokens",
            used_tokens=snapshot.tokens,
            available_tokens=0,
            estimated_input_tokens=prepared.estimated_input_tokens,
            projected_tokens=prepared.projected_tokens,
            request_count=snapshot.requests,
        )


def settle_unknown_dispatched_usage(log: SessionProviderLog, *, purpose: str) -> tuple[str, ...]:
    """Settle a request whose provider response was lost or had no reliable usage marker."""
    pending = request_ids_without_usage(log)
    for request_id in pending:
        observed_input, observed_output, cached_input, reasoning_output = _observed_chunk_usage(
            log, request_id
        )
        _record_unknown_usage(
            log,
            request_id,
            purpose,
            model=_request_model(log, request_id),
            observed_input_tokens=observed_input,
            observed_output_tokens=observed_output,
            cached_input_tokens=cached_input,
            reasoning_output_tokens=reasoning_output,
        )
    return pending


def refuse_replay_after_dispatch(log: SessionProviderLog) -> None:
    """Settle crash gaps and stop a retried discussion from dispatching another request."""
    request_ids = tuple(
        event.subject_id
        for event in log.events()
        if event.type is EventType.MODEL_REQUEST_RECORDED and event.subject_id
    )
    if not request_ids:
        return
    settled = settle_unknown_dispatched_usage(log, purpose="recovery")
    snapshot = session_usage_snapshot(log)
    raise SessionChatBudgetError(
        "recovery",
        reason=("a provider request was already dispatched; its answer will not be redispatched"),
        used_tokens=snapshot.tokens,
        available_tokens=snapshot.available_tokens,
        request_count=snapshot.requests,
        usage_unknown=snapshot.usage_unknown or bool(settled),
    )


def unknown_usage_error(log: SessionProviderLog, purpose: str) -> SessionChatBudgetError:
    """Build the user-safe terminal diagnostic after a durable unknown-usage settlement."""
    snapshot = session_usage_snapshot(log)
    return SessionChatBudgetError(
        purpose,
        reason="provider usage is unknown; the remaining turn balance was reserved",
        used_tokens=snapshot.tokens,
        available_tokens=0,
        request_count=snapshot.requests,
        usage_unknown=True,
    )


def _observed_chunk_usage(log: SessionProviderLog, request_id: str) -> tuple[int, int, int, int]:
    """Keep raw response counts visible during conservative unknown-usage recovery."""
    chunks = [
        event.payload
        for event in log.events()
        if event.type is EventType.MODEL_RESPONSE_CHUNK
        and event.payload.get("request_id") == request_id
        and event.payload.get("message") is not None
    ]
    return (
        sum(item.get("input_tokens", 0) for item in chunks),
        sum(item.get("output_tokens", 0) for item in chunks),
        sum(item.get("cached_input_tokens", 0) for item in chunks),
        sum(item.get("reasoning_output_tokens", 0) for item in chunks),
    )


def _request_model(log: SessionProviderLog, request_id: str) -> str:
    """Recover the selected model from its durable gateway contract when no response returned."""
    for event in log.events():
        if event.type is not EventType.MODEL_REQUEST_RECORDED or event.subject_id != request_id:
            continue
        gateway = event.payload.get("gateway")
        model = gateway.get("model") if isinstance(gateway, dict) else None
        if isinstance(model, str) and model:
            return model
    return "unknown"


def _record_unknown_usage(
    log: SessionProviderLog,
    request_id: str,
    purpose: str,
    *,
    model: str = "unknown",
    observed_input_tokens: int,
    observed_output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
) -> None:
    """Reserve the balance left after known calls without hiding any returned raw counts."""
    before = session_usage_snapshot(log, ignore_unsettled_request_ids=frozenset({request_id}))
    reserve = max(
        0,
        MAX_SESSION_MODEL_TOKENS - before.tokens,
    )
    log.append(
        EventType.SESSION_MODEL_USAGE,
        Actor.system(),
        {
            "request_id": request_id,
            "model": model,
            "input_tokens": observed_input_tokens,
            "output_tokens": observed_output_tokens,
            "cached_input_tokens": cached_input_tokens,
            "reasoning_output_tokens": reasoning_output_tokens,
            "scope": "session_discussion",
            "purpose": purpose,
            "usage_status": "unknown",
            "usage_source": "provider_usage_unavailable",
            "conservative_reserved_tokens": reserve,
            "limits": _SESSION_USAGE_LIMITS.to_json_dict(),
        },
    )
