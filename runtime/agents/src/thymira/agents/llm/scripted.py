"""A deterministic, offline LLM provider double for tests.

This provider belongs to the test surface: it has no network, keys or cost, and returns a
predetermined sequence of responses. It is not part of the provider catalog a real run may use.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from thymira.agents.llm.base import (
    BaseModelT,
    LLMCallError,
    LLMMessage,
    LLMResponse,
    LLMStructuredOutputError,
    LLMToolCall,
    LLMToolDefinition,
)
from thymira.agents.llm.litellm_provider import (
    estimate_structured_input_tokens,
    estimate_text_tokens,
)
from thymira.agents.llm.validation_feedback import validation_feedback

ScriptedItem = str | dict[str, Any] | BaseModel
ScriptedTurn = Callable[[Sequence[LLMMessage]], ScriptedItem]


def _as_text(item: ScriptedItem) -> str:
    """Render a scripted item as response text."""
    if isinstance(item, str):
        return item
    if isinstance(item, BaseModel):
        return item.model_dump_json()
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _bounded_usage(
    response: LLMResponse,
    *,
    model: str,
    prompt: str,
    system: str,
    schema: type[BaseModel],
) -> LLMResponse:
    """Use explicit scripted counts or mark a local, request-exact synthetic estimate."""
    metadata = dict(response.metadata)
    if (
        metadata.get("usage_known") is False
        or metadata.get("session_chat_usage_source") == "unknown"
    ):
        return response
    source = metadata.get("session_chat_usage_source")
    if (
        metadata.get("usage_known") is True
        or source in {"scripted_local_estimate", "scripted_explicit"}
        or response.input_tokens > 0
        or response.output_tokens > 0
    ):
        usage_source = "scripted_explicit"
    else:
        input_tokens = estimate_structured_input_tokens(model, prompt, system, schema)
        output_tokens = estimate_text_tokens(model, response.text)
        response = response.model_copy(
            update={"input_tokens": input_tokens, "output_tokens": output_tokens}
        )
        usage_source = "scripted_local_estimate"
    return response.model_copy(
        update={
            "metadata": {
                **response.metadata,
                "session_chat_usage_source": usage_source,
            }
        }
    )


class ScriptedProvider:
    """Return a fixed sequence of responses, one per call, for deterministic tests.

    Each ``complete``/``complete_structured`` call pops the next scripted item and records the
    prompt in :attr:`calls`. ``complete`` returns the item as text; ``complete_structured``
    validates it against the requested schema (a ``str`` item is parsed as JSON). A call made
    after the script is exhausted raises :class:`LLMCallError`. A callable item is resolved by
    ``complete_turn`` with the messages it is shown -- the way a scripted model reports what a
    tool actually returned.
    """

    opaque_replay_state_required = False

    def __init__(
        self,
        responses: Sequence[ScriptedItem | ScriptedTurn],
        *,
        model: str = "scripted",
        provider_name: str = "test",
    ) -> None:
        """Store the scripted responses.

        Args:
            responses: The items to return, in order. Each is a string, a mapping, a Pydantic
                model instance, or a callable resolved by ``complete_turn`` with the messages it
                is shown.
            model: The reported model name.
            provider_name: The reported provider name.
        """
        self.model = model
        self.provider_name = provider_name
        self._responses: list[ScriptedItem | ScriptedTurn] = list(responses)
        self._index = 0
        self.calls: list[dict[str, Any]] = []

    def _next(
        self, prompt: str, system: str, tools: list[dict[str, Any]] | None = None
    ) -> ScriptedItem | ScriptedTurn:
        self.calls.append({"prompt": prompt, "system": system, "tools": tools or []})
        if self._index >= len(self._responses):
            raise LLMCallError("the scripted provider ran out of responses")
        item = self._responses[self._index]
        self._index += 1
        return item

    def complete_turn(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[LLMToolDefinition] = (),
        parallel_tool_calls: bool | None = None,
    ) -> LLMResponse:
        """Return one scripted conversational response, including an optional tool call.

        The deterministic script, not a model, decides which call to emit; the tool definitions
        the caller offered are recorded on :attr:`calls` (a harness records what the model saw)
        but never steer the response.
        """
        del parallel_tool_calls
        recorded_tools = [tool.model_dump(mode="json") for tool in tools]
        prompt = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        system = "\n".join(message.content for message in messages if message.role == "system")
        item = self._next(prompt, system, recorded_tools)
        if not isinstance(item, (str, dict, BaseModel)):
            item = item(messages)
        if isinstance(item, LLMToolCall):
            return LLMResponse(
                text="",
                provider=self.provider_name,
                model=self.model,
                metadata={"api": "scripted.turn"},
                tool_calls=(item,),
            )
        return LLMResponse(
            text=_as_text(item),
            provider=self.provider_name,
            model=self.model,
            metadata={"api": "scripted.turn"},
        )

    def complete(self, prompt: str, *, system: str = "") -> LLMResponse:
        """Return the next scripted item as free text."""
        item = self._next(prompt, system)
        if not isinstance(item, (str, dict, BaseModel)):
            raise LLMCallError("a callable scripted item needs complete_turn")
        return LLMResponse(
            text=_as_text(item),
            provider=self.provider_name,
            model=self.model,
            metadata={"api": "scripted"},
        )

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModelT],
        system: str = "",
    ) -> tuple[BaseModelT, LLMResponse]:
        """Validate the next scripted item against ``schema``.

        Raises:
            LLMStructuredOutputError: If the item does not validate against ``schema``. The
                exception carries the scripted response, matching a received gateway response.
            LLMCallError: If the script is exhausted.
        """
        item = self._next(prompt, system)
        if not isinstance(item, (str, dict, BaseModel)):
            raise LLMCallError("a callable scripted item needs complete_turn")
        try:
            if isinstance(item, str):
                validated = schema.model_validate_json(item)
            elif isinstance(item, BaseModel):
                validated = schema.model_validate(item.model_dump())
            else:
                validated = schema.model_validate(item)
        except ValidationError as exc:
            response = LLMResponse(
                text=_as_text(item),
                provider=self.provider_name,
                model=self.model,
                metadata={"api": "scripted.structured", "schema": schema.__name__},
            )
            raise LLMStructuredOutputError(
                f"the scripted response does not satisfy {schema.__name__}. "
                + validation_feedback(exc, schema),
                response=response,
            ) from None
        response = LLMResponse(
            text=validated.model_dump_json(),
            provider=self.provider_name,
            model=self.model,
            metadata={"api": "scripted.structured", "schema": schema.__name__},
        )
        return validated, response

    def complete_structured_bounded(
        self,
        prompt: str,
        *,
        schema: type[BaseModelT],
        system: str = "",
        max_output_tokens: int,
    ) -> tuple[BaseModelT, LLMResponse]:
        """Expose a deterministic bounded-chat surface with explicitly synthetic usage.

        Ordinary scripted calls retain their existing zero-usage behavior. SessionChat opts into
        this method explicitly; absent caller-supplied counts, its response receives local
        LiteLLM tokenizer estimates based on the exact prompt, system and schema it was shown.
        """
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        try:
            proposal, response = self.complete_structured(prompt, schema=schema, system=system)
        except LLMStructuredOutputError as exc:
            if exc.response is None:
                raise
            response = _bounded_usage(
                exc.response,
                model=self.model,
                prompt=prompt,
                system=system,
                schema=schema,
            )
            raise LLMStructuredOutputError(str(exc), response=response) from None
        response = _bounded_usage(
            response,
            model=self.model,
            prompt=prompt,
            system=system,
            schema=schema,
        )
        self.calls[-1]["max_output_tokens"] = max_output_tokens
        return proposal, response
