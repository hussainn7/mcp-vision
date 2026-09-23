"""Top-center notch HUD: the MVP primary interface.

A tiny pill at the top of the screen that shows the agent's state:
listening (pulsing dot + waveform), working (spinner), a question,
done (check), or failure. Collapses quickly after completion.
"""
from __future__ import annotations


def top_center_origin(frame: tuple[float, float, float, float], size: tuple[float, float]) -> tuple[float, float]:
    x, y, width, _height = frame
    return (x + (width - size[0]) / 2, y + 12)


class NotchHUD:
    """Stateful top-center pill. Owns its window; callers only set states."""

    WIDTH = 460
    HEIGHT = 62

    def __init__(self):
        import AppKit
        from PyObjCTools import AppHelper
        self.AppKit = AppKit
        self.AppHelper = AppHelper
        self.generation = 0
        self._pulse_timer = None
        self._build()

    def _color(self, r, g, b, a=1.0):
        return self.AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)

    def _build(self):
        AppKit = self.AppKit
        rect = AppKit.NSMakeRect(0, 0, self.WIDTH, self.HEIGHT)
        style = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False)
        panel.setLevel_(AppKit.NSStatusWindowLevel + 2)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                     AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        panel.setAccessibilityLabel_("MCP-Vision status")
        root = AppKit.NSView.alloc().initWithFrame_(rect)
        root.setWantsLayer_(True)
        root.layer().setBackgroundColor_(self._color(.06, .075, .105, .98).CGColor())
        root.layer().setCornerRadius_(self.HEIGHT / 2)
        root.layer().setBorderWidth_(1)
        root.layer().setBorderColor_(self._color(.25, .32, .40, .95).CGColor())
        panel.setContentView_(root)
        self.root = root

        self.icon = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(22, 24, 14, 14))
        self.icon.setWantsLayer_(True)
        self.icon.layer().setBackgroundColor_(AppKit.NSColor.systemTealColor().CGColor())
        self.icon.layer().setCornerRadius_(7)
        root.addSubview_(self.icon)

        self.title = AppKit.NSTextField.labelWithString_("")
        self.title.setFrame_(AppKit.NSMakeRect(46, 30, 300, 20))
        self.title.setFont_(AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightBold))
        self.title.setTextColor_(AppKit.NSColor.systemTealColor())
        root.addSubview_(self.title)

        self.detail = AppKit.NSTextField.labelWithString_("")
        self.detail.setFrame_(AppKit.NSMakeRect(46, 8, 330, 22))
        self.detail.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self.detail.setTextColor_(AppKit.NSColor.whiteColor())
        self.detail.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        root.addSubview_(self.detail)

        teal = AppKit.NSColor.systemTealColor().CGColor()
        self.waveform_bars = []
        for index in range(5):
            bar = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(388 + index * 11, 22, 5, 18))
            bar.setWantsLayer_(True)
            bar.layer().setBackgroundColor_(teal)
            bar.layer().setCornerRadius_(2.5)
            bar.setHidden_(True)
            root.addSubview_(bar)
            self.waveform_bars.append(bar)

        self.panel = panel

    def _place(self, screen=None):
        AppKit = self.AppKit
        if screen is None:
            screen = AppKit.NSScreen.mainScreen()
        frame = screen.visibleFrame()
        origin = top_center_origin(
            (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height),
            (self.WIDTH, self.HEIGHT))
        self.panel.setFrameOrigin_(AppKit.NSMakePoint(*origin))

    def _cancel_pulse(self):
        if self._pulse_timer is not None:
            try:
                self._pulse_timer.cancel()
            except Exception:
                pass
            self._pulse_timer = None

    def _start_pulse(self):
        import time as _time
        start = _time.monotonic()
        generation = self.generation

        def beat():
            if generation != self.generation or not self.panel.isVisible():
                self.icon.layer().setOpacity_(1.0)
                return
            phase = (_time.monotonic() - start) % 1.2
            opacity = .45 + .55 * (1 - abs(phase / .6 - 1))
            self.icon.layer().setOpacity_(max(.4, min(1.0, opacity)))
            from PyObjCTools import AppHelper
            self._pulse_timer = AppHelper.callLater(.06, beat)

        beat()

    def set(self, state: str, detail: str = "") -> None:
        """state: listening|working|question|done|error|cancelled"""
        import AppKit
        self.generation += 1
        self._cancel_pulse()
        labels = {
            "listening": "●  Listening",
            "preparing": None,  # title supplied via detail label
            "understanding": "◌  Understanding…",
            "working": "◌  Working…",
            "acting": "◌  Working…",
            "verifying": "◌  Verifying…",
            "question": "?  Your answer",
            "done": "✓  Done",
            "error": "✕  Couldn't verify result",
            "cancelled": "Stopped",
        }
        heading = labels.get(state, state.replace('_', ' ').capitalize())
        kind = {"listening": "listening", "preparing": "listening", "question": "question",
                "done": "done", "error": "error", "cancelled": "cancelled"}.get(state, "working")
        if state == "preparing":
            heading = (detail or "◌  Preparing…").strip()
            detail = ""
        self.title.setStringValue_(heading)
        detail = (detail or "").strip()
        self.detail.setStringValue_(detail[:90])
        self.detail.setHidden_(not detail)
        colors = {
            "listening": AppKit.NSColor.systemTealColor(),
            "working": AppKit.NSColor.systemTealColor(),
            "question": self._color(1, .8, .35, 1),
            "done": AppKit.NSColor.systemGreenColor(),
            "error": AppKit.NSColor.systemRedColor(),
            "cancelled": AppKit.NSColor.secondaryLabelColor(),
        }
        color = colors.get(kind, AppKit.NSColor.systemTealColor())
        self.title.setTextColor_(color)
        self.icon.layer().setBackgroundColor_(color.CGColor())
        self.icon.layer().setOpacity_(1.0)
        listening = kind == "listening"
        for bar in self.waveform_bars:
            bar.setHidden_(not listening)
        if listening:
            self.detail.setStringValue_(detail or "Speak now")
            self._start_pulse()
        self._place()
        self.panel.orderFrontRegardless()
        self.panel.setAccessibilityValue_(heading)

    def partial(self, text: str, generation: int) -> None:
        if generation != self.generation or not self.panel.isVisible():
            return
        self.detail.setStringValue_(str(text)[-90:])

    def waveform(self, level: float, generation: int) -> None:
        if generation != self.generation or not self.panel.isVisible():
            return
        amount = max(.08, min(1.0, float(level)))
        for index, bar in enumerate(self.waveform_bars):
            height = 8 + 30 * amount * (.55 + .45 * ((index * 3) % 5) / 4)
            bar.setFrame_(self.AppKit.NSMakeRect(388 + index * 11, 31 - height / 2, 5, height))

    def hide(self) -> None:
        self.generation += 1
        self._cancel_pulse()
        self.icon.layer().setOpacity_(1.0)
        self.panel.orderOut_(None)

    def is_visible(self) -> bool:
        return self.panel.isVisible()
