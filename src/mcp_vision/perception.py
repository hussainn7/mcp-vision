"""Semantic-first evidence fusion with measured visual-cache reuse.

Heavy visual grounding is deliberately a fallback: DOM or AX controls remain
authoritative when they already provide an actionable, high-confidence state.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mcp_vision.browser import BrowserSnapshot
from mcp_vision.browser import Receipt
from mcp_vision.core.governor import Governor, classify
from mcp_vision.core.models import BoundingBox, Policy, ScreenElement


_SEMANTIC_SOURCES = {"dom", "dom-accessibility", "cdp", "accessibility", "macos-accessibility"}
_ACTIONABLE_ROLES = {
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "switch",
    "combobox", "listbox", "option", "menuitem", "tab", "slider", "spinbutton",
}


def _bounds(record: dict[str, Any]) -> tuple[float, float, float, float]:
    return tuple(float(record.get(key) or 0) for key in ("x", "y", "w", "h"))


def _iou(left: dict[str, Any], right: dict[str, Any]) -> float:
    lx, ly, lw, lh = _bounds(left)
    rx, ry, rw, rh = _bounds(right)
    intersection_w = max(0, min(lx + lw, rx + rw) - max(lx, rx))
    intersection_h = max(0, min(ly + lh, ry + rh) - max(ly, ry))
    intersection = intersection_w * intersection_h
    union = lw * lh + rw * rh - intersection
    return intersection / union if union else 0


def _identity_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a, b = left.get("identity") or {}, right.get("identity") or {}
    return any(a.get(key) and a.get(key) == b.get(key) for key in {**a, **b})


def _semantic_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    role_match = not left.get("role") or not right.get("role") or left.get("role") == right.get("role")
    left_name = str(left.get("name") or "").casefold().strip()
    right_name = str(right.get("name") or "").casefold().strip()
    name_match = not left_name or not right_name or left_name == right_name
    return role_match and name_match and _iou(left, right) >= 0.62


def fuse_records(layers: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Deduplicate evidence while retaining every usable identity and source."""
    fused: list[dict[str, Any]] = []
    for source, records in layers:
        for raw in records:
            record = dict(raw)
            record["sources"] = list(dict.fromkeys([*(record.get("sources") or []), source]))
            record.setdefault("confidence", 1.0 if source in _SEMANTIC_SOURCES else 0.7)
            match = next((item for item in fused
                          if _identity_overlap(item, record) or _semantic_match(item, record)), None)
            if match is None:
                fused.append(record)
                continue
            match["identity"] = {**(match.get("identity") or {}), **{
                key: value for key, value in (record.get("identity") or {}).items() if value
            }}
            match["sources"] = list(dict.fromkeys([*match.get("sources", []), *record["sources"]]))
            match["confidence"] = min(1.0, max(float(match.get("confidence", 0)),
                                                float(record.get("confidence", 0)))
                                      + 0.03 * (len(match["sources"]) - 1))
            for key in ("name", "role", "value", "description"):
                if not match.get(key) and record.get(key):
                    match[key] = record[key]
    for index, record in enumerate(fused):
        record["index"] = index
    return fused


@dataclass(frozen=True)
class FallbackDecision:
    needed: bool
    reason: str
    semantic_controls: int


def visual_fallback_decision(records: list[dict[str, Any]]) -> FallbackDecision:
    actionable = [record for record in records if str(record.get("role") or "").lower() in _ACTIONABLE_ROLES
                  and float(record.get("confidence", 1)) >= 0.75]
    visual_surface = any(str(record.get("role") or "").lower() in {
        "canvas", "image", "map", "remote-desktop",
    } for record in records)
    if actionable and not visual_surface:
        return FallbackDecision(False, "Semantic controls are sufficient; skip visual grounding.", len(actionable))
    if visual_surface:
        return FallbackDecision(True, "A primarily visual surface needs grounded regions.", len(actionable))
    return FallbackDecision(True, "No high-confidence actionable semantic controls were observed.", 0)


@dataclass(frozen=True)
class TileCacheResult:
    total_tiles: int
    changed_tiles: tuple[tuple[int, int], ...]
    unchanged_ratio: float


class TileChangeCache:
    """Hash raw pixel tiles so unchanged regions can reuse OCR/grounding output."""

    def __init__(self, tile_size: int = 128):
        if tile_size < 16:
            raise ValueError("tile_size must be at least 16")
        self.tile_size = tile_size
        self._hashes: dict[tuple[int, int], str] = {}

    def update(self, pixels: bytes, *, width: int, height: int, channels: int = 4) -> TileCacheResult:
        if width <= 0 or height <= 0 or channels not in {1, 3, 4}:
            raise ValueError("invalid image dimensions or channels")
        if len(pixels) != width * height * channels:
            raise ValueError("pixel buffer size does not match dimensions")
        current: dict[tuple[int, int], str] = {}
        changed: list[tuple[int, int]] = []
        row_bytes = width * channels
        for tile_y, y in enumerate(range(0, height, self.tile_size)):
            for tile_x, x in enumerate(range(0, width, self.tile_size)):
                rows = []
                for offset in range(y, min(y + self.tile_size, height)):
                    start = offset * row_bytes + x * channels
                    rows.append(pixels[start:start + min(self.tile_size, width - x) * channels])
                digest = hashlib.blake2s(b"".join(rows), digest_size=12).hexdigest()
                key = (tile_x, tile_y)
                current[key] = digest
                if self._hashes.get(key) != digest:
                    changed.append(key)
        total = len(current)
        self._hashes = current
        return TileCacheResult(total, tuple(changed), 1 - len(changed) / total if total else 0)


VisualProvider = Callable[[bytes, tuple[tuple[int, int], ...] | None], Awaitable[list[dict[str, Any]]]]


class PerceptionPipeline:
    """Call an injected visual provider only when semantics are insufficient."""

    def __init__(self, provider: VisualProvider):
        self.provider = provider
        self._last_visual: list[dict[str, Any]] = []
        self.calls = 0

    async def enrich(self, snapshot: BrowserSnapshot, screenshot: bytes,
                     *, changed_tiles: tuple[tuple[int, int], ...] | None = None) -> BrowserSnapshot:
        decision = visual_fallback_decision(snapshot.elements)
        if not decision.needed:
            return snapshot
        if changed_tiles == () and self._last_visual:
            visual = self._last_visual
        else:
            visual = await self.provider(screenshot, changed_tiles)
            self.calls += 1
            self._last_visual = visual
        records = fuse_records([(snapshot.source, snapshot.elements), ("visual", visual)])
        return snapshot.model_copy(update={
            "elements": records, "source": "unified",
            "pruned": {**snapshot.pruned, "visual_provider_calls": self.calls},
        })


class VisualFallbackBackend:
    """Resolve visual-only semantic candidates to a fresh, guarded pointer fallback."""

    def __init__(self, backend, pipeline: PerceptionPipeline, *, allow_visual_writes: bool = False,
                 governor: Governor | None = None, actuator=None):
        self.backend = backend
        self.pipeline = pipeline
        self.allow_visual_writes = allow_visual_writes
        self.governor = governor or Governor()
        self.actuator = actuator
        self._snapshot: BrowserSnapshot | None = None
        self._base_count = 0
        self._screenshot_hash = ""

    async def snapshot(self) -> BrowserSnapshot:
        base = await self.backend.snapshot()
        self._base_count = len(base.elements)
        if not visual_fallback_decision(base.elements).needed:
            self._snapshot = base
            self._screenshot_hash = ""
            return base
        image = await self.backend.screenshot()
        self._screenshot_hash = hashlib.sha256(image).hexdigest()
        self._snapshot = await self.pipeline.enrich(base, image)
        return self._snapshot

    def _visual(self, snapshot_id: str, index: int) -> dict[str, Any] | None:
        if not self._snapshot or snapshot_id != self._snapshot.snapshot_id:
            return None
        if index < self._base_count or not 0 <= index < len(self._snapshot.elements):
            return None
        record = self._snapshot.elements[index]
        return record if (record.get("identity") or {}).get("visual") else None

    async def click(self, snapshot_id: str, index: int) -> Receipt:
        visual = self._visual(snapshot_id, index)
        if visual is None:
            if self._snapshot and snapshot_id == self._snapshot.snapshot_id and index < self._base_count:
                return await self.backend.click(snapshot_id, index)
            return Receipt(status="stale", action="click", message="Visual target is stale or missing.", executed=False)
        if not self.allow_visual_writes:
            return Receipt(status="blocked", action="click", message="Visual pointer fallback is disabled.", executed=False)
        element = ScreenElement(id=index, label=str(visual.get("name") or ""),
                                role=str(visual.get("role") or "unknown"),
                                bbox=BoundingBox(x=round(visual.get("x", 0)), y=round(visual.get("y", 0)),
                                                 w=round(visual.get("w", 0)), h=round(visual.get("h", 0))),
                                cx=round(visual.get("x", 0) + visual.get("w", 0) / 2),
                                cy=round(visual.get("y", 0) + visual.get("h", 0) / 2))
        policy = classify("click_element", element=element)
        if policy is Policy.RESTRICTED_ACTION or not self.governor.allow(policy, f"visual click {element.label}"):
            return Receipt(status="blocked", action="click", message="Visual target requires confirmation.",
                           executed=False)
        fresh = await self.backend.screenshot()
        if hashlib.sha256(fresh).hexdigest() != self._screenshot_hash:
            return Receipt(status="stale", action="click",
                           message="Pixels changed after visual grounding; reobserve before clicking.", executed=False)
        actuator = self.actuator
        if actuator is None:
            from mcp_vision.core.actuate import get_actuator
            actuator = get_actuator()
        actuator.click(element.cx, element.cy)
        return Receipt(status="unverified", action="click", executed=True,
                       message="Clicked a freshly grounded visual target; verify the successor state.",
                       evidence={"execution_path": "visual_pointer", "background": False,
                                 "visual_identity": visual["identity"]["visual"],
                                 "bounds": element.bbox.model_dump()})

    async def fill(self, snapshot_id: str, index: int, text: str) -> Receipt:
        if self._visual(snapshot_id, index):
            return Receipt(status="blocked", action="fill",
                           message="Visual-only text entry is not enabled; use a semantic control.", executed=False)
        return await self.backend.fill(snapshot_id, index, text)

    async def select(self, snapshot_id: str, index: int, value: str) -> Receipt:
        if self._visual(snapshot_id, index):
            return Receipt(status="blocked", action="select",
                           message="Visual-only selection is not enabled; use a semantic control.", executed=False)
        return await self.backend.select(snapshot_id, index, value)

    async def set_checked(self, snapshot_id: str, index: int, checked: bool) -> Receipt:
        if self._visual(snapshot_id, index):
            return Receipt(status="blocked", action="set_checked",
                           message="Visual-only toggles require semantic confirmation.", executed=False)
        return await self.backend.set_checked(snapshot_id, index, checked)

    async def scroll(self, snapshot_id: str, delta_y: int) -> Receipt:
        return await self.backend.scroll(snapshot_id, delta_y)

    async def screenshot(self) -> bytes:
        return await self.backend.screenshot()

    async def settle(self, operation: str = "") -> None:
        settle = getattr(self.backend, "settle", None)
        if settle:
            await settle(operation)
