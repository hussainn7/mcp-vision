"""Draw the buddy on macOS: a glowing blue triangle that lives next to the cursor.

One small borderless window follows the animator's position at 60 Hz. It is
click-through, floats above everything on every Space (including full-screen
apps), and is excluded from screen capture so the buddy never appears in the
screenshots it sends to the model. All methods must run on the main thread;
``MainThreadPointer`` adapts it for the companion's worker thread.
"""
from __future__ import annotations

import math
import time

from mcp_vision.buddy.animator import BuddyAnimator, RenderState
from mcp_vision.buddy.geometry import Rect

BLUE = (0.2, 0.5, 1.0)          # #3380FF
WIDTH, HEIGHT = 340.0, 120.0    # room for the bubble to the right of the buddy
ANCHOR = (44.0, 44.0)           # buddy center inside the (flipped) view
TRIANGLE_SIDE = 16.0
WAVE_PROFILE = (0.4, 0.7, 1.0, 0.7, 0.4)

_CLASSES: dict[str, type] = {}


def _blue(alpha: float = 1.0):
    import AppKit

    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*BLUE, alpha)


def _glow(radius: float, alpha: float = 0.9) -> None:
    import AppKit

    shadow = AppKit.NSShadow.alloc().init()
    shadow.setShadowColor_(_blue(alpha))
    shadow.setShadowBlurRadius_(radius)
    shadow.setShadowOffset_(AppKit.NSMakeSize(0, 0))
    shadow.set()


def _draw_triangle(angle: float, scale: float) -> None:
    import AppKit

    AppKit.NSGraphicsContext.saveGraphicsState()
    _glow(8 + max(0.0, scale - 1) * 20)
    transform = AppKit.NSAffineTransform.transform()
    transform.translateXBy_yBy_(*ANCHOR)
    transform.rotateByDegrees_(angle)          # flipped view: positive is clockwise
    transform.scaleBy_(scale)
    transform.concat()
    height = TRIANGLE_SIDE * math.sqrt(3) / 2
    path = AppKit.NSBezierPath.bezierPath()
    path.moveToPoint_(AppKit.NSMakePoint(0, -2 * height / 3))
    path.lineToPoint_(AppKit.NSMakePoint(-TRIANGLE_SIDE / 2, height / 3))
    path.lineToPoint_(AppKit.NSMakePoint(TRIANGLE_SIDE / 2, height / 3))
    path.closePath()
    _blue().setFill()
    path.fill()
    AppKit.NSGraphicsContext.restoreGraphicsState()


def wave_heights(level: float, t: float) -> list[float]:
    """Clicky's waveform: five bars driven by mic level plus a gentle idle shimmer."""
    energy = pow(min(max(level - 0.008, 0.0) * 2.85, 1.0), 0.76)
    return [3 + energy * 10 * profile + (math.sin(t * 3.6 + i * 0.35) + 1) / 2 * 1.5
            for i, profile in enumerate(WAVE_PROFILE)]


def _draw_waveform(level: float, t: float) -> None:
    import AppKit

    AppKit.NSGraphicsContext.saveGraphicsState()
    _glow(6)
    _blue().setFill()
    bar, gap = 2.0, 2.0
    total = len(WAVE_PROFILE) * bar + (len(WAVE_PROFILE) - 1) * gap
    left = ANCHOR[0] - total / 2
    for index, height in enumerate(wave_heights(level, t)):
        rect = AppKit.NSMakeRect(left + index * (bar + gap), ANCHOR[1] - height / 2, bar, height)
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 1.0, 1.0).fill()
    AppKit.NSGraphicsContext.restoreGraphicsState()


def _draw_spinner(t: float) -> None:
    import AppKit

    AppKit.NSGraphicsContext.saveGraphicsState()
    _glow(5, 0.7)
    rotation = (t / 0.8) * 360.0
    path = AppKit.NSBezierPath.bezierPath()
    path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
        AppKit.NSMakePoint(*ANCHOR), 7.0, rotation + 0.15 * 360, rotation + 0.85 * 360, False)
    path.setLineWidth_(2.5)
    path.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    _blue().setStroke()
    path.stroke()
    AppKit.NSGraphicsContext.restoreGraphicsState()


def _draw_bubble(text: str, alpha: float) -> None:
    import AppKit

    font = AppKit.NSFont.systemFontOfSize_weight_(11.0, AppKit.NSFontWeightMedium)
    attributes = {AppKit.NSFontAttributeName: font,
                  AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor().colorWithAlphaComponent_(alpha)}
    string = AppKit.NSString.stringWithString_(text)
    size = string.sizeWithAttributes_(attributes)
    width, height = size.width + 16, size.height + 8
    x = ANCHOR[0] + 10
    y = ANCHOR[1] + 18 - height / 2
    AppKit.NSGraphicsContext.saveGraphicsState()
    _glow(6, 0.5 * alpha)
    _blue(alpha).setFill()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSMakeRect(x, y, width, height), 6.0, 6.0).fill()
    AppKit.NSGraphicsContext.restoreGraphicsState()
    string.drawAtPoint_withAttributes_(AppKit.NSMakePoint(x + 8, y + 4), attributes)


def draw(state: RenderState | None, t: float) -> None:
    if state is None or not state.visible:
        return
    resting = state.mode == "follow"
    if resting and state.voice == "listening":
        _draw_waveform(state.level, t)
    elif resting and state.voice == "thinking":
        _draw_spinner(t)
    else:
        _draw_triangle(state.angle, state.scale)
    if state.bubble and state.bubble_alpha > 0:
        _draw_bubble(state.bubble, state.bubble_alpha)


def _view_class():
    if "view" not in _CLASSES:
        import AppKit
        import objc

        class BuddyView(AppKit.NSView):
            def initWithFrame_(self, frame):
                self = objc.super(BuddyView, self).initWithFrame_(frame)
                if self is None:
                    return None
                self.render_state = None
                self.render_time = 0.0
                return self

            def isFlipped(self):
                return True

            def isOpaque(self):
                return False

            def drawRect_(self, rect):
                draw(self.render_state, self.render_time)

        _CLASSES["view"] = BuddyView
    return _CLASSES["view"]


def _ticker_class():
    """NSObject that forwards an NSTimer's ``fire:`` to a Python callable."""
    if "ticker" not in _CLASSES:
        import Foundation
        import objc

        class BuddyTicker(Foundation.NSObject):
            def initWithCallback_(self, callback):
                self = objc.super(BuddyTicker, self).init()
                if self is None:
                    return None
                self.callback = callback
                return self

            def fire_(self, _timer):
                try:
                    self.callback()
                except Exception:          # never let one bad frame kill the run loop
                    import traceback
                    traceback.print_exc()

        _CLASSES["ticker"] = BuddyTicker
    return _CLASSES["ticker"]


def screen_rects() -> list[Rect]:
    """Every display in global top-left points."""
    import AppKit

    screens = AppKit.NSScreen.screens()
    if not screens:
        return []
    primary_height = float(screens[0].frame().size.height)
    rects = []
    for screen in screens:
        frame = screen.frame()
        top = primary_height - float(frame.origin.y) - float(frame.size.height)
        rects.append(Rect(float(frame.origin.x), top, float(frame.size.width), float(frame.size.height)))
    return rects


def mouse_global() -> tuple[float, float]:
    import AppKit

    location = AppKit.NSEvent.mouseLocation()
    primary_height = float(AppKit.NSScreen.screens()[0].frame().size.height)
    return float(location.x), primary_height - float(location.y)


class BuddyOverlay:
    """The on-screen buddy. Main thread only."""

    def __init__(self, *, visible: bool = True):
        import AppKit

        self.animator = BuddyAnimator(screens=screen_rects)
        self.visible = visible
        self._last: RenderState | None = None
        rect = AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setHasShadow_(False)
        window.setIgnoresMouseEvents_(True)
        window.setReleasedWhenClosed_(False)
        window.setLevel_(AppKit.NSScreenSaverWindowLevel)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        try:
            window.setSharingType_(AppKit.NSWindowSharingNone)   # keep the buddy out of screenshots
        except Exception:
            pass
        self.view = _view_class().alloc().initWithFrame_(rect)
        window.setContentView_(self.view)
        self.window = window
        if visible:
            window.orderFrontRegardless()
        self._ticker = _ticker_class().alloc().initWithCallback_(self.tick)
        self.timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 60, self._ticker, "fire:", None, True)
        AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, AppKit.NSRunLoopCommonModes)

    # -- Pointer port -------------------------------------------------------------
    def set_state(self, state: str, detail: str = "") -> None:
        self.animator.set_voice(state)
        if state != "idle" and not self.visible:
            self.window.orderFrontRegardless()

    def point(self, x: float, y: float, label: str) -> None:
        self.animator.point(x, y, label)
        self.window.orderFrontRegardless()

    def release(self) -> None:
        self.animator.release()

    def set_level(self, level: float) -> None:
        self.animator.set_level(level)

    def set_visible(self, visible: bool) -> None:
        self.visible = visible
        if visible:
            self.window.orderFrontRegardless()
        elif not self.animator.busy and self.animator.voice == "idle":
            self.window.orderOut_(None)

    # -- frame loop -----------------------------------------------------------------
    def tick(self) -> None:
        import AppKit

        now = time.monotonic()
        try:
            mouse = mouse_global()
            primary_height = float(AppKit.NSScreen.screens()[0].frame().size.height)
        except Exception:
            return
        state = self.animator.tick(now, mouse)
        hidden = not self.visible and state.mode == "follow" and state.voice == "idle"
        if hidden:
            if self.window.isVisible():
                self.window.orderOut_(None)
            return
        origin_x = state.x - ANCHOR[0]
        origin_y = primary_height - (state.y - ANCHOR[1]) - HEIGHT
        self.window.setFrameOrigin_(AppKit.NSMakePoint(origin_x, origin_y))
        animating = state.voice in {"listening", "thinking"} or state.mode != "follow"
        if animating or state != self._last:
            self.view.render_state = state
            self.view.render_time = now
            self.view.setNeedsDisplay_(True)
        self._last = state


class MainThreadPointer:
    """Pointer port for worker threads: hops every call onto the main thread."""

    def __init__(self, overlay: BuddyOverlay):
        self.overlay = overlay

    def set_state(self, state: str, detail: str = "") -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self.overlay.set_state, state, detail)

    def point(self, x: float, y: float, label: str) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self.overlay.point, x, y, label)

    def release(self) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self.overlay.release)
