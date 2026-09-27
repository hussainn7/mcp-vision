"""Validation and execution boundary for domain-neutral operation plans."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from reasoning.intent import Operation


@dataclass(frozen=True)
class Step:
    operation: Operation
    arguments: dict[str, Any]
    expected: str = ""


class OperationExecutor(Protocol):
    async def execute(self, step: Step) -> Any: ...


_REQUIRED = {
    Operation.OPEN_URL: {"url"}, Operation.ENTER_TEXT: {"target", "text"},
    Operation.SELECT: {"target", "value"}, Operation.PRESS: {"key"},
    Operation.WAIT_FOR: {"condition"}, Operation.VERIFY: {"condition"},
    Operation.ASK_USER: {"question"}, Operation.REPLAN: {"reason"},
}


def validate_step(step: Step) -> Step:
    missing = _REQUIRED[step.operation] - step.arguments.keys()
    if missing:
        raise ValueError(f"{step.operation.value} missing: {', '.join(sorted(missing))}")
    if step.operation in {Operation.ENTER_TEXT, Operation.SELECT} and not step.arguments.get("target"):
        raise ValueError("target must be a fresh semantic target")
    return step


def structured_result(*, fields: dict[str, Any], sources: list[str], complete: bool,
                      missing: list[str] | None = None) -> dict[str, Any]:
    return {"complete": bool(complete), "fields": fields, "sources": list(dict.fromkeys(sources)),
            "missing": missing or []}
