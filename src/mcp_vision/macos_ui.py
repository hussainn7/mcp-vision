"""Small native macOS contextual popup and global invocation hotkey."""
from __future__ import annotations

import sys
import threading
from typing import Any

from mcp_vision.context import Context, ContextElement, IdentityState, Point


def _ax_copy(api: Any, element: Any, attribute: str) -> Any:
    try:
        result = api.AXUIElementCopyAttributeValue(element, attribute, None)
        if isinstance(result, tuple):
            return result[-1] if result and result[0] == 0 else None
        return result
    except Exception:
        return None


def capture_native_context() -> Context:
    """Collect only reliable foreground AX metadata; missing permission is explicit."""
    if sys.platform != "darwin":
        return Context(source="macos", source_application=sys.platform)
    from AppKit import NSEvent, NSWorkspace
    import ApplicationServices as AX

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    name = str(app.localizedName() or "") if app else ""
    bundle = str(app.bundleIdentifier() or "") if app else ""
    cursor = NSEvent.mouseLocation()
    trusted = bool(AX.AXIsProcessTrusted())
    context = Context(
        source="macos",
        source_application=name,
        cursor_position=Point(x=float(cursor.x), y=float(cursor.y)),
        accessibility_context={"permission": "granted" if trusted else "required", "bundle_id": bundle},
        identity=IdentityState(status="unknown"),
    )
    if not trusted:
        return context
    system = AX.AXUIElementCreateSystemWide()
    focused_app = _ax_copy(AX, system, AX.kAXFocusedApplicationAttribute)
    window = _ax_copy(AX, focused_app, AX.kAXFocusedWindowAttribute) if focused_app else None
    element = _ax_copy(AX, system, AX.kAXFocusedUIElementAttribute)
    title = _ax_copy(AX, window, AX.kAXTitleAttribute) if window else ""
    role = _ax_copy(AX, element, AX.kAXRoleAttribute) if element else ""
    element_title = _ax_copy(AX, element, AX.kAXTitleAttribute) if element else ""
    description = _ax_copy(AX, element, AX.kAXDescriptionAttribute) if element else ""
    selected = _ax_copy(AX, element, AX.kAXSelectedTextAttribute) if element else ""
    value = _ax_copy(AX, element, AX.kAXValueAttribute) if element else ""
    safe_value = value if isinstance(value, str) and "secure" not in str(role).lower() else ""
    focused = None
    if element:
        focused = ContextElement(role=str(role or ""), name=str(element_title or description or ""),
                                 value=str(safe_value or ""))
    return context.model_copy(update={
        "title": str(title or ""),
        "selected_text": str(selected or ""),
        "focused_element": focused,
        "accessibility_context": {
            "permission": "granted", "bundle_id": bundle,
            "window": str(title or ""), "focused_role": str(role or ""),
        },
    })


def run_contextual_ui(*, port: int = 7331, provider: str | None = None) -> None:
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
            self._build_panel()
            return self

        def _build_panel(self):
            width, height = 430, 286
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
            title.setFrame_(AppKit.NSMakeRect(24, 226, 360, 32))
            title.setFont_(AppKit.NSFont.systemFontOfSize_weight_(21, AppKit.NSFontWeightSemibold))
            root.addSubview_(title)

            self.input = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(20, 174, 390, 43))
            self.input.setPlaceholderString_("Ask about what’s under your cursor…")
            self.input.setFont_(AppKit.NSFont.systemFontOfSize_(15))
            self.input.setBezeled_(True)
            self.input.setBezelStyle_(AppKit.NSTextFieldRoundedBezel)
            self.input.setTarget_(self)
            self.input.setAction_("submit:")
            root.addSubview_(self.input)

            self.modes = AppKit.NSSegmentedControl.alloc().initWithFrame_(AppKit.NSMakeRect(20, 134, 192, 28))
            self.modes.setSegmentCount_(3)
            for index, label in enumerate(("Ask", "Guide", "Act")):
                self.modes.setLabel_forSegment_(label, index)
                self.modes.setWidth_forSegment_(62, index)
            self.modes.setSelectedSegment_(0)
            self.modes.setEnabled_(False)
            root.addSubview_(self.modes)

            send = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(356, 132, 54, 31))
            send.setTitle_("Ask")
            send.setBezelStyle_(AppKit.NSBezelStyleRounded)
            send.setKeyEquivalent_("\r")
            send.setTarget_(self)
            send.setAction_("submit:")
            root.addSubview_(send)

            self.response = AppKit.NSTextField.wrappingLabelWithString_("")
            self.response.setFrame_(AppKit.NSMakeRect(24, 48, 382, 76))
            self.response.setFont_(AppKit.NSFont.systemFontOfSize_(13.5))
            self.response.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            root.addSubview_(self.response)

            self.status = AppKit.NSTextField.labelWithString_("Local runtime · ⌥Space")
            self.status.setFrame_(AppKit.NSMakeRect(24, 16, 360, 20))
            self.status.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            self.status.setTextColor_(AppKit.NSColor.tertiaryLabelColor())
            root.addSubview_(self.status)

        def install_hotkey(self):
            mask = AppKit.NSEventMaskKeyDown
            option = AppKit.NSEventModifierFlagOption
            disallowed = AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagControl

            def matches(event):
                flags = event.modifierFlags()
                return event.keyCode() == 49 and bool(flags & option) and not bool(flags & disallowed)

            def invoke(_event):
                AppHelper.callAfter(self.show_context, capture_native_context())

            def local(event):
                if matches(event):
                    invoke(event)
                    return None
                return event

            self.monitors.append(AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, lambda event: invoke(event) if matches(event) else None))
            self.monitors.append(AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local))

        def show_context(self, context):
            if self.server and self.server.get_context(context.context_id) is None:
                self.server.accept_context(context)
            self.context = context
            permission = context.accessibility_context.get("permission", "unknown")
            suffix = "Accessibility ready" if permission == "granted" else "Accessibility permission needed"
            self.status.setStringValue_(f"Local runtime · {suffix} · ⌥Space")
            self.input.setStringValue_("")
            self.response.setStringValue_("")
            mouse = AppKit.NSEvent.mouseLocation()
            screen = next((s for s in AppKit.NSScreen.screens() if AppKit.NSPointInRect(mouse, s.frame())),
                          AppKit.NSScreen.mainScreen())
            frame = screen.visibleFrame()
            x = min(max(mouse.x + 12, frame.origin.x + 8), frame.origin.x + frame.size.width - 438)
            y = mouse.y - 298
            if y < frame.origin.y + 8:
                y = min(mouse.y + 14, frame.origin.y + frame.size.height - 294)
            self.panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))
            self.panel.makeKeyAndOrderFront_(None)
            self.panel.orderFrontRegardless()
            app.activateIgnoringOtherApps_(True)
            self.panel.makeFirstResponder_(self.input)

        def submit_(self, _sender):
            request = str(self.input.stringValue()).strip()
            if not request:
                return
            context = self.context or capture_native_context()
            context = context.model_copy(update={"user_request": request})
            self.response.setStringValue_("Thinking with the nearby context…")
            self.input.setEnabled_(False)

            def work():
                if self.server:
                    result = self.server.answer(request, context.context_id)
                else:
                    from mcp_vision.contextual import answer_context
                    result = answer_context(context, provider=provider)
                AppHelper.callAfter(self.show_answer, result)
            threading.Thread(target=work, daemon=True).start()

        def show_answer(self, result):
            self.input.setEnabled_(True)
            self.response.setStringValue_(result["answer"])
            index = {"ask": 0, "guide": 1, "act": 2}.get(result.get("capability"), 0)
            self.modes.setSelectedSegment_(index)

    controller = Controller.alloc().init()
    server = StudioServer(port, invocation_handler=lambda context: AppHelper.callAfter(controller.show_context, context),
                          provider=provider)
    controller.server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller.install_hotkey()
    app.finishLaunching()
    AppHelper.callAfter(controller.show_context, capture_native_context())
    print(f"MCP-Vision UI ready · ⌥Space · http://127.0.0.1:{server.server_port}", flush=True)
    try:
        AppHelper.runEventLoop()
    finally:
        server.shutdown()
        server.server_close()
