"""Deterministic first-pass routing for a future right-click > Agent entry point.

The runtime chooses the richest reliable interface first and records a fallback.
The model may use the recommendation, but it cannot promote low confidence into
an unsafe action capability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


Mode = Literal["browser", "vision", "hybrid"]


@dataclass(frozen=True)
class SurfaceContext:
    application: str = ""
    url: str = ""
    dom_available: bool = False
    accessibility_available: bool = False
    selection_text: str = ""
    target_role: str = ""
    target_name: str = ""


@dataclass(frozen=True)
class RouteDecision:
    primary: Mode
    fallback: tuple[Mode, ...]
    confidence: float
    reason: str
    observe_only: bool = True


_VISUAL_ROLES = {"canvas", "video", "image", "map", "remote-desktop"}


def route_surface(context: SurfaceContext) -> RouteDecision:
    """Choose DOM/CDP vs vision from observed surface capabilities.

    Context-menu prompts begin observe-only. A later policy decision must grant
    write capabilities; routing never grants them implicitly.
    """
    is_web = urlsplit(context.url).scheme in {"http", "https"}
    role = context.target_role.lower().strip()
    if is_web and context.dom_available and role not in _VISUAL_ROLES:
        return RouteDecision("browser", ("hybrid", "vision"), 0.92,
                             "Web DOM and semantic controls are available.")
    if is_web and context.dom_available:
        return RouteDecision("hybrid", ("vision", "browser"), 0.78,
                             "The page is scriptable, but the selected target is primarily visual.")
    if context.accessibility_available and not is_web:
        return RouteDecision("hybrid", ("vision",), 0.74,
                             "Native accessibility can ground controls; vision verifies layout and state.")
    return RouteDecision("vision", ("hybrid",), 0.66,
                         "No reliable DOM surface is available; use pixels with fresh screenshots.")
