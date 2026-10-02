"""Plip on macOS: the notch island, Plip by your cursor, settings, and hold-to-talk.

Threads:
* main thread  - AppKit: island, mascot, settings window, hotkeys, menu bar.
* asyncio loop - the companion turn (look, route, think, speak, point).
* workers      - engine probing, speech recognition, text to speech.

Everything that touches AppKit hops onto the main thread with ``callAfter``.
"""
from __future__ import annotations

import asyncio
import subprocess
import threading
from typing import Any

from mcp_vision.buddy.controller import BuddyController
from mcp_vision.log import get_logger

log = get_logger("mcp_vision.buddy")
_CLASSES: dict[str, type] = {}
PANES = {"screen": "Privacy_ScreenCapture", "accessibility": "Privacy_Accessibility",
         "microphone": "Privacy_Microphone", "speech": "Privacy_SpeechRecognition"}


def _menu_target_class():
    if "target" not in _CLASSES:
        import Foundation
        import objc

        class PlipMenuTarget(Foundation.NSObject):
            def initWithActions_(self, actions):
                self = objc.super(PlipMenuTarget, self).init()
                if self is None:
                    return None
                self.actions = actions
                return self

            def perform_(self, sender):
                action = self.actions.get(str(sender.representedObject()))
                if action:
                    action()

        _CLASSES["target"] = PlipMenuTarget
    return _CLASSES["target"]


def _plip_icon():
    """Menu bar glyph: Plip's round face with two eyes (template image)."""
    import AppKit

    size = 18.0
    image = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(size, size))
    image.lockFocus()
    AppKit.NSColor.blackColor().setFill()
    face = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSMakeRect(2.5, 2.0, 13.0, 12.0), 5.0, 5.0)
    face.fill()
    antenna = AppKit.NSBezierPath.bezierPathWithOvalInRect_(AppKit.NSMakeRect(7.5, 14.6, 3.0, 3.0))
    antenna.fill()
    AppKit.NSGraphicsContext.currentContext().setCompositingOperation_(AppKit.NSCompositingOperationClear)
    for x in (5.8, 10.2):
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(x, 6.0, 2.0, 4.0), 1.0, 1.0).fill()
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
            button.setImage_(_plip_icon())
            button.setToolTip_("Plip - hold Control+Option and ask")
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.status = self._add(menu, "Starting...", None)
        self.status.setEnabled_(False)
        self._add(menu, "Hold ⌃⌥ and ask anything", None).setEnabled_(False)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add(menu, "Open Plip...", "settings", ",")
        self.brain_item = self._add(menu, "Brain: choosing...", "brain")
        self.visible_item = self._add(menu, "Show Plip by my cursor", "toggle_visible")
        self._add(menu, "Forget this conversation", "clear")
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add(menu, "Quit Plip", "quit", "q")
        self.item.setMenu_(menu)

    def _add(self, menu, title: str, key: str | None, shortcut: str = ""):
        import AppKit

        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "perform:" if key else None,
                                                                              shortcut)
        if key:
            item.setTarget_(self.target)
            item.setRepresentedObject_(key)
        menu.addItem_(item)
        return item

    def set_status(self, text: str) -> None:
        self.status.setTitle_(text[:80])

    def set_brain(self, text: str) -> None:
        self.brain_item.setTitle_(f"Brain: {text}"[:60])

    def set_visible_checked(self, checked: bool) -> None:
        import AppKit

        self.visible_item.setState_(AppKit.NSControlStateValueOn if checked else AppKit.NSControlStateValueOff)


def _install_main_menu() -> None:
    """Edit menu so ⌘V/⌘C/⌘A work in the settings window's key fields."""
    import AppKit

    main = AppKit.NSMenu.alloc().init()
    app_item = AppKit.NSMenuItem.alloc().init()
    app_menu = AppKit.NSMenu.alloc().initWithTitle_("Plip")
    app_menu.addItemWithTitle_action_keyEquivalent_("Close Window", "performClose:", "w")
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit Plip", "terminate:", "q")
    app_item.setSubmenu_(app_menu)
    main.addItem_(app_item)
    edit_item = AppKit.NSMenuItem.alloc().init()
    edit = AppKit.NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"),
                               ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                               ("Select All", "selectAll:", "a")):
        edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    edit_item.setSubmenu_(edit)
    main.addItem_(edit_item)
    AppKit.NSApp.setMainMenu_(main)


def _open_pane(anchor: str) -> None:
    _open_url(f"x-apple.systempreferences:com.apple.preference.security?{anchor}")


def _open_url(url: str) -> None:
    import AppKit

    AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(url))


def _copy(text: str) -> None:
    import AppKit

    board = AppKit.NSPasteboard.generalPasteboard()
    board.clearContents()
    board.setString_forType_(text, AppKit.NSPasteboardTypeString)


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run_in_terminal(command: str) -> None:
    script = f'tell application "Terminal"\nactivate\ndo script {applescript_string(command)}\nend tell'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _request_permission(name: str, done) -> None:
    from mcp_vision.native_permissions import request_accessibility, request_screen_recording

    try:
        if name == "screen":
            snapshot = request_screen_recording()
            if not snapshot.get("screenRecording"):
                _open_pane(PANES["screen"])
        elif name == "accessibility":
            snapshot = request_accessibility()
            if not snapshot.get("accessibility"):
                _open_pane(PANES["accessibility"])
        elif name == "microphone":
            import AVFoundation

            AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AVFoundation.AVMediaTypeAudio, lambda _granted: done())
        elif name == "speech":
            import Speech

            Speech.SFSpeechRecognizer.requestAuthorization_(lambda _status: done())
    except Exception as exc:
        log.info("permission request for %s fell back to System Settings: %s", name, exc)
        _open_pane(PANES.get(name, "Privacy"))


def _request_startup_permissions() -> dict[str, bool]:
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


def _webkit_available() -> bool:
    try:
        import WebKit  # noqa: F401
    except ImportError:
        return False
    from mcp_vision.buddy.web_host import bundle_html

    try:
        return bool(bundle_html())
    except OSError:
        return False


def run_buddy_app() -> None:
    import AppKit
    from PyObjCTools import AppHelper

    from mcp_vision.buddy.engines import EngineRegistry
    from mcp_vision.buddy.factory import SetupError, apply_prefs, make_companion
    from mcp_vision.buddy.hotkey import ChordDetector, MacHotkeyListener
    from mcp_vision.buddy.overlay_macos import BuddyOverlay, MainThreadPointer
    from mcp_vision.buddy.presenter import Presenter
    from mcp_vision.buddy.settings import load_settings
    from mcp_vision.buddy.settings_service import Platform, SettingsService
    from mcp_vision.buddy.speech_in import ListenerCallbacks, make_listener
    from mcp_vision.buddy.store import History, Prefs
    from mcp_vision.native_permissions import native_permission_snapshot

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _install_main_menu()

    prefs = Prefs.load()
    state: dict[str, Any] = {"settings": apply_prefs(load_settings(), prefs), "brain_error": "",
                             "listener_error": "", "settings_window": None, "building": False}
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="plip-loop").start()
    history = History()
    registry = EngineRegistry(lambda: state["settings"])

    def main(fn):
        return lambda *args: AppHelper.callAfter(fn, *args)

    # -- windows ---------------------------------------------------------------------
    web = _webkit_available()
    island = None
    if web:
        from mcp_vision.buddy.island_macos import IslandWindow
        from mcp_vision.buddy.mascot_macos import MascotWindow

        island = IslandWindow(lambda command: handle_command(command))
        mascot: Any = MascotWindow(visible=prefs.buddy)
    else:
        log.warning("WebKit bridge unavailable; using the native cursor overlay (pip install pyobjc-framework-WebKit)")
        mascot = BuddyOverlay(visible=prefs.buddy)

    presenter = Presenter(
        post_island=(lambda messages: AppHelper.callAfter(island.post, messages)) if island else (lambda _m: None),
        set_mood=(lambda mood, level: AppHelper.callAfter(mascot.set_mood, mood, level))
        if hasattr(mascot, "set_mood") else None)

    def settings_window():
        if state["settings_window"] is None:
            from mcp_vision.buddy.settings_macos import SettingsWindow

            state["settings_window"] = SettingsWindow(service.handle)
        return state["settings_window"]

    def open_settings(tab: str = "home") -> None:
        if not web:
            _open_url("https://github.com/hussainn7/mcp-vision#readme")
            return
        settings_window().show(tab)

    def post_settings(messages):
        if state["settings_window"] is not None:
            state["settings_window"].post(messages)

    # -- companion (rebuilt whenever settings change) ----------------------------------------
    controller = BuddyController(
        companion=None, overlay=mascot, loop=loop, presenter=presenter,
        call_later=lambda delay, fn: AppHelper.callLater(delay, fn),
        on_main=lambda fn, *args: AppHelper.callAfter(fn, *args),
        on_result=lambda transcript, result: record(transcript, result),
        on_setup_needed=lambda _message: None if state["building"] else open_settings("brain"),
        setup_error="Plip is still waking up. Try again in a second.",
    )

    def record(transcript: str, result) -> None:
        if result.state == "done" and result.spoken:
            badge = getattr(controller.companion, "brain", None)
            history.add(transcript, result.spoken, engine=getattr(badge, "label", ""))

    def update_setup_error() -> None:
        controller.setup_error = state["brain_error"] or state["listener_error"]
        if controller.setup_error:
            menu.set_status("Needs setup: " + controller.setup_error)
        elif controller.hotkey_mode == "none":
            menu.set_status("Grant Accessibility so ⌃⌥ works")
        else:
            menu.set_status("Ready - hold ⌃⌥ and ask")

    def build(probe: bool) -> None:
        """Worker thread: probe engines (spawns CLIs) and assemble a companion."""
        prefs = Prefs.load()
        settings = apply_prefs(load_settings(), prefs)
        state["settings"] = settings
        statuses = registry.statuses(refresh=probe)
        companion, error = None, ""
        try:
            companion = make_companion(settings, pointer=MainThreadPointer(mascot), observer=presenter, prefs=prefs,
                                       engines=statuses, watch=True)
        except SetupError as exc:
            error = str(exc)
        except Exception as exc:          # a broken optional piece must not kill the app
            log.exception("could not build Plip's brain")
            error = f"Couldn't start the brain: {exc}"
        AppHelper.callAfter(install, settings, prefs, companion, error)

    def install(settings, prefs, companion, error) -> None:
        old = controller.companion
        if old is not None and old is not companion:
            loop.call_soon_threadsafe(old.interrupt, None)
        controller.companion = companion
        state["brain_error"] = error
        state["building"] = False
        install_listener(settings)
        update_setup_error()
        if companion is not None:
            menu.set_brain(f"{getattr(companion.brain, 'label', 'model')}")
            warm(companion)
        else:
            menu.set_brain("none yet")
        if hasattr(mascot, "set_visible"):
            mascot.set_visible(prefs.buddy)
        menu.set_visible_checked(prefs.buddy)
        service.push()

    def install_listener(settings) -> None:
        if controller.state != "idle":
            AppHelper.callLater(1.0, lambda: install_listener(settings))
            return
        try:
            controller.listener = make_listener(settings, ListenerCallbacks(
                partial=main(controller.on_partial), final=main(controller.on_final),
                level=main(controller.on_level), error=main(controller.on_error)))
            state["listener_error"] = ""
        except RuntimeError as exc:
            controller.listener = None
            state["listener_error"] = str(exc)

    def warm(companion) -> None:
        if not hasattr(companion.brain, "warm"):
            return

        def warmed(future):
            try:
                log.info("plip brain ready: %s", future.result())
            except Exception as exc:
                log.warning("plip brain warm-up failed: %s", exc)
                AppHelper.callAfter(menu.set_status, "Brain problem: check its key or sign-in in Plip's settings")
        asyncio.run_coroutine_threadsafe(companion.brain.warm(), loop).add_done_callback(warmed)

    def rebuild(probe: bool = False) -> None:
        state["building"] = True
        threading.Thread(target=build, args=(probe,), daemon=True, name="plip-build").start()

    def refresh_engines() -> None:
        def work():
            registry.statuses(refresh=True)
            AppHelper.callAfter(service.push)
        threading.Thread(target=work, daemon=True, name="plip-probe").start()

    def test_voice(text: str) -> None:
        speaker = getattr(controller.companion, "speaker", None)
        if speaker is not None and hasattr(speaker, "speak") and type(speaker).__name__ != "_NullSpeaker":
            speaker.speak(text)
        else:
            subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    service = SettingsService(
        engines=lambda: registry.cards(selected=Prefs.load().engine),
        settings=lambda: state["settings"],
        reload=lambda: rebuild(probe=False),
        post=post_settings,
        platform=Platform(
            copy=_copy, open_url=_open_url, run_in_terminal=_run_in_terminal,
            request_permission=lambda name: _request_permission(name, main(service.push)),
            permissions=native_permission_snapshot, say=test_voice,
            quit=lambda: AppKit.NSApp.terminate_(None), open_settings=open_settings),
        history=history,
        on_refresh=refresh_engines,
    )

    def handle_command(command: dict[str, Any]) -> None:
        if command.get("cmd") == "open-settings":
            open_settings(str(command.get("tab") or "home"))
        elif command.get("cmd") not in {"ready", "island-rect"}:
            service.handle(command)

    # -- menu bar --------------------------------------------------------------------------
    def toggle_visible():
        prefs = Prefs.load()
        prefs.buddy = not prefs.buddy
        prefs.save()
        if hasattr(mascot, "set_visible"):
            mascot.set_visible(prefs.buddy)
        menu.set_visible_checked(prefs.buddy)

    def clear():
        if controller.companion:
            controller.companion.conversation.clear()
        menu.set_status("Fresh start - conversation cleared")

    menu = StatusMenu({
        "settings": lambda: open_settings("home"), "brain": lambda: open_settings("brain"),
        "toggle_visible": toggle_visible, "clear": clear, "quit": lambda: AppKit.NSApp.terminate_(None),
    })
    menu.set_visible_checked(prefs.buddy)
    controller.status = menu.set_status

    detector = ChordDetector(on_press=controller.on_press, on_release=controller.on_release,
                             on_cancel=controller.on_cancel)
    hotkeys = MacHotkeyListener(detector)
    controller.hotkey_mode = hotkeys.start()
    _request_startup_permissions()
    rebuild(probe=True)

    if not prefs.onboarded:
        prefs.onboarded = True
        prefs.save()
        AppHelper.callLater(0.8, lambda: open_settings("home"))

    log.info("plip running (hotkey=%s, web=%s)", controller.hotkey_mode, web)
    print("Plip is in your menu bar and notch. Hold Control+Option and ask.", flush=True)
    AppHelper.runEventLoop(installInterrupt=True)
