"""Claude as the buddy's brain: streamed vision replies via the Anthropic SDK."""
from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

from mcp_vision.buddy.conversation import Turn
from mcp_vision.buddy.prompt import screen_label
from mcp_vision.buddy.usage import Usage, from_report

DEFAULT_MODEL = "claude-opus-5-5"
# Conversational, latency-sensitive turns do well at low effort; raise it in
# settings for harder walkthroughs.
DEFAULT_EFFORT = "low"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_EFFORT_STEP = {"low": "medium", "medium": "high", "high": "xhigh", "xhigh": "max", "max": "max"}


class ClaudeBrain:
    """Streams text deltas for one buddy turn.

    The system prompt is frozen and marked for prompt caching, so repeated
    turns only pay for the new screenshots and transcript. Refusals fall back
    server-side (``fallbacks="default"``) instead of leaving the user in
    silence.
    """

    name = "claude"
    label = "Claude API"
    kind = "api"
    vision = True
    last_usage: Usage | None = None     # what the last stream() used, from the final message

    def __init__(self, *, api_key: str | None = None, model: str = DEFAULT_MODEL,
                 effort: str = DEFAULT_EFFORT, max_tokens: int = 16000, client: Any = None,
                 timeout: float = 60.0):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.client = client

    async def warm(self) -> str:
        """Open the TLS connection and validate the key before the first question."""
        model = await self.client.models.retrieve(self.model)
        return getattr(model, "display_name", "") or self.model

    def request(self, *, system: str, turns: list[Turn], detailed: bool = False) -> dict[str, Any]:
        """The exact request body (minus transport options); handy for tests.

        ``detailed`` (a walkthrough or deeper explanation, as judged by the
        router) raises effort one step for that turn only.
        """
        effort = _EFFORT_STEP.get(self.effort, self.effort) if detailed else self.effort
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [_message(turn) for turn in turns],
            "output_config": {"effort": effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }

    async def stream(self, *, system: str, turns: list[Turn], detailed: bool = False) -> AsyncIterator[str]:
        body = self.request(system=system, turns=turns, detailed=detailed)
        async with self.client.beta.messages.stream(**body) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    yield event.delta.text
            final = await stream.get_final_message()
        self.last_usage = _usage(final, self.model)
        if final.stop_reason == "refusal":
            yield " Sorry, I can't help with that one."
        elif final.stop_reason == "max_tokens":
            yield " That's as far as I can go in one breath. Ask me to keep going."


def _usage(final: Any, model: str) -> Usage | None:
    """The final message's token counts (fallback turns bill at the model that answered)."""
    usage = getattr(final, "usage", None)
    if usage is None:
        return None
    raw = {key: getattr(usage, key, 0) or 0 for key in
           ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    return from_report(raw, model=str(getattr(final, "model", "") or model))


def _message(turn: Turn) -> dict[str, Any]:
    if turn.role == "assistant" or not turn.images:
        return {"role": turn.role, "content": turn.text}
    content: list[dict[str, Any]] = []
    for shot in turn.images:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": shot.media_type,
            "data": base64.standard_b64encode(shot.data).decode("ascii"),
        }})
        content.append({"type": "text", "text": screen_label(shot, len(turn.images))})
    content.append({"type": "text", "text": turn.text})
    return {"role": "user", "content": content}
