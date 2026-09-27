"""Bounded operation/target policy with optional Jev assistance."""
from __future__ import annotations

import concurrent.futures
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Literal

from providers import StructuredOutputError, validate_structured_output

OPERATIONS = ("click", "fill", "select", "check", "scroll")
DECISION_SCHEMA = {
    "type": "object", "required": ["target", "confidence"], "additionalProperties": False,
    "properties": {
        "operation": {"type": "string", "enum": list(OPERATIONS)},
        "target": {"type": "string"}, "confidence": {"type": "number"},
        "probabilities": {"type": "array", "items": {"type": "number"}},
        "continuation_id": {"type": "string"},
    },
}


@dataclass(frozen=True)
class Candidate:
    id: str
    role: str
    name: str
    operations: tuple[str, ...] = OPERATIONS


@dataclass
class Decision:
    operation: str
    target: str
    confidence: float
    source: str
    entropy: float = 0.0
    margin: float = 1.0
    escalated: bool = False
    invalid: bool = False
    continuation_id: str | None = None
    errors: list[str] = field(default_factory=list)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (value or "").lower()))


def infer_operation(request: str) -> str:
    tokens = _tokens(request)
    if tokens & {"type", "enter", "write", "fill"}:
        return "fill"
    if tokens & {"choose", "select", "pick"}:
        return "select"
    if tokens & {"check", "toggle", "enable", "disable"}:
        return "check"
    if tokens & {"scroll", "down", "up"}:
        return "scroll"
    return "click"


def _distribution(scores: list[float]) -> tuple[float, float, float]:
    if not scores:
        return 0.0, 0.0, 0.0
    positive = [max(0.0, float(score)) for score in scores]
    total = sum(positive)
    probabilities = [score / total for score in positive] if total else [1 / len(scores)] * len(scores)
    entropy = -sum(p * math.log(p, 2) for p in probabilities if p)
    ordered = sorted(probabilities, reverse=True)
    margin = ordered[0] - (ordered[1] if len(ordered) > 1 else 0)
    return max(probabilities), entropy, margin


def rules_decide(request: str, candidates: list[Candidate], *, threshold=.45) -> Decision:
    operation = infer_operation(request)
    request_tokens = _tokens(request)
    scores = []
    for candidate in candidates:
        overlap = len(request_tokens & _tokens(candidate.name))
        role_bonus = 1 if candidate.role in request_tokens else 0
        operation_bonus = 1 if operation in candidate.operations else -2
        scores.append(max(0, overlap * 2 + role_bonus + operation_bonus))
    confidence, entropy, margin = _distribution(scores)
    best = max(range(len(scores)), key=lambda i: (scores[i], -i)) if scores else -1
    target = candidates[best].id if best >= 0 else ""
    invalid = not target or scores[best] <= 0
    return Decision(operation, target, confidence, "rules", entropy, margin,
                    escalated=invalid or confidence < threshold, invalid=invalid)


class BoundedPolicy:
    """Rules by default; Jev is opt-in and always has a validated fallback."""
    def __init__(self, jev: Callable | None = None, *, timeout_s=.25, threshold=.55):
        self.jev, self.timeout_s, self.threshold = jev, timeout_s, threshold

    def decide(self, request: str, candidates: list[Candidate], *,
               mode: Literal["rules", "jev_target", "jev_operation_target"] = "rules",
               continuation_id: str | None = None) -> Decision:
        baseline = rules_decide(request, candidates, threshold=self.threshold)
        if mode == "rules" or self.jev is None:
            if mode != "rules":
                baseline.source = f"{mode}:configured_unverified->rules"
                baseline.escalated = True
            return baseline
        payload = {"request": request, "candidates": [candidate.__dict__ for candidate in candidates],
                   "mode": mode, "continuation_id": continuation_id}
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(self.jev, payload)
            raw = future.result(timeout=self.timeout_s)
            parsed = validate_structured_output(raw, DECISION_SCHEMA)
            target = parsed["target"]
            if target not in {candidate.id for candidate in candidates}:
                raise StructuredOutputError("target is outside the candidate set")
            operation = parsed.get("operation", baseline.operation) if mode == "jev_operation_target" else baseline.operation
            candidate = next(item for item in candidates if item.id == target)
            if operation not in candidate.operations:
                raise StructuredOutputError("operation is unsupported by the selected target")
            scores = parsed.get("probabilities") or [parsed["confidence"], 1 - parsed["confidence"]]
            confidence, entropy, margin = _distribution(scores)
            confidence = min(confidence, float(parsed["confidence"]))
            return Decision(operation, target, confidence, mode, entropy, margin,
                            escalated=confidence < self.threshold or margin < .1,
                            continuation_id=parsed.get("continuation_id"))
        except (concurrent.futures.TimeoutError, StructuredOutputError, ValueError, TypeError) as exc:
            baseline.source = f"{mode}->rules"
            baseline.escalated = True
            baseline.invalid = isinstance(exc, (StructuredOutputError, ValueError, TypeError))
            baseline.errors.append(type(exc).__name__)
            return baseline
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
