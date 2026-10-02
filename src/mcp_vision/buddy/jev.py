"""Minimal TypeSafe Jev (System-1) client.

Contract, as published in TypeSafe's SDK/OpenAPI spec:

    POST {base}/v1/systemone      Authorization: Bearer <TYPESAFE_API_KEY>
    {"model": "jev-latest", "state": <str | object | array>,
     "questions": {"<id>": {"type": "choice" | "noul" | "score",
                            "criteria": ..., "instructions": ...}}}

    200 {"model": "...", "usage": {"input_tokens": n, "output_tokens": n},
         "answers": {"<id>": {"type": "choice", "choice": label, "confidence": c,
                              "probabilities": {label: p}}
                    | {"type": "noul", "noul": p_yes}
                    | {"type": "score", "score": ev, "confidence": c,
                       "legend": {...}, "probabilities": {"0": p, ...}}}}

Jev is text only and answers every question in parallel against one shared
``state``; questions cannot see each other, so each carries its own meaning.
Typical latency is ~100 ms, so callers use short timeouts and fall back.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
MAX_CHOICES = 255
_RETRYABLE = {408, 429, 500, 502, 503, 504, 529}


class JevError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Choice:
    criteria: dict[str, Any]
    instructions: Any = None

    def to_json(self) -> dict[str, Any]:
        if not self.criteria:
            raise ValueError("a choice needs at least one option")
        if len(self.criteria) > MAX_CHOICES:
            raise ValueError(f"a choice allows at most {MAX_CHOICES} options")
        return _question("choice", self.criteria, self.instructions)


@dataclass(frozen=True)
class Noul:
    """Yes/no question; the answer is P(yes)."""

    instructions: Any = None
    criteria: dict[str, Any] | None = None    # optional {"true": ..., "false": ...}

    def to_json(self) -> dict[str, Any]:
        return _question("noul", self.criteria, self.instructions)


@dataclass(frozen=True)
class Score:
    """Ordinal scale; list position is the level, starting at 0."""

    criteria: list[Any]
    instructions: Any = None

    def to_json(self) -> dict[str, Any]:
        return _question("score", list(self.criteria), self.instructions)


Question = Choice | Noul | Score


def _question(kind: str, criteria: Any, instructions: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"type": kind}
    if criteria is not None:
        body["criteria"] = criteria
    if instructions is not None:
        body["instructions"] = instructions
    return body


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]

    def p(self, label: str) -> float:
        return self.probabilities.get(label, 0.0)


@dataclass
class JevResult:
    answers: dict[str, Any]
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0

    def choice(self, name: str, options: set[str] | None = None) -> ChoiceAnswer:
        answer = self._answer(name, "choice")
        probabilities = answer.get("probabilities")
        selected = answer.get("choice")
        if not isinstance(probabilities, dict) or not probabilities:
            raise JevError(f"{name}: missing probabilities")
        cleaned = {}
        for label, value in probabilities.items():
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise JevError(f"{name}: invalid probability for {label!r}")
            cleaned[str(label)] = float(value)
        total = sum(cleaned.values())
        if total <= 0:
            raise JevError(f"{name}: probabilities sum to zero")
        # Values arrive rounded; renormalize instead of rejecting small drift.
        cleaned = {label: value / total for label, value in cleaned.items()}
        if options is not None and (set(cleaned) != options or selected not in options):
            raise JevError(f"{name}: answer does not match the asked options")
        if selected not in cleaned:
            raise JevError(f"{name}: choice is not one of the options")
        confidence = answer.get("confidence", cleaned[selected])
        if type(confidence) not in (int, float) or not math.isfinite(confidence):
            confidence = cleaned[selected]
        return ChoiceAnswer(choice=str(selected), confidence=min(max(float(confidence), 0.0), 1.0),
                            probabilities=cleaned)

    def noul(self, name: str) -> float:
        value = self._answer(name, "noul").get("noul")
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise JevError(f"{name}: invalid yes probability")
        return float(value)

    def score(self, name: str) -> float:
        value = self._answer(name, "score").get("score")
        if type(value) not in (int, float) or not math.isfinite(value):
            raise JevError(f"{name}: invalid score")
        return float(value)

    def _answer(self, name: str, kind: str) -> dict[str, Any]:
        answer = self.answers.get(name)
        if not isinstance(answer, dict):
            raise JevError(f"{name}: no answer")
        if answer.get("type", kind) != kind:
            raise JevError(f"{name}: expected a {kind} answer")
        return answer


# transport(url, headers, body, timeout) -> (status, headers, body)
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]


def urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


class JevClient:
    def __init__(self, api_key: str, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 timeout: float = 2.5, retries: int = 1, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise ValueError("TYPESAFE_API_KEY is required for Jev")
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.transport = transport or urllib_transport
        self.sleep = sleep

    def body(self, state: Any, questions: dict[str, Question]) -> dict[str, Any]:
        if not questions:
            raise ValueError("ask at least one question")
        return {"model": self.model, "state": state,
                "questions": {name: question.to_json() for name, question in questions.items()}}

    def ask_sync(self, state: Any, questions: dict[str, Question]) -> JevResult:
        payload = json.dumps(self.body(state, questions), ensure_ascii=False).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        started = time.perf_counter()
        last: JevError | None = None
        for attempt in range(self.retries + 1):
            try:
                status, response_headers, raw = self.transport(self.url, headers, payload, self.timeout)
            except (OSError, TimeoutError) as exc:      # connection reset, DNS, timeout
                last = JevError(f"network error: {type(exc).__name__}")
            else:
                if status == 200:
                    try:
                        data = json.loads(raw)
                    except ValueError as exc:
                        raise JevError("response is not JSON", status=status) from exc
                    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
                        raise JevError("response has no answers", status=status)
                    return JevResult(answers=data["answers"], model=str(data.get("model", "")),
                                     usage=dict(data.get("usage") or {}),
                                     latency_ms=round((time.perf_counter() - started) * 1000, 1))
                last = JevError(f"HTTP {status}: {_detail(raw)}", status=status)
                if status not in _RETRYABLE:
                    raise last
                delay = _retry_after(response_headers)
                if attempt < self.retries and delay is not None and delay <= self.timeout:
                    self.sleep(delay)
                    continue
            if attempt < self.retries:
                self.sleep(0.15)
        raise last or JevError("request failed")

    async def ask(self, state: Any, questions: dict[str, Question]) -> JevResult:
        return await asyncio.to_thread(self.ask_sync, state, questions)


def _retry_after(headers: dict[str, str]) -> float | None:
    lowered = {key.lower(): value for key, value in (headers or {}).items()}
    try:
        if "retry-after-ms" in lowered:
            return float(lowered["retry-after-ms"]) / 1000
        if "retry-after" in lowered:
            return float(lowered["retry-after"])
    except ValueError:
        return None
    return 0.2


def _detail(raw: bytes) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return raw[:160].decode(errors="replace")
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        return str(detail[0].get("msg", ""))[:160]
    return str(detail or data)[:160]
