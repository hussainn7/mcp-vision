"""Small native macOS contextual popup and global invocation hotkey."""
from __future__ import annotations

import sys
import os
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


def _ax_trusted(api) -> bool:
    if bool(api.AXIsProcessTrusted()):
        return True
    # After a fresh grant, the flag can lag; a successful system-wide read means we are trusted.
    try:
        system = api.AXUIElementCreateSystemWide()
        focused = _ax_copy(api, system, api.kAXFocusedApplicationAttribute)
        return focused is not None
    except Exception:
        return False


def capture_native_context() -> Context:
    """Collect what's under the cursor (not the MCP-Vision popup / IDE)."""
    if sys.platform != "darwin":
        return Context(source="macos", source_application=sys.platform)
    from AppKit import NSEvent, NSRunningApplication, NSScreen, NSWorkspace
    import ApplicationServices as AX

    cursor = NSEvent.mouseLocation()
    trusted = _ax_trusted(AX)
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    context = Context(
        source="macos",
        source_application=str(front.localizedName() or "") if front else "",
        cursor_position=Point(x=float(cursor.x), y=float(cursor.y)),
        accessibility_context={"permission": "granted" if trusted else "required",
                               "bundle_id": str(front.bundleIdentifier() or "") if front else "",
                               "app_path": "/Applications/MCP-Vision.app"},
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
    if bundle == "org.mcpvision.contextual" or (app and int(app.processIdentifier()) == os.getpid()):
        focused_app = _ax_copy(AX, system, AX.kAXFocusedApplicationAttribute)
        if focused_app:
            try:
                err, pid_ref = AX.AXUIElementGetPid(focused_app, None)
                if err == 0 and pid_ref:
                    other = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid_ref))
                    if other and int(other.processIdentifier()) != os.getpid() and str(other.bundleIdentifier() or "") != "org.mcpvision.contextual":
                        app = other
                        window = _ax_copy(AX, focused_app, AX.kAXFocusedWindowAttribute)
                        element = _ax_copy(AX, system, AX.kAXFocusedUIElementAttribute) or element
                        pid = int(other.processIdentifier())
                        bundle = str(other.bundleIdentifier() or "")
            except Exception:
                pass

    if bundle == "org.mcpvision.contextual" or (app and int(app.processIdentifier()) == os.getpid()):
        # Never turn our own request field/answer into task evidence or an action target.
        return Context(source="macos", accessibility_context={"permission": "granted"})

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


def submission_context(captured: Context | None, request: str, pending_request: str | None = None) -> Context:
    """Go must never re-capture the foreground popup as task context."""
    context = captured or Context(source="macos")
    if pending_request:
        import re
        from mcp_vision.request_routing import route_request
        if route_request(pending_request, 'ask').missing == 'departure' and not re.search(r'\bfrom\b', request, re.I):
            request = 'from ' + request
        request = pending_request + "\nAdditional details: " + request
    return context.model_copy(update={"user_request": request})


def _edit_menu():
    """Standard Edit items so Cmd+C/Cmd+V and right-click work in the popup.

    The popup is a borderless accessory app with no menu bar, so without an
    Edit menu the field editor never gets copy/paste/select-all key equivalents.
    """
    import AppKit
    menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (
        ("Select All", "selectAll:", "a"),
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
    ):
        item = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
        item.setKeyEquivalentModifierMask_(AppKit.NSEventModifierFlagCommand)
        # Nill target routes each action up the responder chain to the field editor.
    return menu


def run_contextual_ui(*, port: int = 7331, provider: str | None = None, live_driver: str = "native", cdp_endpoint: str | None = None) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("The contextual hotkey UI currently requires macOS.")
    import AppKit
    import objc
    from PyObjCTools import AppHelper
    from mcp_vision.studio import StudioServer

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    app.setMainMenu_(_edit_menu())

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
            self.pending_request = None
            self.source_path = None
            self.highlight_window = None
            self.marker_window = None
            self.highlight_generation = 0
            self._build_panel()
            return self

        @objc.python_method
        def _build_panel(self):
            width, height = 580, 610
            rect = AppKit.NSMakeRect(0, 0, width, height)
            style = AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskFullSizeContentView
            self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                rect, style, AppKit.NSBackingStoreBuffered, False)
            self.panel.setTitle_("MCP-Vision")
            self.panel.setTitlebarAppearsTransparent_(True)
            self.panel.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            self.panel.setMovableByWindowBackground_(True)
            self.panel.setLevel_(AppKit.NSFloatingWindowLevel)
            self.panel.setFloatingPanel_(True)
            self.panel.setHidesOnDeactivate_(False)
            self.panel.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                               AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
            self.panel.setReleasedWhenClosed_(False)
            self.panel.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
            self.panel.setOpaque_(False)
            self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
            root = AppKit.NSView.alloc().initWithFrame_(rect)
            root.setWantsLayer_(True)
            root.layer().setBackgroundColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(.08, .10, .14, 1).CGColor())
            root.layer().setCornerRadius_(20)
            root.layer().setMasksToBounds_(True)
            self.panel.setContentView_(root)

            def label(text, frame, size=13, bold=False):
                view = AppKit.NSTextField.labelWithString_(text)
                view.setFrame_(AppKit.NSMakeRect(*frame))
                view.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, AppKit.NSFontWeightSemibold if bold else AppKit.NSFontWeightRegular))
                view.setTextColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(.91, .94, .98, 1))
                root.addSubview_(view)
                return view

            label("MCP-Vision", (28, 550, 440, 38), 28, True)
            self.context_label = label("Ask a question. Find something. Get it done.", (30, 524, 510, 22))
            self.context_label.setTextColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(.64, .72, .84, 1))
            dismiss = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(530, 559, 26, 26))
            dismiss.setTitle_("×")
            dismiss.setBordered_(False)
            dismiss.setKeyEquivalent_("\x1b")
            dismiss.setTarget_(self)
            dismiss.setAction_("dismiss:")
            root.addSubview_(dismiss)

            self.input = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(28, 463, 524, 46))
            self.input.setPlaceholderString_("What would you like me to do?")
            self.input.setFont_(AppKit.NSFont.systemFontOfSize_(16))
            self.input.setBezeled_(True)
            self.input.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
            self.input.setFocusRingType_(AppKit.NSFocusRingTypeNone)
            self.input.setMenu_(_edit_menu())
            self.input.setTarget_(self)
            self.input.setAction_("submit:")
            root.addSubview_(self.input)

            self.modes = AppKit.NSSegmentedControl.alloc().initWithFrame_(AppKit.NSMakeRect(28, 415, 328, 30))
            self.modes.setSegmentCount_(4)
            for index, text in enumerate(("Auto", "Ask", "Guide", "Act")):
                self.modes.setLabel_forSegment_(text, index)
                self.modes.setWidth_forSegment_(82, index)
            self.modes.setSelectedSegment_(0)
            self.modes.setTarget_(self)
            self.modes.setAction_("modeChanged:")
            root.addSubview_(self.modes)

            self.providers = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
                AppKit.NSMakeRect(390, 414, 162, 30), False)
            from mcp_vision.providers import LABELS
            for text, _value in LABELS:
                self.providers.addItemWithTitle_(text)
            self.providers.selectItemAtIndex_(0)
            self.providers.setToolTip_("Model provider · Auto uses a configured provider")
            root.addSubview_(self.providers)
            self.mode_hint = label("Auto chooses whether to answer, guide, or take action.", (30, 386, 520, 20), 12)
            self.mode_hint.setTextColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(.64, .72, .84, 1))

            def button(text, frame, action):
                view = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(*frame))
                view.setTitle_(text)
                view.setBezelStyle_(AppKit.NSBezelStyleRounded)
                view.setTarget_(self)
                view.setAction_(action)
                root.addSubview_(view)
                return view

            self.send = button("Run", (444, 338, 108, 34), "submit:")
            self.send.setKeyEquivalent_("\r")
            self.send.setContentTintColor_(AppKit.NSColor.systemTealColor())
            self.cancel_button = button("Cancel", (344, 338, 96, 34), "cancel:")
            self.cancel_button.setEnabled_(False)
            self.cancel_button.setHidden_(True)
            self.choose_button = button("Attach résumé…", (24, 338, 164, 34), "chooseSource:")
            self.choose_button.setToolTip_("Optional · attach a résumé for factual form filling")
            self.result_heading = label("Ready when you are", (30, 302, 520, 22), 14, True)
            self.result_heading.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)

            scroll = AppKit.NSScrollView.alloc().initWithFrame_(AppKit.NSMakeRect(28, 66, 524, 225))
            scroll.setHasVerticalScroller_(True)
            scroll.setDrawsBackground_(False)
            self.response = AppKit.NSTextView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 504, 225))
            self.response.setEditable_(False)
            self.response.setSelectable_(True)
            self.response.setDrawsBackground_(False)
            self.response.setVerticallyResizable_(True)
            self.response.setAutoresizingMask_(AppKit.NSViewWidthSizable)
            self.response.textContainer().setWidthTracksTextView_(True)
            self.response.setTextContainerInset_(AppKit.NSMakeSize(2, 6))
            scroll.setDocumentView_(self.response)
            root.addSubview_(scroll)
            self.response.setFont_(AppKit.NSFont.systemFontOfSize_(15))
            self.response.setTextColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(.91, .94, .98, 1))
            self.response.setMenu_(_edit_menu())
            self.status = label("Desktop preview · 0.3.1", (30, 28, 318, 20), 11)
            self.copy_button = button("Copy", (350, 22, 74, 30), "copyAnswer:")
            self.source_button = button("Open source", (428, 22, 126, 30), "openSource:")
            self.copy_button.setEnabled_(False)
            self.source_button.setHidden_(True)
            self.result_url = ''

            self._ensure_accessibility()

        @objc.python_method
        def install_hotkey(self):
            """Wire ⌥Space / ⌃⌥Space plus the menu-bar and native hotkey paths.

            The packaged macOS launcher registers a Carbon global hotkey and posts
            org.mcpvision.contextual.hotkey; this also installs AppKit key monitors
            so the same shortcuts work when started via `mcp-vision ui`.
            """
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
                # Borderless accessory apps often never dispatch ⌘C/⌘V key
                # equivalents from the menu bar, so send the edit actions
                # directly to whichever control is first responder.
                flags = int(event.modifierFlags())
                cmd = AppKit.NSEventModifierFlagCommand
                if (bool(flags & cmd) and not bool(flags & (option | control))
                        and self.panel and self.panel.isVisible()):
                    action = {8: "copy:", 7: "cut:", 9: "paste:", 0: "selectAll:"}.get(event.keyCode())
                    if action:
                        AppKit.NSApp.sendAction_to_from_(action, None, None)
                        return None
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
            trusted = bool(AX.AXIsProcessTrustedWithOptions(options)) or _ax_trusted(AX)
            if trusted:
                self.status.setStringValue_("Ready · ⌥Space / ⌃⌥Space · or menu bar MV")
            else:
                self.status.setStringValue_("Enable /Applications/MCP-Vision.app in Accessibility")
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
            self.pending_request = None
            self.source_path = None
            self.choose_button.setTitle_("Attach résumé…")
            self.modes.setSelectedSegment_(0)
            self.hide_indicator()
            permission = context.accessibility_context.get("permission", "unknown")
            where = context.source_application or context.title or "screen"
            if permission == "granted":
                suffix = "Accessibility ready"
            else:
                suffix = "Add /Applications/MCP-Vision.app in Accessibility, then toggle it on"
            self.context_label.setStringValue_(f"Working with {where}" if context.source_application else "Ask a question. Find something. Get it done.")
            self.status.setStringValue_("Ready · Desktop preview 0.3.1")
            self.input.setStringValue_("")
            self.result_heading.setStringValue_("Ready when you are")
            self.response.setString_("Try a request in your own words:\n\nFind flights to San Francisco next week\nResearch a topic on the web\nExplain what’s on this screen\nFill this form using my résumé")
            self.input.setPlaceholderString_("What would you like me to do?")
            self.send.setTitle_("Run")
            self.source_button.setHidden_(True)
            self.copy_button.setEnabled_(False)
            self.modeChanged_(None)
            mouse = AppKit.NSEvent.mouseLocation()
            screen = next((s for s in AppKit.NSScreen.screens() if AppKit.NSPointInRect(mouse, s.frame())),
                          AppKit.NSScreen.mainScreen())
            frame = screen.visibleFrame()
            x = min(max(mouse.x + 12, frame.origin.x + 8), frame.origin.x + frame.size.width - 588)
            y = mouse.y - 622
            if y < frame.origin.y + 8:
                y = min(mouse.y + 14, frame.origin.y + frame.size.height - 618)
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
            # Go is inside the popup. Preserve the target captured BEFORE it opened.
            context = submission_context(self.context, request, self.pending_request)
            mode = (None, "ask", "guide", "act")[self.modes.selectedSegment()]
            choice = LABELS[max(0, self.providers.indexOfSelectedItem())][1]
            selected_provider = resolve_provider(provider or choice)
            task = ContextTask(context, mode=mode, provider=selected_provider, source_path=self.source_path,
                               history=self.history, progress=lambda message: AppHelper.callAfter(self.show_progress, message))
            self.task = task
            self.result_heading.setStringValue_(request)
            self.source_button.setHidden_(True)
            self.copy_button.setEnabled_(False)
            auto = self.modes.selectedSegment() == 0
            label = {"ask": "Asking", "guide": "Guiding", "act": "Acting"}.get(task.mode, "Working")
            self.response.setString_(f"{'Auto → ' if auto else ''}{label}…")
            self.status.setStringValue_(f"{'Auto → ' if auto else ''}{task.mode.capitalize()} · reading context…")
            self.input.setEnabled_(False)
            self.send.setEnabled_(False)
            self.choose_button.setEnabled_(False)
            self.modes.setEnabled_(False)
            self.providers.setEnabled_(False)
            self.cancel_button.setEnabled_(True)
            self.cancel_button.setHidden_(False)

            async def run():
                try:
                    if task.route.kind == "surface":
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
            self.pending_request = (self.task.context.user_request if self.task and result.get("state") == "input"
                                    and self.task.route.missing else None)
            self.task = None
            self.input.setEnabled_(True)
            self.send.setEnabled_(True)
            self.choose_button.setEnabled_(True)
            self.modes.setEnabled_(True)
            self.providers.setEnabled_(True)
            self.cancel_button.setEnabled_(False)
            self.cancel_button.setHidden_(True)
            self.response.setString_(result["answer"])
            self.response.scrollRangeToVisible_(AppKit.NSMakeRange(0, 0))
            self.copy_button.setEnabled_(True)
            from urllib.parse import urlsplit
            self.result_url = result.get('url', '')
            self.source_button.setHidden_(urlsplit(self.result_url).scheme not in {'http', 'https'})
            self.send.setTitle_("Continue" if self.pending_request else "Run")
            self.input.setPlaceholderString_("Add the missing details…" if self.pending_request else "Ask another question or start a task…")
            where = (self.context.source_application if self.context else "") or "Ready"
            self.status.setStringValue_(
                f"{ {'answered': 'Answer ready', 'input': 'Needs your input', 'review': 'Ready for review', 'guided': 'Guidance ready', 'error': 'Could not complete'}.get(result.get('state'), result.get('state', 'Ready').capitalize())} · {result.get('capability', 'ask').capitalize()}"
                f" · {result.get('provider', 'local').capitalize()}")
            self.input.setStringValue_("")
            self.panel.makeFirstResponder_(self.input)

        def modeChanged_(self, _sender):
            hints = ("Auto chooses whether to answer, guide, or take action.",
                     "Ask answers questions and can research the web.",
                     "Guide shows you the next control without changing it.",
                     "Act performs supported steps and verifies the result.")
            self.mode_hint.setStringValue_(hints[self.modes.selectedSegment()])

        def copyAnswer_(self, _sender):
            board = AppKit.NSPasteboard.generalPasteboard()
            board.clearContents()
            board.setString_forType_(str(self.response.string()), AppKit.NSPasteboardTypeString)
            self.status.setStringValue_("Answer copied")

        def openSource_(self, _sender):
            from urllib.parse import urlsplit
            if urlsplit(self.result_url).scheme in {'http', 'https'}:
                AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(self.result_url))

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
