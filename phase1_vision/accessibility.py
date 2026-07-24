"""
Accessibility tree extraction via PyObjC AXUIElement.
Reads the frontmost app's UI hierarchy and returns elements in the same
format as OmniParser so downstream tools don't need to change.
"""

import time
from functools import lru_cache
from typing import Any

try:
    import ApplicationServices
    import Cocoa
    HAS_PYOBJC_AX = True
except ImportError:
    HAS_PYOBJC_AX = False

from phase1_vision.app_detector import get_frontmost_app, get_display_info
from config import cfg
from loguru import logger

AXErrorSuccess = 0

# Minimum confidence threshold - elements below this are filtered out
MIN_CONFIDENCE = cfg.ax_confidence_threshold

# Role confidence weights - standard Cocoa roles we trust highly
ROLE_CONFIDENCE = {
    "button": 0.95,
    "textfield": 0.95,
    "textarea": 0.95,
    "checkbox": 0.95,
    "radiobutton": 0.95,
    "combobox": 0.9,
    "list": 0.9,
    "table": 0.9,
    "outline": 0.85,
    "scrollbar": 0.9,
    "slider": 0.9,
    "popupbutton": 0.9,
    "menubutton": 0.9,
    "tabgroup": 0.85,
    "searchfield": 0.95,
    "passwordfield": 0.95,
    "statictext": 0.7,  # labels, not interactive
    "group": 0.6,      # container, low signal
    "window": 0.5,     # top-level container
    "application": 0.4,# root element
}

# Subrole boosts
SUBROLE_BOOST = {
    "textfield": 0.05,
    "securetextfield": 0.05,
    "searchfield": 0.05,
    "closebutton": 0.1,
    "minimizebutton": 0.1,
    "zoombutton": 0.1,
}

def _check_ax_permission() -> bool:
    if not HAS_PYOBJC_AX:
        return False
    try:
        trusted = ApplicationServices.AXIsProcessTrusted()
        if trusted:
            return True
        system_element = ApplicationServices.AXUIElementCreateSystemWide()
        return system_element is not None
    except Exception:
        return False


def _get_ax_element_for_app() -> Any:
    if not HAS_PYOBJC_AX:
        raise RuntimeError("PyObjC with Accessibility framework not available")

    if not _check_ax_permission():
        raise PermissionError(
            "Accessibility permissions are not granted. "
            "Grant access in System Settings → Privacy & Security → Accessibility"
        )

    frontmost_app = Cocoa.NSWorkspace.sharedWorkspace().frontmostApplication()
    if frontmost_app is None:
        return None

    pid = frontmost_app.processIdentifier()
    if pid == 0:
        return None

    return ApplicationServices.AXUIElementCreateApplication(pid)


def _copy_ax_attribute(ax_element: Any, attribute: str) -> Any:
    result = ApplicationServices.AXUIElementCopyAttributeValue(ax_element, attribute, None)
    if result[0] == AXErrorSuccess:
        return result[1]
    return None


def _ax_value_to_point(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    if hasattr(value, "x") and hasattr(value, "y"):
        return float(value.x), float(value.y)
    try:
        ok, point = ApplicationServices.AXValueGetValue(
            value, ApplicationServices.kAXValueCGPointType, None
        )
        if ok and point is not None:
            return float(point.x), float(point.y)
    except Exception:
        pass
    return None


def _ax_value_to_size(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    if hasattr(value, "width") and hasattr(value, "height"):
        return float(value.width), float(value.height)
    try:
        ok, size = ApplicationServices.AXValueGetValue(
            value, ApplicationServices.kAXValueCGSizeType, None
        )
        if ok and size is not None:
            return float(size.width), float(size.height)
    except Exception:
        pass
    return None


def _extract_position_bounds(ax_element: Any) -> tuple[int, int, int, int] | None:
    if not HAS_PYOBJC_AX or ax_element is None:
        return None

    try:
        position_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXPositionAttribute)
        size_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXSizeAttribute)
        point = _ax_value_to_point(position_ref)
        size = _ax_value_to_size(size_ref)
        if point is None or size is None:
            return None

        pos_x, pos_y = point
        width, height = size

        monitor_index = cfg.screenshot_monitor
        displays = get_display_info()
        display = displays[monitor_index] if monitor_index < len(displays) else displays[0]
        local_x = pos_x - display["origin_x"]
        local_y = pos_y - display["origin_y"]
        scale = display["scale_factor"] or 1.0
        return (
            int(local_x / scale),
            int(local_y / scale),
            int(width / scale),
            int(height / scale),
        )
    except Exception as e:
        logger.debug(f"Failed to read AX bounds: {e}")
        return None


def _global_to_screenshot_coords(global_x: int, global_y: int) -> tuple[int, int]:
    try:
        for display in get_display_info():
            if (
                display["origin_x"] <= global_x < display["origin_x"] + display["width"]
                and display["origin_y"] <= global_y < display["origin_y"] + display["height"]
            ):
                local_x = global_x - display["origin_x"]
                local_y = global_y - display["origin_y"]
                if cfg.display_scale_factor != 1.0:
                    return (
                        int(local_x / display["scale_factor"]),
                        int(local_y / display["scale_factor"]),
                    )
                return local_x, local_y
        return global_x, global_y
    except Exception:
        return global_x, global_y


def _extract_element_title(ax_element: Any) -> str:
    for attr in (
        ApplicationServices.kAXTitleAttribute,
        getattr(ApplicationServices, "kAXDescriptionAttribute", "AXDescription"),
        getattr(ApplicationServices, "kAXValueAttribute", "AXValue"),
    ):
        value = _copy_ax_attribute(ax_element, attr)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _extract_element_role(ax_element: Any) -> str:
    value = _copy_ax_attribute(ax_element, ApplicationServices.kAXRoleAttribute)
    if value is None:
        return ""
    role = str(value)
    if role.startswith("AX"):
        role = role[2:]
    return role


def _normalize_role(role: str) -> str:
    role = (role or "").lower().strip()
    if role.startswith("ax"):
        role = role[2:]
    aliases = {
        "textareatext": "textarea",
        "text": "statictext",
        "menuitem": "menuitem",
        "pop up button": "popupbutton",
    }
    return aliases.get(role, role)


def _compute_element_confidence(ax_element: Any) -> tuple[bool, float]:
    """
    Determine if element is interactable and compute confidence score.

    Returns:
        (is_interactable, confidence_score) where confidence is 0.0-1.0
    """
    if not HAS_PYOBJC_AX or ax_element is None:
        return False, 0.0

    try:
        enabled_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXEnabledAttribute)
        if enabled_ref is False:
            return False, 0.0

        role = _normalize_role(_extract_element_role(ax_element))
        confidence = ROLE_CONFIDENCE.get(role, 0.4)

        subrole_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXSubroleAttribute)
        if subrole_ref:
            subrole = _normalize_role(str(subrole_ref))
            confidence += SUBROLE_BOOST.get(subrole, 0.0)
            if subrole in {"textfield", "securetextfield", "searchfield", "closebutton", "minimizebutton", "zoombutton"}:
                confidence = max(confidence, 0.9)
                return True, min(confidence, 1.0)

        actions_attr = getattr(ApplicationServices, "kAXActionsAttribute", "AXActions")
        try:
            actions_ref = _copy_ax_attribute(ax_element, actions_attr)
        except Exception:
            actions_ref = None
        if actions_ref:
            action_names = [str(action).lower() for action in list(actions_ref)]
            if any(name in {"press", "click", "toggle", "pick", "scroll", "axpress"} for name in action_names):
                confidence = max(confidence, 0.95)
                return True, min(confidence, 1.0)

        interactable_roles = {
            "button", "textfield", "textarea", "checkbox", "radiobutton",
            "combobox", "list", "table", "outline", "scrollbar", "slider",
            "popupbutton", "menubutton", "tabgroup", "searchfield", "passwordfield",
            "link", "menuitem", "incrementor",
        }
        is_interactable = role in interactable_roles or "button" in role or "field" in role
        if is_interactable:
            confidence = max(confidence, 0.8)
        return is_interactable, min(confidence, 1.0)
    except Exception:
        return False, 0.0


def _is_element_interactable(ax_element: Any) -> bool:
    """Backward-compatible wrapper."""
    return _compute_element_confidence(ax_element)[0]


@lru_cache(maxsize=32)
def _get_ax_elements_cached(window_id: str, timestamp: int) -> list[dict[str, Any]]:
    return _extract_ax_elements_internal()


def _extract_ax_elements_internal() -> list[dict[str, Any]]:
    if not HAS_PYOBJC_AX:
        return []

    try:
        app_info = get_frontmost_app()
        if not app_info["bundle_id"] or not _check_ax_permission():
            return []

        app_element = _get_ax_element_for_app()
        if app_element is None:
            return []

        elements: list[dict[str, Any]] = []
        window_ref = _copy_ax_attribute(app_element, ApplicationServices.kAXMainWindowAttribute)
        root = window_ref if window_ref is not None else app_element
        _extract_children_recursive(root, elements, max_depth=8 if window_ref else 4)

        if not elements:
            title = _extract_element_title(app_element)
            bounds = _extract_position_bounds(app_element)
            if title and bounds:
                x, y, width, height = bounds
                elements.append({
                    "id": 1,
                    "label": title,
                    "x": x + width // 2,
                    "y": y + height // 2,
                    "box": [x, y, x + width, y + height],
                    "interactivity": False,
                    "role": _extract_element_role(app_element),
                    "title": title,
                    "confidence": 0.5,
                })

        # Sort: interactable first, then by confidence desc, then by position
        elements.sort(key=lambda e: (not e.get("interactivity", False), -e.get("confidence", 0.0), e["y"], e["x"]))
        for i, elem in enumerate(elements):
            elem["id"] = i + 1

        logger.debug(f"Extracted {len(elements)} elements via accessibility tree")
        return elements[: cfg.max_elements]
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"AX extraction failed: {e}")
        return []


def _extract_children_recursive(
    parent_element: Any,
    elements: list[dict[str, Any]],
    max_depth: int,
    current_depth: int = 0,
) -> None:
    if not HAS_PYOBJC_AX or parent_element is None or current_depth >= max_depth:
        return

    try:
        children_ref = _copy_ax_attribute(parent_element, ApplicationServices.kAXChildrenAttribute)
        if not children_ref:
            return

        for child in list(children_ref):
            if child is None:
                continue

            title = _extract_element_title(child)
            role = _extract_element_role(child)
            bounds = _extract_position_bounds(child)
            is_interactable, confidence = _compute_element_confidence(child)

            if bounds and (title or role):
                x, y, width, height = bounds
                if width > 0 and height > 0 and confidence >= MIN_CONFIDENCE:
                    elements.append({
                        "id": len(elements) + 1,
                        "label": title or f"{role} element",
                        "x": x + width // 2,
                        "y": y + height // 2,
                        "box": [x, y, x + width, y + height],
                        "interactivity": is_interactable,
                        "role": role,
                        "title": title,
                        "confidence": confidence,
                    })

            _extract_children_recursive(child, elements, max_depth, current_depth + 1)
    except Exception as e:
        logger.debug(f"Failed to walk AX children: {e}")


def get_accessibility_elements(use_cache: bool = True) -> list[dict[str, Any]]:
    if not HAS_PYOBJC_AX:
        return []

    if use_cache:
        timestamp = int(time.time() // 0.1)
        app_info = get_frontmost_app()
        window_id = app_info["bundle_id"] or "unknown"
        return _get_ax_elements_cached(window_id, timestamp)

    return _extract_ax_elements_internal()
