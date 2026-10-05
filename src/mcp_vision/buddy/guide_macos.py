"""The permission card on macOS: a small panel docked to System Settings (see ``permission_guide.py``).

The card is the web UI's ``guide`` surface in a borderless, non-activating panel, so System
Settings stays in front while you use it. The app row on it is a real AppKit drag source
laid over the web view: dragging it hands System Settings the app bundle, like dragging
the app from Finder. A 150 ms timer follows the System Settings window (CGWindowList: just
window positions, no permission needed) and checks the permission.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from mcp_vision.buddy.permission_guide import (
    DRAGGABLE, NAMES, PANEL, ROW, SETTINGS_APP, GuideFlow, appkit_frame, bundle_for, card_text, pane_url,
    settings_window,
)
from mcp_vision.buddy.web_host import WebSurface

_CLASSES: dict[str, type] = {}


def responsible_app() -> tuple[str, str]:
    """(name, bundle path) of the app macOS grants permissions to: Plip.app, or the terminal Plip runs in."""
    from Foundation import NSBundle

    bundle = str(NSBundle.mainBundle().bundlePath() or "")
    if bundle.endswith(".app"):
        return "Plip", bundle
    app = bundle_for(_pid_path(_responsible_pid(os.getpid()) or 0))
    if not app:
        return "Plip", ""
    try:
        import plistlib

        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as handle:
            info = plistlib.load(handle)
        name = str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or "")
    except Exception:
        name = ""
    return name or os.path.basename(app)[:-4], app


def _responsible_pid(pid: int) -> int | None:
    """The process macOS asks permissions for: libquarantine's answer, else the nearest ancestor in an .app."""
    import ctypes
    import subprocess

    for library in (None, "/usr/lib/system/libquarantine.dylib"):
        try:
            fn = ctypes.CDLL(library).responsibility_get_pid_responsible_for_pid
        except (AttributeError, OSError):
            continue
        fn.argtypes, fn.restype = [ctypes.c_int], ctypes.c_int
        if (value := int(fn(pid))) > 0:
            return value
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=2).stdout
    except Exception:
        return None
    table = {int(parts[0]): (int(parts[1]), parts[2]) for parts in (line.split(None, 2) for line in out.splitlines())
             if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit()}
    current, seen = pid, set()
    while current in table and current not in seen:
        seen.add(current)
        parent, command = table[current]
        if bundle_for(command):
            return current
        current = parent
    return None


def _pid_path(pid: int) -> str:
    import ctypes

    if pid <= 0:
        return ""
    buffer = ctypes.create_string_buffer(4096)          # PROC_PIDPATHINFO_MAXSIZE
    fn = ctypes.CDLL(None).proc_pidpath
    fn.argtypes, fn.restype = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int
    return buffer.value.decode("utf-8", "replace") if fn(pid, buffer, ctypes.sizeof(buffer)) > 0 else ""


def icon_data_url(bundle: str, pixels: int = 96) -> str:
    """The app's Finder icon as a PNG data URL, for the card's app row."""
    import base64

    import AppKit

    if not bundle:
        return ""
    image = AppKit.NSWorkspace.sharedWorkspace().iconForFile_(bundle)
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, pixels, pixels, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    image.drawInRect_fromRect_operation_fraction_(AppKit.NSMakeRect(0, 0, pixels, pixels), AppKit.NSZeroRect,
                                                  AppKit.NSCompositingOperationSourceOver, 1.0)
    AppKit.NSGraphicsContext.restoreGraphicsState()
    png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    return "data:image/png;base64," + base64.b64encode(bytes(png)).decode()


def is_granted(permission: str) -> bool | None:
    """Is the permission on right now? None when macOS can't be asked (the card then waits for you)."""
    if permission == "fulldisk":
        try:
            with open(os.path.expanduser("~/Library/Messages/chat.db"), "rb"):
                return True
        except PermissionError:
            return False
        except OSError:
            return None
    key = {"accessibility": "accessibility", "screen": "screenRecording", "microphone": "microphone",
           "speech": "speechRecognition"}.get(permission)
    if key is None:
        return None
    from mcp_vision.native_permissions import native_permission_snapshot

    return native_permission_snapshot().get(key)


def _ticker_class():
    if "ticker" not in _CLASSES:
        import Foundation
        import objc

        class GuideTicker(Foundation.NSObject):
            def initWithCallback_(self, callback):
                self = objc.super(GuideTicker, self).init()
                if self is None:
                    return None
                self.callback = callback
                return self

            def fire_(self, _timer):
                try:
                    self.callback()
                except Exception:
                    import traceback
                    traceback.print_exc()

        _CLASSES["ticker"] = GuideTicker
    return _CLASSES["ticker"]


def _drag_view_class():
    if "drag" not in _CLASSES:
        import AppKit
        import Foundation
        import objc

        class AppDragView(AppKit.NSView, protocols=[objc.protocolNamed("NSDraggingSource")]):
            """The card's app row: press and drag it into the System Settings list."""

            def initWithFrame_(self, frame):
                self = objc.super(AppDragView, self).initWithFrame_(frame)
                if self is None:
                    return None
                self.bundle = ""
                return self

            def acceptsFirstMouse_(self, _event):
                return True

            def resetCursorRects(self):
                if self.bundle:
                    self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.openHandCursor())

            def mouseDown_(self, event):
                if not self.bundle:
                    return
                item = AppKit.NSDraggingItem.alloc().initWithPasteboardWriter_(
                    Foundation.NSURL.fileURLWithPath_(self.bundle))
                icon = AppKit.NSWorkspace.sharedWorkspace().iconForFile_(self.bundle)
                icon.setSize_(AppKit.NSMakeSize(44, 44))
                point = self.convertPoint_fromView_(event.locationInWindow(), None)
                item.setDraggingFrame_contents_(AppKit.NSMakeRect(point.x - 22, point.y - 22, 44, 44), icon)
                session = self.beginDraggingSessionWithItems_event_source_([item], event, self)
                session.setAnimatesToStartingPositionsOnCancelOrFail_(True)

            def draggingSession_sourceOperationMaskForDraggingContext_(self, _session, _context):
                return AppKit.NSDragOperationCopy

        _CLASSES["drag"] = AppDragView
    return _CLASSES["drag"]


class PermissionGuide:
    """Shows the card for one permission at a time; ``on_done(permission, granted)`` when it closes."""

    INTERVAL = 0.15

    def __init__(self, on_done: Callable[[str, bool], None]):
        self.on_done = on_done
        self.flow: GuideFlow | None = None
        self.panel: Any = None
        self.surface: WebSurface | None = None
        self.drag: Any = None
        self._timer: Any = None
        self._ticks = 0
        self._granted: bool | None = None
        self._ticker = _ticker_class().alloc().initWithCallback_(self._tick)

    @property
    def active(self) -> str:
        return self.flow.permission if self.flow is not None else ""

    def _ensure_panel(self) -> None:
        if self.panel is not None:
            return
        import AppKit

        width, height = PANEL
        rect = AppKit.NSMakeRect(0, 0, width, height)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(False)                        # the card draws its own
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        container = AppKit.NSView.alloc().initWithFrame_(rect)
        self.surface = WebSurface("guide", rect, self._command)
        container.addSubview_(self.surface.view)
        x, y, row_w, row_h = ROW
        self.drag = _drag_view_class().alloc().initWithFrame_(AppKit.NSMakeRect(x, height - y - row_h, row_w, row_h))
        container.addSubview_(self.drag)                  # on top of the web view: it takes the row's mouse
        panel.setContentView_(container)
        self.panel = panel

    def start(self, permission: str) -> None:
        """Open the right System Settings page and dock the card to it."""
        import AppKit

        self._ensure_panel()
        name, bundle = responsible_app()
        draggable = permission in DRAGGABLE and bool(bundle)
        self.drag.bundle = bundle if draggable else ""
        self.drag.setHidden_(not draggable)
        self.panel.invalidateCursorRectsForView_(self.drag)
        title, hint = card_text(permission, name)
        if not draggable and permission in DRAGGABLE:
            title = f"Switch on {name} in the list above"
        self.surface.post([{"type": "guide", "state": {
            "permission": permission, "name": NAMES.get(permission, permission), "app": name,
            "icon": icon_data_url(bundle), "title": title, "hint": hint, "draggable": draggable,
            "granted": False, "panel": {"width": PANEL[0], "height": PANEL[1]},
            "row": {"x": ROW[0], "y": ROW[1], "width": ROW[2], "height": ROW[3]}}}])
        self.flow = GuideFlow(permission)
        self._ticks = 0
        self._granted = None
        AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(pane_url(permission)))
        if self._timer is None:
            self._timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
                self.INTERVAL, self._ticker, "fire:", None, True)
            AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, AppKit.NSRunLoopCommonModes)

    def stop(self, granted: bool = False) -> None:
        permission = self.active
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self.panel is not None:
            self.panel.orderOut_(None)
        self.flow = None
        if permission:
            self.on_done(permission, granted)

    def _command(self, command: dict[str, Any]) -> None:
        if command.get("cmd") == "guide-close":
            self.stop(False)

    def _tick(self) -> None:
        import AppKit
        import Quartz

        flow = self.flow
        if flow is None:
            return
        self._ticks += 1
        if self._ticks % 3 == 1:                          # the permission twice a second, the window every tick
            self._granted = is_granted(flow.permission)
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        in_front = front is not None and str(front.bundleIdentifier() or "") == SETTINGS_APP
        frame = visible = None
        if in_front:
            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID) or []
            found = settings_window(list(windows), int(front.processIdentifier()))
            screens = AppKit.NSScreen.screens()
            if found is not None and screens:
                frame = appkit_frame(found, float(screens[0].frame().size.height))
                visible = _visible_frame(screens, frame)
        running = bool(AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(SETTINGS_APP))
        step = flow.tick(granted=self._granted, settings_front=in_front, frame=frame, visible=visible,
                         settings_open=running)
        if step.granted:
            self.surface.post([{"type": "guide", "state": {"granted": True}}])
            self.drag.setHidden_(True)
        if step.show is not None:
            self.panel.setFrameOrigin_(AppKit.NSMakePoint(*step.show))
            if not self.panel.isVisible():
                self.surface.post([{"type": "guide", "state": {"shown": self._ticks}}])     # replays the entrance
            self.panel.orderFrontRegardless()
        elif step.hide and self.panel.isVisible():
            self.panel.orderOut_(None)
        if step.close:
            self.stop(flow.done)


def _visible_frame(screens: Any, frame: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """The visible area (no menu bar or Dock) of the screen the window is mostly on."""
    x, y, width, height = frame
    center = (x + width / 2, y + height / 2)
    chosen = screens[0]
    for screen in screens:
        box = screen.frame()
        if box.origin.x <= center[0] <= box.origin.x + box.size.width and \
                box.origin.y <= center[1] <= box.origin.y + box.size.height:
            chosen = screen
            break
    visible = chosen.visibleFrame()
    return (float(visible.origin.x), float(visible.origin.y), float(visible.size.width), float(visible.size.height))
