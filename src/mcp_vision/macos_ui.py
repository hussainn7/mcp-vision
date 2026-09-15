"""Small native macOS contextual popup and global invocation hotkey."""
from __future__ import annotations

import sys
import threading
from typing import Any

from mcp_vision.context import Context, ContextElement, IdentityState, Point
from mcp_vision.native_context import describe_ax, nearby_ax


def _ax_copy(api: Any, element: Any, attribute: str) -> Any:
    try:
        result = api.AXUIElementCopyAttributeValue(element, attribute, None)
        if isinstance(result, tuple):
            return result[-1] if result and result[0] == 0 else None
        return result
    except Exception:
        return None


def capture_native_context() -> Context:
    """Collect what's under the cursor (not the MCP-Vision popup / IDE)."""
    if sys.platform != "darwin":
        return Context(source="macos", source_application=sys.platform)
    from AppKit import NSEvent, NSRunningApplication, NSScreen, NSWorkspace
    import ApplicationServices as AX

    cursor = NSEvent.mouseLocation()
    trusted = bool(AX.AXIsProcessTrusted())
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    context = Context(
        source="macos",
        source_application=str(front.localizedName() or "") if front else "",
        cursor_position=Point(x=float(cursor.x), y=float(cursor.y)),
        accessibility_context={"permission": "granted" if trusted else "required",
                               "bundle_id": str(front.bundleIdentifier() or "") if front else ""},
        identity=IdentityState(status="unknown"),
    )
    if not trusted:
        return context

    primary_height = float(NSScreen.screens()[0].frame().size.height)
    ax_x, ax_y = float(cursor.x), primary_height - float(cursor.y)
    system = AX.AXUIElementCreateSystemWide()
    element = None
    try:
        err, element = AX.AXUIElementCopyElementAtPosition(system, ax_x, ax_y, None)
        if err != 0:
            element = None
    except Exception:
        element = None
    if element is None:
        element = _ax_copy(AX, system, AX.kAXFocusedUIElementAttribute)

    app = front
    window = None
    pid = None
    if element is not None:
        try:
            err, pid_ref = AX.AXUIElementGetPid(element, None)
            if err == 0 and pid_ref:
                pid = int(pid_ref)
                matched = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
                if matched:
                    app = matched
        except Exception:
            pass
        window = _ax_copy(AX, element, AX.kAXWindowAttribute) or _ax_copy(AX, element, "AXTopLevelUIElement")
    if window is None and app is not None:
        focused_app = AX.AXUIElementCreateApplication(int(app.processIdentifier()))
        window = _ax_copy(AX, focused_app, AX.kAXFocusedWindowAttribute)

    # Skip our own launcher when the cursor is over the popup chrome.
    bundle = str(app.bundleIdentifier() or "") if app else ""
    if bundle == "org.mcpvision.contextual":
        focused_app = _ax_copy(AX, system, AX.kAXFocusedApplicationAttribute)
        if focused_app:
            try:
                err, pid_ref = AX.AXUIElementGetPid(focused_app, None)
                if err == 0 and pid_ref:
                    other = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid_ref))
                    if other and str(other.bundleIdentifier() or "") != "org.mcpvision.contextual":
                        app = other
                        window = _ax_copy(AX, focused_app, AX.kAXFocusedWindowAttribute)
                        element = _ax_copy(AX, system, AX.kAXFocusedUIElementAttribute) or element
                        pid = int(other.processIdentifier())
                        bundle = str(other.bundleIdentifier() or "")
            except Exception:
                pass

    name = str(app.localizedName() or "") if app else ""
    title = _ax_copy(AX, window, AX.kAXTitleAttribute) if window else ""
    role = _ax_copy(AX, element, AX.kAXRoleAttribute) if element else ""
    selected = _ax_copy(AX, element, AX.kAXSelectedTextAttribute) if element else ""
    secure = "secure" in str(role).lower() or _ax_copy(AX, element, "AXSubrole") == "AXSecureTextField"
    focused = describe_ax(AX, element) if element else None
    parent = _ax_copy(AX, element, "AXParent") if element else None
    controls = nearby_ax(AX, parent or window, 24)[0]
    return context.model_copy(update={
        "source_application": name,
        "title": str(title or ""),
        "selected_text": str(selected or "") if not secure else "",
        "focused_element": focused,
        "accessibility_context": {
            "permission": "granted", "bundle_id": bundle,
            "window": str(title or ""), "focused_role": str(role or ""),
            "pid": int(pid if pid is not None else (app.processIdentifier() if app else 0)),
            "pointer": {"x": ax_x, "y": ax_y},
            "controls": controls,
        },
    })


def run_contextual_ui(*, port: int = 7331, provider: str | None = None, live_driver: str = "native", cdp_endpoint: str | None = None) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("The contextual hotkey UI currently requires macOS.")
    import AppKit
    import objc
    from PyObjCTools import AppHelper
    from mcp_vision.studio import StudioServer

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    class Controller(AppKit.NSObject):
        def init(self):
            self = objc.super(Controller, self).init()
            if self is None:
                return None
            self.context = None
            self.server = None
            self.monitors = []
            self.task = None
            self.history = []
            self.source_path = None
            self.highlight_window = None
            self.marker_window = None
            self.highlight_generation = 0
            self._build_panel()
            return self

        @objc.python_method
        def _build_panel(self):
            width, height = 430, 400
            rect = AppKit.NSMakeRect(0, 0, width, height)
            style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskFullSizeContentView)
            self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                rect, style, AppKit.NSBackingStoreBuffered, False)
            self.panel.setTitlebarAppearsTransparent_(True)
            self.panel.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            self.panel.setMovableByWindowBackground_(True)
            self.panel.setLevel_(AppKit.NSFloatingWindowLevel)
            self.panel.setFloatingPanel_(True)
            self.panel.setHidesOnDeactivate_(False)
            self.panel.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                               AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
            self.panel.setReleasedWhenClosed_(False)
            self.panel.setOpaque_(False)
            self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
            root = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
            root.setMaterial_(AppKit.NSVisualEffectMaterialPopover)
            root.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            root.setState_(AppKit.NSVisualEffectStateActive)
            root.setWantsLayer_(True)
            root.layer().setCornerRadius_(18)
            root.layer().setMasksToBounds_(True)
            self.panel.setContentView_(root)

            title = AppKit.NSTextField.labelWithString_("What should I do here?")
            title.setFrame_(AppKit.NSMakeRect(24, 340, 360, 32))
            title.setFont_(AppKit.NSFont.systemFontOfSize_weight_(21, AppKit.NSFontWeightSemibold))
            root.addSubview_(title)
            dismiss = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(387, 344, 24, 24))
            dismiss.setTitle_("×")
            dismiss.setBordered_(False)
            dismiss.setKeyEquivalent_("\x1b")
            dismiss.setTarget_(self)
            dismiss.setAction_("dismiss:")
            root.addSubview_(dismiss)

            self.input = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(20, 288, 390, 43))
            self.input.setPlaceholderString_("Ask about what’s under your cursor…")
            self.input.setFont_(AppKit.NSFont.systemFontOfSize_(15))
            self.input.setBezeled_(True)
            self.input.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
            self.input.setTarget_(self)
            self.input.setAction_("submit:")
            root.addSubview_(self.input)

            self.modes = AppKit.NSSegmentedControl.alloc().initWithFrame_(AppKit.NSMakeRect(20, 248, 248, 28))
            self.modes.setSegmentCount_(4)
            for index, label in enumerate(("Auto", "Ask", "Guide", "Act")):
                self.modes.setLabel_forSegment_(label, index)
                self.modes.setWidth_forSegment_(62, index)
            self.modes.setSelectedSegment_(0)
            self.modes.setEnabled_(True)
            root.addSubview_(self.modes)

            self.providers = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
                AppKit.NSMakeRect(276, 246, 134, 28), False)
            from mcp_vision.providers import LABELS
            for label, _value in LABELS:
                self.providers.addItemWithTitle_(label)
            self.providers.selectItemAtIndex_(0)
            root.addSubview_(self.providers)

            send = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(346, 206, 64, 31))
            send.setTitle_("Go")
            self.send = send
            send.setBezelStyle_(AppKit.NSBezelStyleRounded)
            send.setKeyEquivalent_("\r")
            send.setTarget_(self)
            send.setAction_("submit:")
            root.addSubview_(send)

            cancel = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(276, 206, 70, 31))
            cancel.setTitle_("Cancel")
            cancel.setBezelStyle_(AppKit.NSBezelStyleRounded)
            cancel.setTarget_(self)
            cancel.setAction_("cancel:")
            root.addSubview_(cancel)
            self.cancel_button = cancel
            cancel.setEnabled_(False)

            choose = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(20, 206, 150, 28))
            choose.setTitle_("Choose résumé…")
            choose.setBezelStyle_(AppKit.NSBezelStyleRounded)
            choose.setTarget_(self)
            choose.setAction_("chooseSource:")
            root.addSubview_(choose)
            self.choose_button = choose

            scroll = AppKit.NSScrollView.alloc().initWithFrame_(AppKit.NSMakeRect(24, 44, 382, 150))
            scroll.setHasVerticalScroller_(True)
            scroll.setDrawsBackground_(False)
            self.response = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 362, 150))
            self.response.setEditable_(False)
            self.response.setSelectable_(True)
            self.response.setDrawsBackground_(False)
            self.response.setVerticallyResizable_(True)
            self.response.setAutoresizingMask_(AppKit.NSViewWidthSizable)
            self.response.textContainer().setWidthTracksTextView_(True)
            scroll.setDocumentView_(self.response)
            root.addSubview_(scroll)
            self.response.setFont_(AppKit.NSFont.systemFontOfSize_(13.5))
            self.response.setTextColor_(AppKit.NSColor.secondaryLabelColor())

            self.status = AppKit.NSTextField.labelWithString_("Local · ⌥Space or ⌃⌥Space over the target")
            self.status.setFrame_(AppKit.NSMakeRect(24, 16, 380, 20))
            self.status.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            self.status.setTextColor_(AppKit.NSColor.tertiaryLabelColor())
            root.addSubview_(self.status)

        @objc.python_method
        def install_hotkey(self):
            mask = AppKit.NSEventMaskKeyDown
            option = AppKit.NSEventModifierFlagOption
            control = AppKit.NSEventModifierFlagControl

            def invoke():
                context = capture_native_context()
                AppHelper.callAfter(self.show_context, context)

            def matches_primary(event):
                flags = int(event.modifierFlags())
                return (event.keyCode() == 49 and bool(flags & option)
                        and not bool(flags & (AppKit.NSEventModifierFlagCommand | control)))

            def matches_fallback(event):
                flags = int(event.modifierFlags())
                return (event.keyCode() == 49 and bool(flags & option) and bool(flags & control)
                        and not bool(flags & AppKit.NSEventModifierFlagCommand))

            def local(event):
                if matches_primary(event) or matches_fallback(event):
                    invoke()
                    return None
                return event

            self.monitors.append(AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, lambda event: invoke() if matches_primary(event) or matches_fallback(event) else None))
            self.monitors.append(AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local))
            center = AppKit.NSDistributedNotificationCenter.defaultCenter()
            center.addObserver_selector_name_object_(
                self, "nativeHotkey:", "org.mcpvision.contextual.hotkey", None)
            self._menu_invoke = invoke
            self._install_status_item(invoke)
            self._ensure_accessibility()

        def nativeHotkey_(self, _notification):
            if getattr(self, "_menu_invoke", None):
                self._menu_invoke()

        @objc.python_method
        def _install_status_item(self, invoke):
            bar = AppKit.NSStatusBar.systemStatusBar()
            self.status_item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
            button = self.status_item.button()
            if button is not None:
                button.setTitle_("MV")
                button.setToolTip_("MCP-Vision · click or ⌥Space / ⌃⌥Space")
            menu = AppKit.NSMenu.alloc().init()
            open_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Ask about this…", "invokeFromMenu:", "")
            open_item.setTarget_(self)
            menu.addItem_(open_item)
            quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit MCP-Vision", "quitFromMenu:", "")
            quit_item.setTarget_(self)
            menu.addItem_(quit_item)
            self.status_item.setMenu_(menu)
            self._menu_invoke = invoke

        def invokeFromMenu_(self, _sender):
            if getattr(self, "_menu_invoke", None):
                self._menu_invoke()

        def quitFromMenu_(self, _sender):
            AppKit.NSApp.terminate_(None)

        @objc.python_method
        def _ensure_accessibility(self):
            import ApplicationServices as AX
            from Foundation import NSDictionary, NSNumber
            options = NSDictionary.dictionaryWithObject_forKey_(
                NSNumber.numberWithBool_(True), AX.kAXTrustedCheckOptionPrompt)
            trusted = bool(AX.AXIsProcessTrustedWithOptions(options))
            if trusted:
                self.status.setStringValue_("Ready · ⌥Space or ⌃⌥Space · or click MV in the menu bar")
            else:
                self.status.setStringValue_("Enable Accessibility for MCP-Vision, then use ⌥Space or menu bar MV")
                AppKit.NSWorkspace.sharedWorkspace().openURL_(
                    AppKit.NSURL.URLWithString_(
                        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"))
            return trusted

        @objc.python_method
        def show_context(self, context):
            if self.server and self.server.get_context(context.context_id) is None:
                self.server.accept_context(context)
            if self.task:
                self.task.cancel()
                self.response.setString_("Cancelling the current task. Invoke again once it stops.")
                return
            self.context = context
            self.history = []
            self.source_path = None
            self.choose_button.setTitle_("Choose résumé…")
            self.modes.setSelectedSegment_(0)
            self.hide_indicator()
            permission = context.accessibility_context.get("permission", "unknown")
            where = context.source_application or context.title or "screen"
            suffix = "Accessibility ready" if permission == "granted" else "Accessibility permission needed"
            self.status.setStringValue_(f"{where} · {suffix} · ⌥Space over the target")
            self.input.setStringValue_("")
            self.response.setString_("")
            mouse = AppKit.NSEvent.mouseLocation()
            screen = next((s for s in AppKit.NSScreen.screens() if AppKit.NSPointInRect(mouse, s.frame())),
                          AppKit.NSScreen.mainScreen())
            frame = screen.visibleFrame()
            x = min(max(mouse.x + 12, frame.origin.x + 8), frame.origin.x + frame.size.width - 438)
            y = mouse.y - 412
            if y < frame.origin.y + 8:
                y = min(mouse.y + 14, frame.origin.y + frame.size.height - 408)
            self.panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))
            self.panel.makeKeyAndOrderFront_(None)
            self.panel.orderFrontRegardless()
            app.activateIgnoringOtherApps_(True)
            self.panel.makeFirstResponder_(self.input)

        def chooseSource_(self, _sender):
            panel = AppKit.NSOpenPanel.openPanel()
            panel.setCanChooseDirectories_(False)
            panel.setAllowsMultipleSelection_(False)
            panel.setAllowedFileTypes_(["txt", "md", "pdf", "docx"])
            panel.setMessage_("Choose a résumé to read and attach to the selected form. Other files remain private.")
            if panel.runModal() == AppKit.NSModalResponseOK:
                self.source_path = str(panel.URL().path())
                self.choose_button.setTitle_("Résumé selected ✓")

        def dismiss_(self, _sender):
            self.cancel_(None)
            self.panel.orderOut_(None)

        def cancel_(self, _sender):
            if self.task:
                self.task.cancel()
                self.status.setStringValue_("Cancelling · finishing any in-flight operation…")
            self.hide_indicator()

        @objc.python_method
        def hide_indicator(self):
            self.highlight_generation += 1
            if self.highlight_window:
                self.highlight_window.orderOut_(None)
                self.highlight_window = None
            if self.marker_window:
                self.marker_window.orderOut_(None)
                self.marker_window = None

        @objc.python_method
        def indicator(self, resolver, label, duration):
            AppHelper.callAfter(self.show_indicator, resolver, label, duration)

        @objc.python_method
        def show_indicator(self, resolver, label, duration):
            import time
            self.hide_indicator()
            if resolver is None:
                return
            generation = self.highlight_generation
            teal = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(.18, .73, .63, 1)
            window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(0, 0, 20, 20), AppKit.NSWindowStyleMaskBorderless,
                AppKit.NSBackingStoreBuffered, False)
            window.setReleasedWhenClosed_(False)
            window.setOpaque_(False)
            window.setBackgroundColor_(AppKit.NSColor.clearColor())
            window.setIgnoresMouseEvents_(True)
            window.setLevel_(AppKit.NSStatusWindowLevel)
            view = window.contentView()
            view.setWantsLayer_(True)
            view.layer().setBorderWidth_(3)
            view.layer().setCornerRadius_(7)
            view.layer().setBorderColor_(teal.CGColor())
            marker = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(0, 0, 14, 14), AppKit.NSWindowStyleMaskBorderless,
                AppKit.NSBackingStoreBuffered, False)
            marker.setReleasedWhenClosed_(False)
            marker.setOpaque_(False)
            marker.setBackgroundColor_(AppKit.NSColor.clearColor())
            marker.setIgnoresMouseEvents_(True)
            marker.setLevel_(AppKit.NSStatusWindowLevel + 1)
            mview = marker.contentView()
            mview.setWantsLayer_(True)
            mview.layer().setBackgroundColor_(teal.CGColor())
            mview.layer().setCornerRadius_(7)
            mview.layer().setBorderWidth_(2)
            mview.layer().setBorderColor_(AppKit.NSColor.whiteColor().CGColor())
            self.highlight_window = window
            self.marker_window = marker
            deadline = time.monotonic() + duration / 1000
            def track():
                if generation != self.highlight_generation:
                    return
                box = resolver()
                if not box or time.monotonic() >= deadline:
                    self.hide_indicator()
                    return
                primary_height = AppKit.NSScreen.screens()[0].frame().size.height
                window.setFrame_display_(AppKit.NSMakeRect(box.x-3, primary_height-box.y-box.height-3,
                                                          box.width+6, box.height+6), True)
                marker.setFrame_display_(AppKit.NSMakeRect(box.x + box.width/2 - 7,
                                                          primary_height - box.y - box.height/2 - 7, 14, 14), True)
                window.orderFrontRegardless()
                marker.orderFrontRegardless()
                AppHelper.callLater(.12, track)
            track()

        def submit_(self, _sender):
            import asyncio
            from mcp_vision.tasks import ContextTask
            from mcp_vision.execution import bind_context_backend
            from mcp_vision.providers import LABELS, resolve_provider
            request = str(self.input.stringValue()).strip()
            if not request or self.task:
                return
            # Chrome relay contexts stay; otherwise refresh what's under the cursor.
            if self.context and self.context.source == "chrome" and self.context.url:
                context = self.context
            else:
                context = capture_native_context()
            context = context.model_copy(update={"user_request": request})
            mode = (None, "ask", "guide", "act")[self.modes.selectedSegment()]
            choice = LABELS[max(0, self.providers.indexOfSelectedItem())][1]
            selected_provider = resolve_provider(provider or choice)
            task = ContextTask(context, mode=mode, provider=selected_provider, source_path=self.source_path,
                               history=self.history, progress=lambda message: AppHelper.callAfter(self.show_progress, message))
            self.task = task
            self.response.setString_("Reading current context…")
            self.input.setEnabled_(False)
            self.send.setEnabled_(False)
            self.choose_button.setEnabled_(False)
            self.modes.setEnabled_(False)
            self.providers.setEnabled_(False)
            self.cancel_button.setEnabled_(True)

            async def run():
                try:
                    if task.mode != "ask":
                        task.check_cancel()
                        task.backend = await bind_context_backend(context, mode=task.mode, live_driver=live_driver,
                                                                  cdp_endpoint=cdp_endpoint, indicator=self.indicator, source_path=self.source_path)
                    return await task.run()
                except asyncio.CancelledError:
                    return task.result("cancelled", "Cancelled. No further actions will run.")
                except Exception as exc:
                    from mcp_vision.redaction import redact
                    return task.result("error", redact(str(exc)))
                finally:
                    if task.backend and hasattr(task.backend, "close"):
                        await task.backend.close()
            def work():
                result = asyncio.run(run())
                AppHelper.callAfter(self.show_answer, result)
            threading.Thread(target=work, daemon=True).start()

        @objc.python_method
        def show_progress(self, message):
            if self.task and not self.task.cancelled.is_set():
                self.status.setStringValue_(message)

        @objc.python_method
        def show_answer(self, result):
            if self.task and result.get("capability") == "ask":
                self.history.extend([{"role": "user", "content": self.task.context.user_request},
                                     {"role": "assistant", "content": result["answer"]}])
                self.history = self.history[-6:]
            self.task = None
            self.input.setEnabled_(True)
            self.send.setEnabled_(True)
            self.choose_button.setEnabled_(True)
            self.modes.setEnabled_(True)
            self.providers.setEnabled_(True)
            self.cancel_button.setEnabled_(False)
            self.response.setString_(result["answer"])
            where = (self.context.source_application if self.context else "") or "Ready"
            self.status.setStringValue_(
                f"{result.get('state', 'ready').capitalize()} · {result.get('capability', 'ask').capitalize()}"
                f" · {result.get('provider', 'local')} · {where}")
            self.input.setStringValue_("")

    controller = Controller.alloc().init()
    server = StudioServer(port, invocation_handler=lambda context: AppHelper.callAfter(controller.show_context, context),
                          provider=provider)
    controller.server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller.install_hotkey()
    app.finishLaunching()
    print(f"MCP-Vision UI ready · menu bar MV · ⌥Space / ⌃⌥Space · http://127.0.0.1:{server.server_port}", flush=True)
    try:
        AppHelper.runEventLoop()
    finally:
        server.shutdown()
        server.server_close()
