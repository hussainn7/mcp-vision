"""Snap a model-estimated point onto the real UI element it names.

Vision models land near a control, not always on it. When accessibility data
is available we look at elements around the estimate and, if one clearly
matches the label, aim at its center instead. A chooser decides the match:
Jev when configured (typed choice with probabilities), otherwise token and
distance heuristics. No match means we keep the model's point unchanged.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from mcp_vision.buddy.companion import Target
from mcp_vision.buddy.geometry import Rect


@dataclass(frozen=True)
class Element:
    id: str
    role: str
    name: str
    bounds: Rect          # global top-left points


Locator = Callable[[float, float, float], list[Element]]


class Chooser(Protocol):
    async def choose(self, label: str, target: Target, elements: list[Element]) -> Element | None: ...


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words} - {
        "the", "a", "an", "button", "icon", "menu", "item", "field", "link", "tab", "option"}


def heuristic_score(label: str, target: Target, element: Element, radius: float) -> float:
    """0..1: how likely ``element`` is what the model meant by ``label``."""
    wanted, have = _terms(label), _terms(element.name)
    overlap = len(wanted & have) / len(wanted) if wanted else 0.0
    cx, cy = element.bounds.center
    distance = math.hypot(cx - target.x, cy - target.y)
    nearness = max(0.0, 1.0 - distance / radius)
    inside = 1.0 if element.bounds.contains(target.x, target.y) else 0.0
    return 0.6 * overlap + 0.25 * nearness + 0.15 * inside


class HeuristicChooser:
    def __init__(self, radius: float = 90.0, threshold: float = 0.55):
        self.radius = radius
        self.threshold = threshold

    async def choose(self, label: str, target: Target, elements: list[Element]) -> Element | None:
        scored = sorted(((heuristic_score(label, target, e, self.radius), e) for e in elements),
                        key=lambda pair: pair[0], reverse=True)
        if not scored or scored[0][0] < self.threshold:
            return None
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.05:
            return None                       # ambiguous; trust the model's point
        return scored[0][1]


class JevChooser:
    """Typed choice over nearby accessibility elements, with an explicit 'none'."""

    NONE = "none"

    def __init__(self, client, *, threshold: float = 0.55, fallback: Chooser | None = None):
        self.client = client
        self.threshold = threshold
        self.fallback = fallback or HeuristicChooser()

    async def choose(self, label: str, target: Target, elements: list[Element]) -> Element | None:
        from mcp_vision.buddy.jev import Choice

        by_id = {f"e{index}": element for index, element in enumerate(elements)}
        criteria = {key: f"{element.role or 'element'} named {element.name!r}"
                    for key, element in by_id.items()}
        criteria[self.NONE] = "None of these elements is the one described."
        state = {"pointing_at": label,
                 "nearby_elements": [{"id": key, "role": e.role, "name": e.name,
                                      "distance_px": round(math.hypot(e.bounds.center[0] - target.x,
                                                                      e.bounds.center[1] - target.y))}
                                     for key, e in by_id.items()]}
        try:
            result = await self.client.ask(state, {"element": Choice(
                criteria=criteria,
                instructions=f"An assistant wants to point at the {label!r}. Which nearby on-screen "
                             f"element is it? Prefer the element whose name matches; closer is better "
                             f"when names tie. Choose none if no element matches.")})
            answer = result.choice("element", set(criteria))
        except Exception:
            return await self.fallback.choose(label, target, elements)
        if answer.choice == self.NONE or answer.p(answer.choice) < self.threshold:
            return None
        return by_id[answer.choice]


class ElementSnapper:
    def __init__(self, locator: Locator, chooser: Chooser | None = None, *, radius: float = 90.0,
                 max_candidates: int = 12, max_element_area: float = 400 * 200):
        self.locator = locator
        self.chooser = chooser or HeuristicChooser(radius=radius)
        self.radius = radius
        self.max_candidates = max_candidates
        self.max_element_area = max_element_area

    async def snap(self, target: Target) -> Target:
        import asyncio

        elements = await asyncio.to_thread(self.locator, target.x, target.y, self.radius)
        elements = [e for e in elements if e.bounds.width * e.bounds.height <= self.max_element_area
                    and e.bounds.width > 0 and e.bounds.height > 0]
        elements.sort(key=lambda e: math.hypot(e.bounds.center[0] - target.x, e.bounds.center[1] - target.y))
        elements = elements[:self.max_candidates]
        if not elements or not target.label:
            return target
        chosen = await self.chooser.choose(target.label, target, elements)
        if chosen is None:
            return target
        x, y = chosen.bounds.center
        return replace(target, x=x, y=y, element=chosen.bounds, source="snapped")
