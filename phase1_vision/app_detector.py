"""
Frontmost app detection via PyObjC.
Used to pick the right perception backend (browser vs native).
"""

from typing import Dict, Optional

try:
    import Cocoa
    import Quartz
    HAS_PYOBJC = True
except ImportError:
    HAS_PYOBJC = False

from loguru import logger

BROWSER_BUNDLE_IDS = {
    "com.apple.Safari",
    "com.google.Chrome",
    "com.mozilla.firefox",
    "com.brave.browser",
    "com.microsoft.edgemac",
    "com.operasoftware.Opera",
    "org.mozilla.nightly",
    "org.mozilla.firefox.developer",
}


def get_frontmost_app() -> Dict[str, Optional[str] | bool]:
    if not HAS_PYOBJC:
        logger.warning("PyObjC not available")
        return {
            "name": None,
            "bundle_id": None,
            "is_browser": False,
            "needs_ax_permission": False,
        }

    try:
        frontmost_app = Cocoa.NSWorkspace.sharedWorkspace().frontmostApplication()
        if frontmost_app is None:
            return {
                "name": None,
                "bundle_id": None,
                "is_browser": False,
                "needs_ax_permission": False,
            }

        app_name = frontmost_app.localizedName() or frontmost_app.displayName()
        bundle_id = frontmost_app.bundleIdentifier()

        return {
            "name": str(app_name) if app_name else None,
            "bundle_id": str(bundle_id) if bundle_id else None,
            "is_browser": is_browser_app(bundle_id),
            "needs_ax_permission": True,
        }
    except Exception as e:
        logger.error(f"Error detecting frontmost app: {e}")
        return {
            "name": None,
            "bundle_id": None,
            "is_browser": False,
            "needs_ax_permission": False,
        }


def is_browser_app(bundle_id: Optional[str]) -> bool:
    return bool(bundle_id and bundle_id in BROWSER_BUNDLE_IDS)


def get_display_info() -> list[dict]:
    if not HAS_PYOBJC:
        return [{
            "index": 0,
            "width": 1920,
            "height": 1080,
            "origin_x": 0,
            "origin_y": 0,
            "scale_factor": 1.0,
        }]

    try:
        displays = []
        display_ids = Quartz.CGGetOnlineDisplayList(Quartz.kCGNullDirectDisplay, None, None)[1]

        for i, display_id in enumerate(display_ids):
            bounds = Quartz.CGDisplayBounds(display_id)
            displays.append({
                "index": i,
                "width": int(Quartz.CGDisplayPixelsWide(display_id)),
                "height": int(Quartz.CGDisplayPixelsHigh(display_id)),
                "origin_x": int(bounds.origin.x),
                "origin_y": int(bounds.origin.y),
                "scale_factor": float(Quartz.CGDisplayBackingScaleFactor(display_id)),
            })

        return displays
    except Exception as e:
        logger.error(f"Error getting display info: {e}")
        return [{
            "index": 0,
            "width": 1920,
            "height": 1080,
            "origin_x": 0,
            "origin_y": 0,
            "scale_factor": 1.0,
        }]
