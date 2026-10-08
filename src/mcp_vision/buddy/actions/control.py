"""Plip's hands: click, scroll, press keys and drag on whatever is on screen.

Targets come from the numbered screen map (``{"id": 7}``), a visible label
(``{"text": "Play"}``) or screenshot pixels (``{"x": 410, "y": 88}``). Numbers
and labels are exact; pixels are the fallback.

Every scroll checks itself against the Accessibility map, so "that's the bottom"
comes back instead of a step that silently did nothing. Clicks and keys that
spend money, send, delete or submit ask first.
"""
from __future__ import annotations

import re
import time

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec, Preview
from mcp_vision.buddy.actions.host import parse_keys

RISKY = re.compile(r"\b(buy|purchase|pay|place (your )?order|checkout|check out|delete|remove|erase|send|submit|"
                   r"transfer|confirm|sign out|log ?out|unsubscribe|book|reserve|publish|post|merge|deploy|"
                   r"trash|discard|wipe|format|"
                   # applying sends your details to someone; "apply filters" or "apply changes" doesn't
                   r"apply(?!\s+(filters?|changes|settings|coupon|code|promo|discount|theme|style|formatting)\b))\b",
                   re.IGNORECASE)
RISKY_KEYS = {"cmd+q", "cmd+alt+esc", "cmd+delete", "cmd+backspace", "cmd+shift+delete", "cmd+shift+backspace"}
PAGE_LINES = 8                 # wheel steps (x3 lines each) per "page"
MAX_SCROLL_TO = 15


def _screen(ctx: ActionContext):
    shots, context = ctx.screen if isinstance(ctx.screen, tuple) else ([], None)
    return list(shots or []), context


def resolve(ctx: ActionContext, args: dict, *, key: str = "") -> tuple[float, float, str]:
    """A target in args -> (global x, global y, label)."""
    prefix = f"{key}_" if key else ""
    shots, context = _screen(ctx)
    raw_id = args.get(f"{prefix}id")
    if raw_id is not None:
        if ctx.state.get("scrolled"):
            raise ActionError("The page scrolled, so I'll take a fresh look first.",
                              hint="the [id] numbers changed when it scrolled: aim by its text or by x,y from a fresh "
                                   "look instead")
        try:
            control = context.ids.get(int(str(raw_id).lstrip("#"))) if context is not None else None
        except ValueError:
            control = None
        if control is None:
            raise ActionError(f"I can't find number {raw_id} on screen anymore. Let me look again.",
                              hint="aim by its text or by x,y from the screenshot instead")
        return control.x, control.y, control.label
    text = args.get(f"{prefix}text")
    if text:
        for candidate in (ctx.observe(), context):
            found = candidate.find(str(text)) if candidate is not None else None
            if found is not None:
                return found.x, found.y, found.label
        raise ActionError(f"I don't see {text} on screen.",
                          hint="it may be scrolled out of view or worded differently: scroll_to it, or aim by x,y "
                               "from the screenshot")
    if f"{prefix}x" in args and f"{prefix}y" in args and shots:
        try:
            px, py = float(args[f"{prefix}x"]), float(args[f"{prefix}y"])
        except (TypeError, ValueError):
            raise ActionError("Those coordinates don't look right.") from None
        screen = args.get("screen")
        shot = next((item for item in shots if screen and item.screen.index == int(screen)), None) or \
            next((item for item in shots if item.screen.is_cursor_screen), shots[0])
        gx, gy = shot.to_global(px, py)
        # Named by what it lands on, not what the model calls it: "Buy now" clicked by x,y still asks first.
        return gx, gy, _under(ctx, context, gx, gy) or str(args.get("label") or "there")
    raise ActionError("Tell me what to click: a number from the screen map, its label, or x and y.")


def _under(ctx: ActionContext, context, x: float, y: float, reach: float = 40.0) -> str:
    """The label of the control a point lands on (the nearest center within ``reach`` points), or ""."""
    for candidate in (context, None):
        if candidate is None:
            try:
                candidate = ctx.observe()             # the map the model saw may be gone: read it fresh
            except Exception:
                candidate = None
        controls = list(getattr(candidate, "controls", None) or [])
        near = min(controls, key=lambda c: abs(c.x - x) + abs(c.y - y), default=None)
        if near is not None and abs(near.x - x) <= reach and abs(near.y - y) <= reach:
            return near.label
    return ""


def _glide(ctx: ActionContext, x: float, y: float, label: str) -> None:
    try:
        ctx.animate(x, y, label)
    except Exception:
        pass
    time.sleep(0.25)                            # let them see where it's about to act


# -- click ------------------------------------------------------------------------------------
def preview_click(ctx: ActionContext, args: dict) -> Preview | None:
    x, y, label = resolve(ctx, args)
    if not RISKY.search(label):
        return None
    return Preview(title=f"Click “{label[:40]}”", lines=["This one can't be undone, so I'm checking first."],
                   confirm="Click it", state=(x, y, label))


def click(ctx: ActionContext, args: dict, state: tuple | None = None) -> ActionResult:
    x, y, label = state if state is not None else resolve(ctx, args)
    button = "right" if str(args.get("button", "")).lower() == "right" else "left"
    count = 2 if args.get("double") else 1
    _glide(ctx, x, y, label)
    ctx.host.click(x, y, button, count)
    verb = {("left", 1): "Clicked", ("left", 2): "Double-clicked"}.get((button, count), "Right-clicked")
    return ActionResult(report=f"{verb.lower()} {label!r}", look_after=0.15, settle=4.0, detail=f"{verb} {label[:30]}")


# -- scroll -----------------------------------------------------------------------------------
def _scroll_point(ctx: ActionContext, args: dict) -> tuple[float, float]:
    if any(key in args for key in ("id", "text", "x")):
        x, y, _ = resolve(ctx, args)
        return x, y
    shots, _ = _screen(ctx)
    if shots:
        frame = next((item for item in shots if item.screen.is_cursor_screen), shots[0]).screen.frame
        return frame.x + frame.width / 2, frame.y + frame.height * 0.55
    from mcp_vision.buddy.capture import cursor_position

    where = cursor_position()
    if where is None:
        raise ActionError("I need to look at the screen before I can scroll.")
    return where


def _amount(args: dict) -> int:
    raw = args.get("amount", "page")
    if isinstance(raw, str):
        lowered = raw.lower()
        if lowered in {"half", "half page"}:
            return PAGE_LINES // 2
        if lowered in {"a lot", "lots", "far"}:
            return PAGE_LINES * 3
        try:
            raw = float(lowered)
        except ValueError:
            return PAGE_LINES
    try:
        return max(1, min(60, round(float(raw) * PAGE_LINES)))
    except (TypeError, ValueError):
        return PAGE_LINES


def _deltas(direction: str, lines: int) -> tuple[int, int]:
    return {"down": (lines, 0), "up": (-lines, 0), "right": (0, lines), "left": (0, -lines)}.get(direction, (lines, 0))


def _moved(ctx: ActionContext, before) -> bool | None:
    """Did the screen move after a scroll? None when there's no map to tell."""
    if before is None or before.empty:
        return None
    for _ in range(4):                      # smooth scrolling and lazy loading take a moment
        time.sleep(0.15)
        after = ctx.observe()
        if after is not None and after.signature() != before.signature():
            return True
    return False


def scroll(ctx: ActionContext, args: dict) -> ActionResult:
    x, y = _scroll_point(ctx, args)
    direction = str(args.get("direction") or "down").lower()
    if str(args.get("amount", "")).lower() in {"all", "top", "bottom", "end"}:
        for _ in range(10):                 # until it stops moving: the top or bottom in one action
            before = ctx.observe()
            ctx.host.scroll(x, y, *_deltas(direction, 60))
            if _moved(ctx, before) is not True:
                break
        ctx.state["scrolled"] = True
        return ActionResult(report=f"scrolled all the way {direction}", look_after=0.3,
                            detail=f"Scrolled all the way {direction}")
    before = ctx.observe()
    ctx.host.scroll(x, y, *_deltas(direction, _amount(args)))
    if _moved(ctx, before) is False:
        edge = {"down": "bottom", "up": "top"}.get(direction, "edge")
        return ActionResult(report=f"scrolled {direction}, but nothing moved: that's the {edge}, or that part "
                                   "doesn't scroll. try another area by id, or click into the page and press pagedown",
                            look_after=0.1, detail="Nothing moved")
    ctx.state["scrolled"] = True            # the numbers the model saw have moved
    return ActionResult(report=f"scrolled {direction}", look_after=0.3, detail=f"Scrolled {direction}")


def scroll_to(ctx: ActionContext, args: dict) -> ActionResult:
    """Scroll until ``text`` is on screen, reading the Accessibility map between steps (no model calls)."""
    text = str(args.get("text") or "").strip()
    if not text:
        raise ActionError("Tell me what to look for.")
    direction = str(args.get("direction") or "down").lower()
    x, y = _scroll_point(ctx, {})
    seen = ctx.observe()
    if seen is None:
        raise ActionError("I can't read this app's layout, so I'll scroll a page at a time instead.")
    found, scrolls = seen.find(text), 0
    while found is None and scrolls < MAX_SCROLL_TO:
        before = seen.signature()
        ctx.host.scroll(x, y, *_deltas(direction, PAGE_LINES))
        scrolls += 1
        time.sleep(0.3)
        seen = ctx.observe() or seen
        found = seen.find(text)
        if found is None and seen.signature() == before:
            break                           # the end of the page
    if scrolls:
        ctx.state["scrolled"] = True
    if found is None:
        return ActionResult(report=f"scrolled {scrolls} times; {text!r} isn't on this page (maybe worded "
                                   "differently)", look_after=0.2, detail=f"Not found after {scrolls} scrolls")
    _glide(ctx, found.x, found.y, found.label[:30])
    return ActionResult(report=f"found {found.label[:80]!r}; it's on screen now", look_after=0.2,
                        detail=f"Found after {scrolls} scroll{'s' if scrolls != 1 else ''}")


# -- keys and drag ----------------------------------------------------------------------------
def _keys(args: dict) -> str:
    keys = str(args.get("keys") or args.get("key") or "").strip()
    try:
        parse_keys(keys)
    except ValueError:
        raise ActionError(f"I don't know the key {keys or 'you meant'}.") from None
    return keys


def preview_press(ctx: ActionContext, args: dict) -> Preview | None:
    keys = _keys(args)
    if keys.replace(" ", "").lower() not in RISKY_KEYS:
        return None
    return Preview(title=f"Press {keys}", lines=["That can close or delete things."], confirm="Press it", state=keys)


def press(ctx: ActionContext, args: dict, state: str | None = None) -> ActionResult:
    keys = state or _keys(args)
    times = max(1, min(int(args.get("times") or 1), 20))
    for _ in range(times):
        ctx.host.press(keys)
        time.sleep(0.05)
    return ActionResult(report=f"pressed {keys}" + (f" x{times}" if times > 1 else ""), look_after=0.15,
                        settle=3.0, detail=f"Pressed {keys}")


def drag(ctx: ActionContext, args: dict) -> ActionResult:
    x1, y1, label1 = resolve(ctx, args, key="from")
    x2, y2, label2 = resolve(ctx, args, key="to")
    _glide(ctx, x1, y1, label1)
    ctx.host.drag(x1, y1, x2, y2)
    return ActionResult(report=f"dragged {label1!r} onto {label2!r}", look_after=0.6, detail="Dragged it")


def wait(ctx: ActionContext, args: dict) -> ActionResult:
    try:
        seconds = max(0.5, min(float(args.get("seconds") or 2), 15.0))
    except (TypeError, ValueError):
        seconds = 2.0
    # Up to that long, and over as soon as the screen holds still (a beat at least), not a blind sleep.
    return ActionResult(report=f"waited for it to settle (up to {seconds:g}s)", look_after=min(seconds, 1.0),
                        settle=seconds, detail="Waiting")


def look(ctx: ActionContext, args: dict) -> ActionResult:
    ctx.state["force_image"] = True
    return ActionResult(report="here's a full screenshot", look_after=0.1, detail="Taking a closer look")


SPECS = (
    ActionSpec("click", "control", "Clicking {text}", click, preview=preview_click,
               args='{"id"} or {"text"} or {"x","y"}, "double"?, "button"?: right'),
    ActionSpec("scroll", "control", "Scrolling {direction}", scroll,
               args='{"direction": down|up|left|right, "amount"?: pages | "all", "id"?}'),
    ActionSpec("scroll_to", "control", "Looking for {text}", scroll_to, args='{"text", "direction"?}'),
    ActionSpec("press", "control", "Pressing {keys}", press, preview=preview_press,
               args='{"keys": "cmd+t" | "return" | "space" | "cmd+=" | "pagedown", "times"?}'),
    ActionSpec("drag", "control", "Dragging", drag, args='{"from_id", "to_id"} or from_x/from_y, to_x/to_y'),
    ActionSpec("wait", "control", "Waiting a moment", wait, args='{"seconds"}'),
    ActionSpec("look", "control", "Taking a closer look", look, args="{}"),
)
