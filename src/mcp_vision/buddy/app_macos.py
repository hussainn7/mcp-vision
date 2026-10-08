"""Plip on macOS: the notch island, Plip by your cursor, settings, and hold-to-talk.

Threads:
* main thread  - AppKit: island, mascot, settings window, hotkeys, menu bar.
* asyncio loop - the companion turn (look, route, think, speak, point).
* workers      - engine probing, speech recognition, text to speech.

Everything that touches AppKit hops onto the main thread with ``callAfter``.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from typing import Any

from mcp_vision.buddy.controller import BuddyController
from mcp_vision.log import get_logger

log = get_logger("mcp_vision.buddy")
_CLASSES: dict[str, type] = {}
# Switched on in System Settings (the card guides you there); the others are a one-click macOS prompt.
GUIDED = {"accessibility", "screen", "fulldisk", "contacts", "automation"}


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
    """Menu bar glyph: the app icon's hood silhouette with its pill eye cut out (template image)."""
    import AppKit

    size = 18.0
    image = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(size, size))
    image.lockFocus()
    # Same curves as the UI mascot (64-unit box, y down), scaled into 18 pt with y flipped.
    scale = size / 64.0

    def point(x, y):
        return AppKit.NSMakePoint(x * scale, size - y * scale)

    hood = AppKit.NSBezierPath.bezierPath()
    hood.moveToPoint_(point(15, 60))
    for c1, c2, end in (((9, 57), (8, 52), (8.2, 46)), ((8.5, 30), (22, 4), (37, 4.1)),
                        ((42, 4), (45.5, 6.5), (47.3, 9.4)), ((51, 14), (54.5, 21), (55.5, 27.1)),
                        ((56.5, 34), (52, 40), (45.9, 43.5)), ((40, 47), (31, 48.5), (25.9, 50.6)),
                        ((22, 53), (19, 61), (15, 60))):
        hood.curveToPoint_controlPoint1_controlPoint2_(point(*end), point(*c1), point(*c2))
    hood.closePath()
    AppKit.NSColor.blackColor().setFill()
    hood.fill()
    AppKit.NSGraphicsContext.currentContext().setCompositingOperation_(AppKit.NSCompositingOperationClear)
    eye = AppKit.NSMakeRect(40.0 * scale, size - 31.5 * scale, 6.6 * scale, 12.0 * scale)
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(eye, 3.3 * scale, 3.3 * scale).fill()
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
        self.hint = self._add(menu, "Hold ⌃⌥ and ask anything", None)
        self.hint.setEnabled_(False)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add(menu, "Open Plip...", "settings", ",")
        self.update_item = self._add(menu, "Download the new Plip...", "update")
        self.update_item.setHidden_(True)
        self.brain_item = self._add(menu, "Brain: choosing...", "brain")
        self.visible_item = self._add(menu, "Show Plip by my cursor", "toggle_visible")
        self._add(menu, "Forget this conversation", "clear")
        self._add(menu, "Report a bug...", "report")
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

    def set_shortcut(self, chord) -> None:
        """The talk shortcut picked in Settings (hotkey.Chord)."""
        self.hint.setTitle_(f"Hold {chord.symbols} and ask anything")
        button = self.item.button()
        if button is not None:
            button.setToolTip_(f"Plip - hold {chord.label} and ask")

    def set_brain(self, text: str) -> None:
        self.brain_item.setTitle_(f"Brain: {text}"[:60])

    def set_update(self, version: str | None) -> None:
        """A newer Plip is out: one click downloads it. Hidden when there's nothing new."""
        if version:
            self.update_item.setTitle_(f"Download Plip {version}...")
        self.update_item.setHidden_(not version)

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


def _relaunch() -> bool:
    """Quit and open Plip again (macOS only applies Screen Recording to a fresh process)."""
    import shlex

    import AppKit

    bundle = str(AppKit.NSBundle.mainBundle().bundlePath() or "")
    if not bundle.endswith(".app"):
        return False                                   # started from a terminal: nothing to reopen
    subprocess.Popen(["/bin/sh", "-c", f"sleep 1; /usr/bin/open {shlex.quote(bundle)}"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    AppKit.NSApp.terminate_(None)
    return True


def _asked_before(name: str) -> bool:
    """Has macOS already been asked for this one (so its prompt won't show again)?"""
    from mcp_vision.native_permissions import native_permission_snapshot

    snapshot = native_permission_snapshot()
    status = snapshot.get("microphoneStatus" if name == "microphone" else "speechRecognitionStatus")
    return status in {"denied", "restricted"}


def _request_permission(name: str, done) -> None:
    from mcp_vision.buddy.permission_guide import PANES
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
        else:
            _open_pane(PANES[name])
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

    from mcp_vision.buddy.actions import ActionLog
    from mcp_vision.buddy.engines import EngineRegistry
    from mcp_vision.buddy.factory import SetupError, apply_prefs, make_companion
    from mcp_vision.buddy.hotkey import ChordDetector, MacHotkeyListener, chord, keyboard_owner
    from mcp_vision.buddy.overlay_macos import BuddyOverlay, MainThreadPointer
    from mcp_vision.buddy.presenter import Presenter
    from mcp_vision.buddy.settings import load_settings
    from mcp_vision.buddy.settings_service import Platform, SettingsService
    from mcp_vision.buddy.speech_in import ListenerCallbacks, make_listener
    from mcp_vision.buddy.usage import UsageLog
    from mcp_vision.buddy.store import History, Prefs
    from mcp_vision.native_permissions import native_permission_snapshot

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _install_main_menu()

    prefs = Prefs.load()
    state: dict[str, Any] = {"settings": apply_prefs(load_settings(), prefs), "brain_error": "",
                             "listener_error": "", "settings_window": None, "building": False}
    loop = asyncio.new_event_loop()
    from mcp_vision.buddy import workers

    workers.install(loop)                         # to_thread jobs drain their autorelease pools
    threading.Thread(target=loop.run_forever, daemon=True, name="plip-loop").start()
    history = History()
    registry = EngineRegistry(lambda: state["settings"])
    from mcp_vision.buddy.memory import Memory

    memory = Memory()
    usage_log = UsageLog()                # the Usage tab: one row per request
    from mcp_vision.buddy.account import Account

    # Sign in once with Google before Plip works (only in builds with a sign-in project).
    account = Account(state["settings"].supabase_url, state["settings"].supabase_key, open_url=_open_url,
                      on_change=lambda: AppHelper.callAfter(account_changed),
                      on_signed_in=lambda: AppHelper.callAfter(signed_in))
    state["signed_in"] = not account.required

    def main(fn):
        return lambda *args: AppHelper.callAfter(fn, *args)

    # -- windows ---------------------------------------------------------------------
    web = _webkit_available()
    island = None
    if web:
        from mcp_vision.buddy.island_macos import IslandWindow
        from mcp_vision.buddy.mascot_macos import MascotWindow

        island = IslandWindow(lambda command: handle_command(command))
        mascot: Any = MascotWindow(visible=prefs.buddy, style=prefs.companion)
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
            _open_url("https://github.com/hussainn7/plip-oss#readme")
            return
        settings_window().show(tab)

    # -- permissions: one click, then the card in System Settings shows where -------------------
    def permission_guide():
        if state.get("guide") is None:
            from mcp_vision.buddy.guide_macos import PermissionGuide

            state["guide"] = PermissionGuide(on_done=guide_done)
        return state["guide"]

    def guide_done(permission: str, granted: bool) -> None:
        if granted and permission == "screen":
            state["restart_for_screen"] = True         # macOS applies it to the next launch
        update_setup_error()                           # Accessibility on: the shortcut starts working
        service.push()
        if granted and state["settings_window"] is not None:
            state["settings_window"].front()           # back to Plip, where you left off

    def request_permission(name: str) -> None:
        if name in GUIDED or (name in {"microphone", "speech"} and _asked_before(name)):
            permission_guide().start(name)
        else:
            _request_permission(name, main(service.push))
        service.push()

    def permissions() -> dict[str, Any]:
        guide = state.get("guide")
        return {**native_permission_snapshot(), "restart": bool(state.get("restart_for_screen")),
                "guiding": guide.active if guide is not None else ""}

    # -- a newer Plip: the menu bar and Settings offer it, the notch says so once -----------------------
    from mcp_vision.buddy.updates import Updates

    updates = Updates(enabled=lambda: Prefs.load().update_check,
                      on_found=lambda release: AppHelper.callAfter(tell_update, release))

    def show_update(found) -> None:
        menu.set_update(found["version"] if found else None)
        service.push()

    def tell_update(release, tries: int = 0) -> None:
        if controller.state != "idle" or presenter.phase != "idle":     # never over a question in progress
            if tries < 30:
                AppHelper.callLater(60.0, lambda: tell_update(release, tries + 1))
            return
        presenter("notice", {"text": f"Plip {release['version']} is out. Download it from the menu bar "
                                     "or Settings → General."})

    def check_updates(force: bool = False) -> None:
        threading.Thread(target=lambda: AppHelper.callAfter(show_update, updates.check(force=force)),
                         daemon=True, name="plip-update-check").start()

    def watch_updates() -> None:
        from mcp_vision.analytics import ping

        while True:                                   # GitHub is asked at most once a day (Updates.check)
            AppHelper.callAfter(show_update, updates.check())
            time.sleep(6 * 60 * 60)
            ping("app")                               # Plip stays open for days: still one anonymous ping a day

    # -- the account: Plip starts working once they're signed in, and stops if they sign out ------------
    def account_changed() -> None:
        signed = not account.required
        if signed != state["signed_in"]:
            state["signed_in"] = signed
            update_setup_error()                       # ⌃⌥ follows: "I need a little setup first" while signed out
        service.push()

    def signed_in() -> None:
        account_changed()
        if state["settings_window"] is not None:
            state["settings_window"].front()           # back from the browser, on to the walkthrough

    def restart() -> None:
        if not _relaunch():
            menu.set_status("Quit Plip and start it again to finish turning on Screen Recording")

    def post_settings(messages):
        if state["settings_window"] is not None:
            state["settings_window"].post(messages)

    # -- companion (rebuilt whenever settings change) ----------------------------------------
    controller = BuddyController(
        companion=None, overlay=mascot, loop=loop, presenter=presenter,
        call_later=lambda delay, fn: AppHelper.callLater(delay, fn),
        on_main=lambda fn, *args: AppHelper.callAfter(fn, *args),
        on_result=lambda transcript, result: record(transcript, result),
        on_setup_needed=lambda _message: None if state["building"] else open_settings(
            "home" if account.required else "brain"),
        setup_error="Plip is still waking up. Try again in a second.",
    )

    def record(transcript: str, result) -> None:
        if result.state == "done" and result.spoken:
            badge = getattr(controller.companion, "brain", None)
            history.add(transcript, result.spoken, engine=getattr(badge, "label", ""))
        if state["settings_window"] is not None:
            AppHelper.callAfter(service.push)     # an open History / Usage tab shows it right away

    def update_setup_error() -> None:
        controller.setup_error = account.blocker or state["brain_error"] or state["listener_error"]
        if controller.setup_error:
            menu.set_status("Needs setup: " + controller.setup_error)
        elif hotkey_mode() == "none":
            menu.set_status(f"Allow Accessibility for {keyboard_owner()} so {controller.shortcut_keys} works")
        else:
            menu.set_status(f"Ready - hold {controller.shortcut_keys} and ask")

    def hotkey_mode() -> str:
        """Asked fresh: Accessibility granted since launch starts listening again (hotkeys.mode)."""
        hotkeys = state.get("hotkeys")
        controller.hotkey_mode = hotkeys.mode() if hotkeys is not None else "none"
        return controller.hotkey_mode

    def apply_hotkey(name: str | None = None) -> None:
        """Listen for the talk shortcut from Settings, and say it everywhere Plip says "hold ⌃⌥"."""
        picked = chord(name if name is not None else Prefs.load().hotkey)
        detector.set_chord(picked.mask)
        controller.shortcut, controller.shortcut_keys = picked.label.replace(" + ", "+"), picked.symbols
        menu.set_shortcut(picked)
        if island is not None:
            island.post([{"type": "shortcut", "state": picked.card()}])
        update_setup_error()

    def build(probe: bool) -> None:
        """Worker thread: probe engines (spawns CLIs) and assemble a companion."""
        prefs = Prefs.load()
        settings = apply_prefs(load_settings(), prefs)
        state["settings"] = settings
        statuses = registry.statuses(refresh=probe)
        companion, error = None, ""
        try:
            companion = make_companion(settings, pointer=MainThreadPointer(mascot), observer=presenter, prefs=prefs,
                                       engines=statuses, watch=True, memory=memory, usage=usage_log)
        except SetupError as exc:
            error = str(exc)
        except Exception as exc:          # a broken optional piece must not kill the app
            log.exception("could not build Plip's brain")
            error = f"Couldn't start the brain: {exc}"
        AppHelper.callAfter(install, settings, prefs, companion, error)

    def default_host_cached():
        if "host" not in state:
            from mcp_vision.buddy.actions.host import default_host

            state["host"] = default_host()
        return state["host"]

    def install(settings, prefs, companion, error) -> None:
        old = controller.companion
        if old is not None and old is not companion:
            loop.call_soon_threadsafe(old.interrupt, None)
            closing = getattr(old.brain, "aclose", None)
            if closing is not None:                 # its warm process won't be used again
                asyncio.run_coroutine_threadsafe(closing(), loop)
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
        if hasattr(mascot, "set_style"):
            mascot.set_style(prefs.companion)
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
        if hasattr(companion.brain, "prewarm"):
            companion.brain.prewarm = True          # the app lives on: keep the next brain process ready
        asyncio.run_coroutine_threadsafe(companion.brain.warm(), loop).add_done_callback(warmed)

    def rebuild(probe: bool = False) -> None:
        state["building"] = True
        threading.Thread(target=build, args=(probe,), daemon=True, name="plip-build").start()

    def refresh_engines() -> None:
        def work():
            registry.statuses(refresh=True)
            AppHelper.callAfter(service.push)
        threading.Thread(target=work, daemon=True, name="plip-probe").start()

    def run_import(source: str) -> None:
        """Worker thread: read one source, merge it, refresh Settings."""
        from mcp_vision.buddy.actions.host import default_host
        from mcp_vision.buddy.memory import importers

        def work():
            host, home = default_host(), os.path.expanduser("~")
            if source == "contacts":
                facts, error = importers.import_contacts(host)
            elif source == "mail":
                facts, error = importers.import_mail(host)
            elif source == "autofill":
                facts, error = importers.import_autofill(home)
            else:
                return
            memory.merge(source, facts, error)
            memory.save()
            AppHelper.callAfter(service.push)
        threading.Thread(target=work, daemon=True, name=f"plip-import-{source}").start()

    def test_voice(text: str) -> None:
        speaker = getattr(controller.companion, "speaker", None)
        if speaker is not None and hasattr(speaker, "speak") and type(speaker).__name__ != "_NullSpeaker":
            speaker.speak(text)
        else:
            subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def parakeet_ready() -> None:
        """The download landed: listen with Parakeet from the next press (the brain stays as it is)."""
        settings = apply_prefs(load_settings(), Prefs.load())
        state["settings"] = settings
        install_listener(settings)
        update_setup_error()
        service.push()

    from mcp_vision.buddy.parakeet import ParakeetModel

    parakeet = ParakeetModel(on_change=lambda: AppHelper.callAfter(service.push),
                             on_ready=lambda: AppHelper.callAfter(parakeet_ready))

    service = SettingsService(
        engines=lambda: registry.cards(selected=Prefs.load().engine),
        settings=lambda: state["settings"],
        reload=lambda: rebuild(probe=False),
        post=post_settings,
        platform=Platform(
            copy=_copy, open_url=_open_url, run_in_terminal=_run_in_terminal,
            request_permission=request_permission, permissions=permissions, say=test_voice,
            quit=lambda: AppKit.NSApp.terminate_(None), open_settings=open_settings, restart=restart),
        history=history,
        on_refresh=refresh_engines,
        memory=memory,
        run_import=run_import,
        action_log=ActionLog(),
        usage=usage_log,
        parakeet=parakeet,
        account=account,
        updates=updates,
        check_updates=lambda: check_updates(force=True),
        hotkey_works=lambda: hotkey_mode() != "none",
        apply_hotkey=lambda name: apply_hotkey(name),
    )

    def handle_command(command: dict[str, Any]) -> None:
        name = command.get("cmd")
        if name == "open-settings":
            open_settings(str(command.get("tab") or "home"))
        elif name == "confirm-action" and controller.companion is not None:
            future = asyncio.run_coroutine_threadsafe(
                controller.companion.answer_pending(bool(command.get("accept"))), loop)
            future.add_done_callback(lambda done: AppHelper.callAfter(record_answer, done))
        elif name == "open-path":
            path = os.path.realpath(os.path.expanduser(str(command.get("path") or "")))
            if path.startswith(os.path.realpath(os.path.expanduser("~")) + os.sep) and os.path.exists(path):
                default_host_cached().open(path)
        elif name == "stop":
            if controller.companion is not None:
                loop.call_soon_threadsafe(controller.companion.interrupt, None)
            presenter.idle()
        elif name == "ready" and island is not None:          # the island loaded: show it the talk shortcut
            island.post([{"type": "shortcut", "state": chord(Prefs.load().hotkey).card()}])
        elif name != "island-rect":
            service.handle(command)

    def record_answer(future) -> None:
        try:
            result = future.result()
        except Exception:
            return
        if result is not None and result.did:
            history.add("(confirmed)", result.spoken, engine="Plip")

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
        "report": lambda: open_settings("report"),
        "update": lambda: service.handle({"cmd": "update-download"}),
    })
    menu.set_visible_checked(prefs.buddy)
    controller.status = menu.set_status

    detector = ChordDetector(on_press=controller.on_press, on_release=controller.on_release,
                             on_cancel=controller.on_cancel, chord=chord(prefs.hotkey).mask)
    hotkeys = state["hotkeys"] = MacHotkeyListener(detector)
    controller.hotkey_mode = hotkeys.start()
    apply_hotkey(prefs.hotkey)
    _request_startup_permissions()
    rebuild(probe=True)
    threading.Thread(target=account.refresh, daemon=True, name="plip-account").start()   # renew the sign-in once
    threading.Thread(target=watch_updates, daemon=True, name="plip-updates").start()      # a newer Plip?

    if account.required or not prefs.onboarded:
        # Sign-in first, then the welcome walkthrough until it calls finish-onboarding.
        AppHelper.callLater(0.8, lambda: open_settings("home"))

    log.info("plip running (hotkey=%s, web=%s)", controller.hotkey_mode, web)
    if controller.hotkey_mode == "none":
        owner = keyboard_owner()
        print(f"Plip can't hear {controller.shortcut} yet: macOS isn't passing keystrokes to {owner}. Turn {owner} "
              "on in System Settings > Privacy & Security > Accessibility, then start Plip again.", flush=True)
    else:
        print(f"Plip is in your menu bar and notch. Hold {controller.shortcut} and ask.", flush=True)
    import atexit

    # Quitting: drop the warm brain process so nothing is left waiting for a question.
    atexit.register(lambda: getattr(getattr(controller.companion, "brain", None), "close", lambda: None)())
    AppHelper.runEventLoop(installInterrupt=True)
