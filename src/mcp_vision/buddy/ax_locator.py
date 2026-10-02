"""Find accessibility elements around a screen point (macOS).

Instead of walking whole window trees, we hit-test a small ring of points
around the model's estimate and climb from each hit to the nearest real
control (a static text label inside a button resolves to the button). That
stays fast (a few milliseconds per probe) on Electron and browser windows
with thousands of nodes.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any

from mcp_vision.buddy.geometry import Rect
from mcp_vision.buddy.snap import Element

CONTROL_ROLES = {
    "AXButton", "AXMenuButton", "AXPopUpButton", "AXMenuItem", "AXMenuBarItem", "AXCheckBox",
    "AXRadioButton", "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXLink", "AXTab",
    "AXRadioGroup", "AXSlider", "AXIncrementor", "AXDisclosureTriangle", "AXCell", "AXRow",
    "AXImage", "AXStaticText", "AXToolbarButton", "AXDockItem",
}
_ROLE_NAMES = {"AXButton": "button", "AXMenuItem": "menu item", "AXMenuBarItem": "menu",
               "AXCheckBox": "checkbox", "AXRadioButton": "radio button", "AXTextField": "text field",
               "AXTextArea": "text area", "AXSearchField": "search field", "AXPopUpButton": "pop-up menu",
               "AXLink": "link", "AXTab": "tab", "AXStaticText": "text", "AXImage": "image",
               "AXDockItem": "dock item"}


def _copy(api: Any, element: Any, attribute: str) -> Any:
    try:
        result = api.AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    if isinstance(result, tuple):
        return result[-1] if result and result[0] == 0 else None
    return result


def _bounds(api: Any, element: Any) -> Rect | None:
    try:
        ok_p, position = api.AXValueGetValue(_copy(api, element, "AXPosition"), api.kAXValueCGPointType, None)
        ok_s, size = api.AXValueGetValue(_copy(api, element, "AXSize"), api.kAXValueCGSizeType, None)
    except Exception:
        return None
    if not (ok_p and ok_s):
        return None
    return Rect(float(position.x), float(position.y), float(size.width), float(size.height))


def _name(api: Any, element: Any, role: str) -> str:
    for attribute in ("AXTitle", "AXDescription", "AXLabel", "AXHelp"):
        value = _copy(api, element, attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if role in {"AXStaticText", "AXTextField", "AXSearchField", "AXCell"}:
        value = _copy(api, element, "AXValue")
        if isinstance(value, str) and value.strip() and role != "AXTextField":
            return value.strip()[:80]
        placeholder = _copy(api, element, "AXPlaceholderValue")
        if isinstance(placeholder, str):
            return placeholder.strip()
    return ""


def _control_for(api: Any, element: Any, max_climb: int = 4) -> Any:
    """Climb from a hit (often a label or image) to the control that owns it."""
    current = element
    for _ in range(max_climb):
        role = str(_copy(api, current, "AXRole") or "")
        if role in CONTROL_ROLES - {"AXStaticText", "AXImage"}:
            return current
        parent = _copy(api, current, "AXParent")
        if parent is None:
            break
        parent_role = str(_copy(api, parent, "AXRole") or "")
        if parent_role in {"AXWindow", "AXApplication", "AXScrollArea", "AXWebArea", "AXGroup"}:
            break
        current = parent
    return element


def probe_points(x: float, y: float, radius: float) -> list[tuple[float, float]]:
    points = [(x, y)]
    for ring, count in ((radius * 0.35, 6), (radius * 0.75, 10)):
        for index in range(count):
            angle = 2 * math.pi * index / count
            points.append((x + ring * math.cos(angle), y + ring * math.sin(angle)))
    return points


def elements_near(x: float, y: float, radius: float, *, time_budget: float = 0.12) -> list[Element]:
    """Elements whose controls are hit around (x, y) in global top-left points."""
    try:
        import ApplicationServices as AX
    except ImportError:
        return []
    if not AX.AXIsProcessTrusted():
        return []
    system = AX.AXUIElementCreateSystemWide()
    try:
        # A hung app would otherwise block each hit-test for the ~6 s default.
        AX.AXUIElementSetMessagingTimeout(system, 0.05)
    except Exception:
        pass
    deadline = time.monotonic() + time_budget
    found: dict[tuple, Element] = {}
    own_pid = os.getpid()
    for px, py in probe_points(x, y, radius):
        if time.monotonic() > deadline:
            break
        try:
            err, hit = AX.AXUIElementCopyElementAtPosition(system, px, py, None)
        except Exception:
            continue
        if err != 0 or hit is None:
            continue
        try:
            err, pid = AX.AXUIElementGetPid(hit, None)
            if err == 0 and int(pid) == own_pid:
                continue                      # never snap onto the buddy's own windows
        except Exception:
            pass
        control = _control_for(AX, hit)
        role = str(_copy(AX, control, "AXRole") or "")
        bounds = _bounds(AX, control)
        if bounds is None or bounds.width <= 0 or bounds.height <= 0:
            continue
        name = _name(AX, control, role)
        if not name:
            continue
        key = (round(bounds.x), round(bounds.y), round(bounds.width), round(bounds.height), name)
        if key not in found:
            found[key] = Element(id=str(len(found)), role=_ROLE_NAMES.get(role, role.removeprefix("AX").lower()),
                                 name=name, bounds=bounds)
    return list(found.values())
