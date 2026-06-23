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


def _extract_position_bounds(ax_element: Any) -> tuple[int, int, int, int] | None:
    if not HAS_PYOBJC_AX or ax_element is None:
        return None

    try:
        position_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXPositionAttribute)
        size_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXSizeAttribute)
        if position_ref is None or size_ref is None:
            return None

        if hasattr(position_ref, "x"):
            pos_x, pos_y = float(position_ref.x), float(position_ref.y)
        else:
            pos_x = float(position_ref[0]) if len(position_ref) > 0 else 0
            pos_y = float(position_ref[1]) if len(position_ref) > 1 else 0

        if hasattr(size_ref, "width"):
            width, height = float(size_ref.width), float(size_ref.height)
        else:
            width = float(size_ref[0]) if len(size_ref) > 0 else 0
            height = float(size_ref[1]) if len(size_ref) > 1 else 0

        # Convert global physical coordinates to logical coordinates for the captured monitor
        monitor_index = cfg.screenshot_monitor
        displays = get_display_info()
        if monitor_index < len(displays):
            display = displays[monitor_index]
            # Check if the element is within this monitor's bounds (optional, but we can clip)
            # For simplicity, we assume the element is within the captured monitor.
            local_x = pos_x - display["origin_x"]
            local_y = pos_y - display["origin_y"]
            # Convert to logical coordinates by dividing by the display's scale factor
            logical_x = int(local_x / display["scale_factor"])
            logical_y = int(local_y / display["scale_factor"])
            logical_width = int(width / display["scale_factor"])
            logical_height = int(height / display["scale_factor"])
            return (logical_x, logical_y, logical_width, logical_height)
        else:
            # Fallback to primary monitor if index out of range
            display = displays[0]
            local_x = pos_x - display["origin_x"]
            local_y = pos_y - display["origin_y"]
            logical_x = int(local_x / display["scale_factor"])
            logical_y = int(local_y / display["scale_factor"])
            logical_width = int(width / display["scale_factor"])
            logical_height = int(height / display["scale_factor"])
            return (logical_x, logical_y, logical_width, logical_height)
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
    value = _copy_ax_attribute(ax_element, ApplicationServices.kAXTitleAttribute)
    return str(value) if value is not None else ""


def _extract_element_role(ax_element: Any) -> str:
    value = _copy_ax_attribute(ax_element, ApplicationServices.kAXRoleAttribute)
    return str(value) if value is not None else ""


def _is_element_interactable(ax_element: Any) -> bool:
    if not HAS_PYOBJC_AX or ax_element is None:
        return False

    try:
        enabled_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXEnabledAttribute)
        if enabled_ref is False:
            return False

        role = _extract_element_role(ax_element).lower()
        interactable_roles = {
            "button", "textfield", "textarea", "checkbox", "radiobutton",
            "combobox", "list", "table", "outline", "scrollbar", "slider",
            "popupbutton", "menubutton", "tabgroup", "searchfield", "passwordfield",
        }

        subrole_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXSubroleAttribute)
        if subrole_ref:
            subrole = str(subrole_ref).lower()
            if subrole in {"textfield", "securetextfield", "searchfield"}:
                return True

        actions_ref = _copy_ax_attribute(ax_element, ApplicationServices.kAXActionsAttribute)
        if actions_ref:
            action_names = [str(action).lower() for action in list(actions_ref)]
            if any(name in {"press", "click", "toggle", "pick", "scroll"} for name in action_names):
                return True

        return role in interactable_roles or "button" in role or "field" in role
    except Exception:
        return False


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
        _extract_children_recursive(root, elements, max_depth=5 if window_ref else 3)

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
                })

        elements.sort(key=lambda e: (e["y"], e["x"]))
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

            if bounds and (title or role):
                x, y, width, height = bounds
                if width > 0 and height > 0:
                    elements.append({
                        "id": len(elements) + 1,
                        "label": title or f"{role} element",
                        "x": x + width // 2,
                        "y": y + height // 2,
                        "box": [x, y, x + width, y + height],
                        "interactivity": _is_element_interactable(child),
                        "role": role,
                        "title": title,
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
