"""Gemini via a free Google AI Studio key: streamed vision replies over the REST API.

No SDK, nothing to install: one ``streamGenerateContent`` request per turn, read by a worker thread.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from typing import Any

from mcp_vision.buddy.conversation import Turn
from mcp_vision.buddy.prompt import screen_label
from mcp_vision.buddy.usage import Usage, from_report

API = "https://generativelanguage.googleapis.com/v1beta"
KEY_PAGE = "https://aistudio.google.com/apikey"
DEFAULT_MODEL = "gemini-flash-latest"      # current Flash alias: free tier, sees images
_LEVEL = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
_STEP = {"low": "medium", "medium": "high", "high": "high", "xhigh": "high", "max": "high"}


class GeminiError(RuntimeError):
    """Google answered with an error (a bad key, the free limit, a blocked prompt)."""


class GeminiKeyBrain:
    """Streams text for one turn from Gemini with an AI Studio key."""

    name = "gemini-api"
    label = "Gemini"
    kind = "api"
    vision = True
    last_usage: Usage | None = None

    def __init__(self, *, api_key: str, model: str = DEFAULT_MODEL, effort: str = "low", max_tokens: int = 8192,
                 timeout: float = 60.0, opener: Callable[..., Any] = urllib.request.urlopen):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.effort = effort
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.opener = opener

    async def warm(self) -> str:
        """Check the key and reach Google once before the first question."""
        found = await asyncio.to_thread(self._get, f"{API}/models/{self.model}")
        return str(found.get("displayName") or self.model)

    def request(self, *, system: str, turns: list[Turn], detailed: bool = False,
                effort: str | None = None) -> dict[str, Any]:
        """Request body (for tests); ``detailed`` thinks one step harder."""
        effort = effort or (_STEP.get(self.effort, self.effort) if detailed else self.effort)
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _contents(turns),
            "generationConfig": {"maxOutputTokens": self.max_tokens,
                                 "thinkingConfig": {"thinkingLevel": _LEVEL.get(effort, "low")}},
        }

    async def stream(self, *, system: str, turns: list[Turn], detailed: bool = False,
                     effort: str | None = None) -> AsyncIterator[str]:
        body = self.request(system=system, turns=turns, detailed=detailed, effort=effort)
        said = False
        try:
            async for text in self._stream(body):
                said = True
                yield text
        except GeminiError as exc:
            if said or "thinking" not in str(exc).lower():
                raise
            body["generationConfig"].pop("thinkingConfig", None)    # model rejects a thinking level
            async for text in self._stream(body):
                yield text

    async def _stream(self, body: dict[str, Any]) -> AsyncIterator[str]:
        self.last_usage = None
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        stop = threading.Event()
        url = f"{API}/models/{self.model}:streamGenerateContent?alt=sse"
        data = json.dumps(body).encode("utf-8")

        def put(kind: str, value: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, value))

        def worker() -> None:
            try:
                request = urllib.request.Request(url, data=data, method="POST", headers=self._headers())
                with self.opener(request, timeout=self.timeout) as response:
                    for raw in response:
                        if stop.is_set():
                            break
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("data:"):
                            put("data", line[5:].strip())
            except urllib.error.HTTPError as exc:
                put("error", _http_error(exc))
            except Exception as exc:                      # no network, timeouts
                put("error", GeminiError(f"connection failed: {exc}"))
            finally:
                put("end", None)

        threading.Thread(target=worker, daemon=True, name="plip-gemini").start()
        blocked, finish, usage, model = "", "", None, self.model
        try:
            while True:
                kind, value = await queue.get()
                if kind == "end":
                    break
                if kind == "error":
                    raise value
                try:
                    chunk = json.loads(value)
                except ValueError:
                    continue
                usage = chunk.get("usageMetadata") or usage
                model = chunk.get("modelVersion") or model
                blocked = (chunk.get("promptFeedback") or {}).get("blockReason") or blocked
                for candidate in chunk.get("candidates") or []:
                    finish = candidate.get("finishReason") or finish
                    for part in (candidate.get("content") or {}).get("parts") or []:
                        if part.get("text") and not part.get("thought"):
                            yield part["text"]
        finally:
            stop.set()
        if usage:
            self.last_usage = from_report({"promptTokens": usage.get("promptTokenCount", 0)
                                           - usage.get("cachedContentTokenCount", 0),
                                           "outputTokens": usage.get("candidatesTokenCount", 0),
                                           "cached_tokens": usage.get("cachedContentTokenCount", 0),
                                           "thoughtsTokens": usage.get("thoughtsTokenCount", 0)}, model=model)
        if blocked or finish in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}:
            yield " Sorry, I can't help with that one."
        elif finish == "MAX_TOKENS":
            yield " That's as far as I can go in one breath. Ask me to keep going."

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json", "User-Agent": "Plip"}

    def _get(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with self.opener(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from None


def check_key(key: str, opener: Callable[..., Any] = urllib.request.urlopen) -> str:
    """"" if Google takes the key, else the problem in plain words."""
    try:
        GeminiKeyBrain(api_key=key, opener=opener)._get(f"{API}/models?pageSize=1")
    except GeminiError as exc:
        text = str(exc).lower()
        if "api key" in text or "api_key" in text or "permission" in text or "403" in text or "400" in text:
            return "Google didn't accept that key. Copy it again from AI Studio and paste the whole thing."
        return "Couldn't check the key with Google right now. Try again in a moment."
    except Exception:
        return "Couldn't reach Google. Check your internet and try again."
    return ""


def _http_error(exc: urllib.error.HTTPError) -> GeminiError:
    try:
        detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", {})
        message = str(detail.get("message") or detail.get("status") or "")
        # which quota: per minute (wait) or per day (tomorrow)
        quotas = [str(violation.get("quotaId") or "") for item in detail.get("details") or [] if isinstance(item, dict)
                  for violation in item.get("violations") or [] if isinstance(violation, dict)]
    except Exception:
        message, quotas = "", []
    limit = f" [{' '.join(quota for quota in quotas if quota)}]" if any(quotas) else ""
    return GeminiError(f"Gemini {exc.code}: {message or exc.reason}{limit}"[:500])


def _contents(turns: list[Turn]) -> list[dict[str, Any]]:
    """Gemini's turns: "user" and "model", taking turns, starting with the user."""
    contents: list[dict[str, Any]] = []
    for turn in turns:
        role = "model" if turn.role == "assistant" else "user"
        parts: list[dict[str, Any]] = []
        if role == "user":
            for shot in turn.images:
                parts.append({"inlineData": {"mimeType": shot.media_type,
                                             "data": base64.standard_b64encode(shot.data).decode("ascii")}})
                parts.append({"text": screen_label(shot, len(turn.images))})
        if turn.text:
            parts.append({"text": turn.text})
        if not parts:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)            # two in a row from one side: one turn
        else:
            contents.append({"role": role, "parts": parts})
    if contents and contents[0]["role"] == "model":
        contents.insert(0, {"role": "user", "parts": [{"text": "(continuing our conversation)"}]})
    return contents


__all__ = ["DEFAULT_MODEL", "GeminiError", "GeminiKeyBrain", "KEY_PAGE", "check_key"]
