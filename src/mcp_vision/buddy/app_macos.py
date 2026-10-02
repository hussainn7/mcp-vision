"""The macOS buddy app: menu bar item, cursor overlay, and hold-to-talk.

Threads:
* main thread  - AppKit: overlay drawing, hotkey events, menu bar.
* asyncio loop - the companion turn (capture, Jev routing, Claude stream).
* helpers      - speech recognition and text to speech run their own threads.

Every callback that touches AppKit is hopped onto the main thread.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from mcp_vision.buddy.controller import BuddyController
from mcp_vision.log import get_logger

log = get_logger("mcp_vision.buddy")
_CLASSES: dict[str, type] = {}


def _menu_target_class():
    if "target" not in _CLASSES:
        import Foundation
        import objc

        class BuddyMenuTarget(Foundation.NSObject):
            def initWithActions_(self, actions):
                self = objc.super(BuddyMenuTarget, self).init()
                if self is None:
                    return None
                self.actions = actions
                return self

            def perform_(self, sender):
                action = self.actions.get(str(sender.representedObject()))
                if action:
                    action()

        _CLASSES["target"] = BuddyMenuTarget
    return _CLASSES["target"]


def _triangle_icon():
    import AppKit

    size = 18.0
    image = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(size, size))
    image.lockFocus()
    transform = AppKit.NSAffineTransform.transform()
    transform.translateXBy_yBy_(size / 2, size / 2)
    transform.rotateByDegrees_(35)            # AppKit is y-up: positive = counterclockwise
    transform.concat()
    path = AppKit.NSBezierPath.bezierPath()
    path.moveToPoint_(AppKit.NSMakePoint(0, 7))
    path.lineToPoint_(AppKit.NSMakePoint(-6, -4.5))
    path.lineToPoint_(AppKit.NSMakePoint(6, -4.5))
    path.closePath()
    AppKit.NSColor.blackColor().setFill()
    path.fill()
    image.unlockFocus()
    image.setTemplate_(True)
    return image


class StatusMenu:
    def __init__(self, actions: dict[str, Any]):
        import AppKit

        self.target = _menu_target_class().alloc().initWithActions_(actions)
        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = self.item.button()
        if button is not None:
            button.setImage_(_triangle_icon())
            button.setToolTip_("Buddy - hold Control+Option to talk")
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.status = self._add(menu, "Starting...", None)
        self.status.setEnabled_(False)
        self._add(menu, "Hold Control+Option and talk", None).setEnabled_(False)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.visible_item = self._add(menu, "Always show buddy", "toggle_visible")
        self._add(menu, "Forget this conversation", "clear")
        self._add(menu, "Check setup...", "doctor")
        self._add(menu, "Open Screen Recording settings...", "screen_settings")
        self._add(menu, "Open Accessibility settings...", "ax_settings")
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add(menu, "Quit Buddy", "quit")
        self.item.setMenu_(menu)

    def _add(self, menu, title: str, key: str | None):
        import AppKit

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "perform:" if key else None, "")
        if key:
            item.setTarget_(self.target)
            item.setRepresentedObject_(key)
        menu.addItem_(item)
        return item

    def set_status(self, text: str) -> None:
        self.status.setTitle_(text[:80])

    def set_visible_checked(self, checked: bool) -> None:
        import AppKit

        self.visible_item.setState_(AppKit.NSControlStateValueOn if checked else AppKit.NSControlStateValueOff)


def _open_settings(anchor: str) -> None:
    import AppKit

    AppKit.NSWorkspace.sharedWorkspace().openURL_(
        AppKit.NSURL.URLWithString_(f"x-apple.systempreferences:com.apple.preference.security?{anchor}"))


def _request_permissions() -> dict[str, bool]:
    granted = {"screen": False, "accessibility": False}
    try:
        import Quartz

        granted["screen"] = bool(Quartz.CGPreflightScreenCaptureAccess())
        if not granted["screen"]:
            granted["screen"] = bool(Quartz.CGRequestScreenCaptureAccess())
    except Exception:
        pass
    try:
        import ApplicationServices as AX

        granted["accessibility"] = bool(AX.AXIsProcessTrustedWithOptions({AX.kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        pass
    return granted


def _alert(title: str, text: str) -> None:
    import AppKit

    AppKit.NSApp.activateIgnoringOtherApps_(True)
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(text)
    alert.runModal()


def run_buddy_app() -> None:
    import AppKit
    from PyObjCTools import AppHelper

    from mcp_vision.buddy.factory import SetupError, make_companion
    from mcp_vision.buddy.hotkey import ChordDetector, MacHotkeyListener
    from mcp_vision.buddy.overlay_macos import BuddyOverlay, MainThreadPointer
    from mcp_vision.buddy.settings import load_settings
    from mcp_vision.buddy.speech_in import ListenerCallbacks, make_listener

    settings = load_settings()
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    overlay = BuddyOverlay(visible=settings.always_visible)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="buddy-loop").start()

    setup_error = ""
    companion = None
    try:
        companion = make_companion(settings, pointer=MainThreadPointer(overlay))
    except SetupError as exc:
        setup_error = str(exc)

    def main(fn):
        return lambda *args: AppHelper.callAfter(fn, *args)

    controller = BuddyController(
        companion=companion, overlay=overlay, loop=loop, setup_error=setup_error,
        call_later=lambda delay, fn: AppHelper.callLater(delay, fn),
        on_main=lambda fn, *args: AppHelper.callAfter(fn, *args),
    )
    try:
        listener = make_listener(settings, ListenerCallbacks(
            partial=main(controller.on_partial), final=main(controller.on_final),
            level=main(controller.on_level), error=main(controller.on_error)))
        controller.listener = listener
    except RuntimeError as exc:
        controller.setup_error = controller.setup_error or str(exc)

    def toggle_visible():
        overlay.set_visible(not overlay.visible)
        menu.set_visible_checked(overlay.visible)

    def doctor():
        from mcp_vision.native_permissions import native_permission_snapshot

        snap = native_permission_snapshot()
        lines = [f"Brain: {settings.model if companion else controller.setup_error}",
                 f"Router: {type(companion.router).__name__ if companion and companion.router else 'off'}",
                 f"Voice in: {getattr(controller.listener, 'name', 'unavailable')}",
                 f"Screen Recording: {snap.get('screenRecording')}",
                 f"Accessibility: {snap.get('accessibility')}",
                 f"Microphone: {snap.get('microphoneStatus')}",
                 f"Hotkey: {controller.hotkey_mode}"]
        _alert("Buddy setup", "\n".join(lines))

    def clear():
        if companion:
            companion.conversation.clear()
        menu.set_status("Conversation cleared")

    menu = StatusMenu({
        "toggle_visible": toggle_visible, "clear": clear, "doctor": doctor,
        "screen_settings": lambda: _open_settings("Privacy_ScreenCapture"),
        "ax_settings": lambda: _open_settings("Privacy_Accessibility"),
        "quit": lambda: AppKit.NSApp.terminate_(None),
    })
    menu.set_visible_checked(overlay.visible)
    controller.status = menu.set_status

    detector = ChordDetector(on_press=controller.on_press, on_release=controller.on_release,
                             on_cancel=controller.on_cancel)
    hotkeys = MacHotkeyListener(detector)
    controller.hotkey_mode = hotkeys.start()

    granted = _request_permissions()
    if controller.setup_error:
        menu.set_status("Needs setup: " + controller.setup_error)
    elif controller.hotkey_mode == "none":
        menu.set_status("Grant Accessibility, then restart Buddy")
    elif not granted["screen"]:
        menu.set_status("Grant Screen Recording, then restart Buddy")
    else:
        menu.set_status("Ready - hold Control+Option")

    if companion is not None and hasattr(companion.brain, "warm"):
        def warmed(future):
            try:
                name = future.result()
                log.info("buddy brain ready: %s", name)
            except Exception as exc:
                log.warning("buddy brain warm-up failed: %s", exc)
                AppHelper.callAfter(menu.set_status, "Model key problem: check ANTHROPIC_API_KEY")
        asyncio.run_coroutine_threadsafe(companion.brain.warm(), loop).add_done_callback(warmed)

    log.info("buddy running (hotkey=%s, listener=%s)", controller.hotkey_mode,
             getattr(controller.listener, "name", None))
    print("Buddy is running in your menu bar. Hold Control+Option and talk.", flush=True)
    AppHelper.runEventLoop(installInterrupt=True)
