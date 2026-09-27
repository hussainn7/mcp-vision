"""Validation and execution boundary for domain-neutral operation plans."""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
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


class BrowserOperationExecutor:
    """Map validated operations onto the grounded browser runtime surface."""

    def __init__(self, runtime, *, poll_interval: float = .05, max_polls: int = 20):
        self.runtime, self.poll_interval, self.max_polls = runtime, poll_interval, max_polls

    async def execute(self, step: Step) -> Any:
        step = validate_step(step)
        args = step.arguments
        if step.operation is Operation.OPEN_URL:
            return await self.runtime.navigate(args["url"])
        if step.operation is Operation.ENTER_TEXT:
            return await self.runtime.fill(args["snapshot_id"], args["target"], args["text"])
        if step.operation is Operation.SELECT:
            return await self.runtime.select(args["snapshot_id"], args["target"], args["value"])
        if step.operation is Operation.PRESS:
            page = getattr(self.runtime, "page", None)
            if page is None:
                raise RuntimeError("keyboard input is unavailable")
            return await page.keyboard.press(args["key"])
        if step.operation is Operation.VERIFY:
            return await self.runtime.verify_text(args["snapshot_id"], args["condition"])
        if step.operation is Operation.WAIT_FOR:
            expected = str(args["condition"]).casefold()
            for _ in range(self.max_polls):
                snapshot = await self.runtime.snapshot()
                if expected in snapshot.text.casefold() or any(
                    expected in str(item.get("name", "")).casefold() for item in snapshot.elements
                ):
                    return snapshot
                await asyncio.sleep(self.poll_interval)
            raise TimeoutError(f"condition not observed: {args['condition']}")
        raise RuntimeError(f"{step.operation.value} belongs to the task lifecycle, not the browser executor")
