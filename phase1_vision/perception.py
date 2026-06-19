"""
Perception router.

Picks the fastest backend for the focused app:
  browser  -> Playwright DOM scrape
  native   -> accessibility tree
  fallback -> OmniParser + vision
"""

import time
from typing import Any

from phase1_vision.app_detector import get_frontmost_app
from phase1_vision.accessibility import get_accessibility_elements, HAS_PYOBJC_AX
from phase2_mcp.playwright_tools import HAS_PLAYWRIGHT, cleanup_playwright
from config import cfg
from loguru import logger


class AccessibilityPermissionError(Exception):
    pass


def get_perception_data() -> dict[str, Any]:
    start_time = time.time()
    error_msg = None

    try:
        app_info = get_frontmost_app()

        if app_info["is_browser"] and HAS_PLAYWRIGHT:
            elements = _get_playwright_elements()
            if elements:
                return {
                    "method": "playwright",
                    "elements": elements,
                    "timestamp": start_time,
                    "app_info": app_info,
                    "coord_space": "screenshot",
                    "error": None,
                }
            error_msg = "Playwright perception failed"

        if HAS_PYOBJC_AX and app_info["needs_ax_permission"]:
            try:
                elements = get_accessibility_elements(use_cache=True)
                if elements:
                    return {
                        "method": "accessibility",
                        "elements": elements,
                        "timestamp": start_time,
                        "app_info": app_info,
                        "coord_space": "screenshot",
                        "error": None,
                    }
                error_msg = "Accessibility perception returned no elements"
            except PermissionError as e:
                raise AccessibilityPermissionError(str(e)) from e
            except Exception as e:
                logger.warning(f"Accessibility perception error: {e}")
                error_msg = f"Accessibility perception error: {e}"

        return {
            "method": "omniparser",
            "elements": [],
            "timestamp": start_time,
            "app_info": app_info,
            "coord_space": "screenshot",
            "error": error_msg,
        }

    except AccessibilityPermissionError:
        raise
    except Exception as e:
        logger.error(f"Perception pipeline error: {e}")
        return {
            "method": "error",
            "elements": [],
            "timestamp": time.time(),
            "app_info": {
                "name": None,
                "bundle_id": None,
                "is_browser": False,
                "needs_ax_permission": False,
            },
            "coord_space": "screenshot",
            "error": f"Perception pipeline error: {e}",
        }


def _get_playwright_elements() -> list[dict[str, Any]] | None:
    if not HAS_PLAYWRIGHT:
        return None

    try:
        from phase2_mcp.playwright_tools import _get_playwright_manager, _run_async

        async def _get_elements_async():
            manager = _get_playwright_manager()
            if not await manager.is_running() and not await manager.start():
                return None

            page = manager.page
            if page is None:
                return None

            elements = []
            element_id = 1
            selectors = [
                ("button", "button"),
                ("[role='button']", "button"),
                ("a", "link"),
                ("[role='link']", "link"),
                ("input", "textbox"),
                ("textarea", "textarea"),
                ("select", "combobox"),
                ("[role='tab']", "tab"),
            ]

            for selector, role_hint in selectors:
                locators = page.locator(selector)
                count = await locators.count()
                for i in range(min(count, 10)):
                    try:
                        element = locators.nth(i)
                        box = await element.bounding_box()
                        if box is None:
                            continue

                        label = (
                            await element.get_attribute("aria-label")
                            or await element.inner_text()
                            or await element.get_attribute("value")
                            or f"{role_hint} {element_id}"
                        )
                        if len(label.strip()) > 100:
                            label = label[:97] + "..."

                        screen_x, screen_y = _playwright_to_screenshot_coords(box["x"], box["y"])
                        elements.append({
                            "id": element_id,
                            "label": label.strip(),
                            "x": int(screen_x + box["width"] / 2),
                            "y": int(screen_y + box["height"] / 2),
                            "box": [
                                int(screen_x),
                                int(screen_y),
                                int(screen_x + box["width"]),
                                int(screen_y + box["height"]),
                            ],
                            "interactivity": True,
                            "role": role_hint,
                            "title": label,
                        })
                        element_id += 1
                    except Exception:
                        continue

            if len(elements) > cfg.max_elements:
                elements = elements[: cfg.max_elements]
                for i, elem in enumerate(elements):
                    elem["id"] = i + 1

            return elements or None

        return _run_async(_get_elements_async())
    except Exception as e:
        logger.error(f"Playwright element scrape failed: {e}")
        return None


def _playwright_to_screenshot_coords(x: float, y: float) -> tuple[int, int]:
    scale = cfg.display_scale_factor
    return int(x / scale), int(y / scale)


def get_perception_method_name(method: str) -> str:
    names = {
        "accessibility": "Accessibility Tree (AXUIElement)",
        "playwright": "Playwright Web Automation",
        "omniparser": "OmniParser + Llama Vision (fallback)",
        "failed": "All Methods Failed",
        "error": "Perception Pipeline Error",
    }
    return names.get(method, method)


def is_perception_available(method: str) -> bool:
    if method == "accessibility":
        return HAS_PYOBJC_AX
    if method == "playwright":
        return HAS_PLAYWRIGHT
    if method == "omniparser":
        return True
    return False


def cleanup_perception_resources():
    try:
        cleanup_playwright()
    except Exception as e:
        logger.debug(f"Perception cleanup failed: {e}")
