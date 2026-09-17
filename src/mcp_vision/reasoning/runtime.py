"""Adapter: the smallest clean insertion point into the existing runtime.

The reasoning harness is runtime-agnostic. This adapter plugs it into the exact
primitives `ContextTask` already uses — the same backend snapshot/dispatch and the
same `TaskConstraints` safety gates — so the harness can drive real actions
without rewriting any execution or permission logic.

Mapping:
- research-style actions (search/list/inspect/compare/read) are read-only: they
  observe the current grounded snapshot and report it as new information.
- ``draft`` is a review marker with no external side effect.
- concrete UI writes (fill/click/select/set_checked/upload/scroll) dispatch to the
  backend via ``resolve_target`` under ``TaskConstraints``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp_vision.guidance import resolve_target
from mcp_vision.reasoning.schemas import ObservationResult
from mcp_vision.tasks import checkbox_value

_READ_ONLY = {"search", "list", "inspect", "compare", "read"}


@dataclass
class BackendExecutor:
    """Adapt a backend (browser/desktop, same interface ContextTask relies on)."""

    backend: Any
    constraints: Any = None  # a mcp_vision.task_policy.TaskConstraints instance
    mode: str = "act"
    resolve: Any = resolve_target

    # -- Executor protocol -------------------------------------------------

    async def observe(self) -> dict:
        snap = await self.backend.snapshot()
        return {
            "url": getattr(snap, "url", ""),
            "title": getattr(snap, "title", ""),
            "text": getattr(snap, "text", "")[:12000],
            "elements": list(getattr(snap, "elements", []) or []),
            "identity": getattr(snap, "identity", {}),
        }

    async def act(self, proposed: Any) -> ObservationResult:
        action = (proposed.action or "").lower()
        if action in _READ_ONLY:
            try:
                obs = await self.observe()
            except Exception as exc:  # pragma: no cover - defensive
                return ObservationResult(ok=False, executed=False, message=f"observe failed: {exc}")
            snippet = (obs.get("text") or "")[:600] or obs.get("url") or ""
            return ObservationResult(ok=True, executed=True, message=f"{action} (read-only)",
                                     new_information=[snippet] if snippet else [])
        if action == "draft":
            # prepare/review marker: no external effect, no write
            return ObservationResult(ok=True, executed=False, message="draft prepared for review")
        if action in {"send", "submit", "buy", "delete", "destroy"}:
            # Consequential actions are not performed by this adapter; the
            # harness consequence gate should have already paused these.
            return ObservationResult(ok=False, executed=False,
                                     message="consequential action not auto-executed")
        if action in {"fill", "click", "select", "set_checked", "upload", "scroll"}:
            return await self._dispatch_write(proposed)
        return ObservationResult(ok=False, executed=False,
                                 message=f"unsupported action: {action}")

    async def _dispatch_write(self, proposed: Any) -> ObservationResult:
        snap = await self.backend.snapshot()
        name = (proposed.params.get("name") or "").strip()
        role = (proposed.params.get("role") or "").strip() or ""
        action = (proposed.action or "").lower()
        if action == "scroll":
            self._check((action, {}))
            receipt = await self.backend.scroll(snap.snapshot_id, int(proposed.params.get("value", 300)))
            return _from_receipt(receipt)
        target = self.resolve(snap.elements, name, role)
        if target is None:
            return ObservationResult(ok=False, executed=False,
                                     message=f"no element named {name!r} on the page")
        index = target["index"]
        value = proposed.params.get("value", "")
        try:
            self._check((action, target))
        except PermissionError as exc:
            # e.g. final-submission click, or a no_send/no_delete guard.
            return ObservationResult(ok=False, executed=False, message=str(exc))
        if action == "fill":
            receipt = await self.backend.fill(snap.snapshot_id, index, value)
        elif action == "select":
            receipt = await self.backend.select(snap.snapshot_id, index, value)
        elif action == "set_checked":
            receipt = await self.backend.set_checked(snap.snapshot_id, index, checkbox_value(str(value)) == "true")
        elif action == "upload":
            receipt = await self.backend.upload(snap.snapshot_id, index, proposed.params.get("path", ""))
        elif action == "click":
            receipt = await self.backend.click(snap.snapshot_id, index)
        else:  # pragma: no cover
            return ObservationResult(ok=False, executed=False, message="unsupported write")
        return _from_receipt(receipt)

    def _check(self, step_action_target) -> None:
        if self.constraints is not None:
            action, target = step_action_target
            self.constraints.check(self.mode, action, target or {})

    async def close(self) -> None:
        closer = getattr(self.backend, "close", None)
        if callable(closer):
            await closer()


def _from_receipt(receipt: Any) -> ObservationResult:
    ok = getattr(receipt, "status", "") in {"verified", "ok"} and getattr(receipt, "executed", False) is not False
    return ObservationResult(
        ok=ok,
        message=getattr(receipt, "message", ""),
        executed=getattr(receipt, "executed", None),
        new_information=[getattr(receipt, "message", "")] if ok else [],)