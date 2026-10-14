"""Translate structured-call instructions and bounded retry history for providers."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    RetryPromptPart,
    SystemPromptPart,
    UserPromptPart,
)

from thymira.events import scrub_credentials


def _effective_instructions(messages: list[ModelMessage]) -> str:
    """Return the current request's ``ModelRequest.instructions`` (PydanticAI's ``instructions=``).

    PydanticAI attaches the rendered instructions to `ModelRequest.instructions` (a ``str | None``,
    confirmed against pydantic_ai 2.33.0) and emits no part for them. Every ``ModelRequest`` in a
    run carries the same rendered instructions, so the most recent one is taken and emitted once,
    rather than repeated per request across a multi-turn tool history. Returns ``""`` when no
    request set instructions (a caller using ``system_prompt=`` only, or neither).
    """
    for message in reversed(messages):
        if isinstance(message, ModelRequest) and message.instructions is not None:
            return message.instructions
    return ""


def system_text(messages: list[ModelMessage]) -> str:
    """Combine PydanticAI's two system-level channels into the provider's single ``system`` string.

    PydanticAI carries system-level content two ways: ``system_prompt=`` becomes a
    `SystemPromptPart`, while ``instructions=`` becomes `ModelRequest.instructions` and emits no
    part. Every call site in this repo builds its agent with ``instructions=``, so honouring only
    `SystemPromptPart` dropped the whole behavioural contract of every `AgentSpec`. Both channels
    are collected here.

    When a caller supplies BOTH, both are kept — each is genuine system content the model must see,
    and dropping either would lose part of the contract — with the `SystemPromptPart` text first and
    the ``instructions`` string last, newline-joined. Returns ``""`` when neither is present.
    """
    system_parts: list[str] = [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    ]
    if instructions := _effective_instructions(messages):
        system_parts.append(instructions)
    return "\n".join(system_parts)


def structured_prompt(messages: list[ModelMessage]) -> tuple[str, str]:
    """Flatten PydanticAI's message history into ``(prompt, system)``.

    Structured retries must include the validation feedback emitted by PydanticAI. Dropping
    RetryPromptPart would send an identical request again while charging another completion.
    """
    prompt_parts: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                prompt_parts.append(part.content)
            elif isinstance(part, RetryPromptPart):
                prompt_parts.append(part.model_response())
    return scrub_credentials("\n".join(prompt_parts)), scrub_credentials(system_text(messages))
