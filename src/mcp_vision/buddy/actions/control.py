"""Plip's hands: click, scroll, press keys, drag. Targets: a map id, a visible label, or screenshot x,y.

Scrolls check themselves and try fallbacks; scroll_to searches locally, no model turns. Risky clicks ask first.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec, Preview
from mcp_vision.buddy.actions.host import Reveal, parse_keys
from mcp_vision.buddy.screen_context import Control
from mcp_vision.buddy.watch import difference

RISKY = re.compile(r"\b(buy|purchase|pay|place (your |an? )?(order|bid)|order now|complete (your )?(order|purchase)|"
                   r"(continue|proceed) to (payment|checkout|pay)|make (a )?payment|checkout|check out|delete|remove|"
                   r"erase|send|submit|transfer|withdraw|donate|subscribe|authori[sz]e|grant access|allow access|"
                   r"confirm|sign out|log ?out|unsubscribe|book|reserve|publish|post|merge|deploy|"
                   r"trash|discard|wipe|format|"
                   # applying sends your details to someone; "apply filters" or "apply changes" doesn't
                   r"apply(?!\s+(filters?|changes|settings|coupon|code|promo|discount|theme|style|formatting)\b))\b",
                   re.IGNORECASE)
# keys that quit, delete, log out or send; parsed, so "⌘+q" matches too
RISKY_KEYS = {parse_keys(keys) for keys in ("cmd+q", "cmd+alt+esc", "cmd+delete", "cmd+shift+delete", "cmd+return",
                                            "cmd+shift+return", "cmd+shift+d", "cmd+shift+q", "cmd+alt+q",
                                            "cmd+shift+alt+q")}
PAGE_LINES = 8                 # wheel steps (x3 lines each) per "page"
MAX_SCROLL_TO = 15


def _stop_if_cut_off(ctx: ActionContext, generation: int) -> None:
    if ctx.generation != generation:
        raise ActionError("Stopped.")


def _screen(ctx: ActionContext):
    shots, context = ctx.screen if isinstance(ctx.screen, tuple) else ([], None)
    return list(shots or []), context


def resolve(ctx: ActionContext, args: dict, *, key: str = "", refind: bool = True) -> tuple[float, float, str]:
    """A target in args -> (global x, global y, label). ``refind``: re-find an id by label after a scroll."""
    prefix = f"{key}_" if key else ""
    shots, context = _screen(ctx)
    raw_id = args.get(f"{prefix}id", args.get(key) if key and isinstance(args.get(key), int) else None)
    if ctx.state.get("stale_map") and (raw_id is not None or f"{prefix}x" in args):
        # an earlier step loaded something new: old ids are stale
        raise ActionError("The screen changed, so I'll take a fresh look first.")
    scrolled = bool(ctx.state.get("scrolled")) and refind
    if scrolled and raw_id is None and f"{prefix}x" in args:
        raise ActionError("The page scrolled, so I'll take a fresh look first.")
    if raw_id is not None and context is not None:
        try:
            control = context.ids.get(int(str(raw_id).lstrip("#")))
        except ValueError:
            control = None
        if control is None:
            raise ActionError(f"I can't find number {raw_id} on screen anymore. Let me look again.",
                              hint="aim by its text or by x,y from the screenshot instead")
        if scrolled:
            moved = _refind(ctx, control)
            if moved is None:
                raise ActionError(f"The screen changed and {control.label[:40]} isn't where it was, "
                                  "so I'll take a fresh look first.")
            return moved.x, moved.y, moved.label
        return control.x, control.y, control.label
    text = args.get(f"{prefix}text") or (args.get(key) if key and isinstance(args.get(key), str) else None)
    if text:
        found = None
        fresh = ctx.observe()
        near = str(args.get(f"{prefix}near") or "").strip()
        for candidate in (fresh, context):
            if candidate is not None and found is None:
                found = _nearest(candidate, str(text), near) if near else candidate.find(str(text))
        if found is None:
            raise ActionError(f"I don't see {text} on screen" + (f" near {near}." if near else "."),
                              hint="it isn't in the controls list: click it by x,y from the screenshot (look {} if "
                                   "you have none), or scroll_to it if it's further down")
        return found.x, found.y, found.label
    if f"{prefix}x" in args and f"{prefix}y" in args and shots:
        try:
            px, py = float(args[f"{prefix}x"]), float(args[f"{prefix}y"])
        except (TypeError, ValueError):
            raise ActionError("Those coordinates don't look right.") from None
        screen = args.get("screen")
        shot = next((item for item in shots if screen and item.screen.index == int(screen)), None) or \
            next((item for item in shots if item.screen.is_cursor_screen), shots[0])
        gx, gy = shot.to_global(px, py)
        # named by what it lands on, so a risky x,y click still asks
        return gx, gy, _under(ctx, context, gx, gy) or str(args.get("label") or "there")
    raise ActionError("Tell me what to click: a number from the screen map, its label, or x and y.",
                      hint='give "id", "text" or "x" and "y"')


def _norm(label: str) -> str:
    return " ".join(label.lower().split())


def _refind(ctx: ActionContext, control):
    """The same control on a fresh map after a scroll (a repeated label: the copy where the page shift puts it)."""
    fresh = ctx.observe()
    if fresh is None:
        return None
    label = _norm(control.label)
    same = [item for item in fresh.controls if _norm(item.label) == label and item.role == control.role]
    if len(same) <= 1:
        return same[0] if same else None
    _, old = _screen(ctx)
    shift = _shift(old, fresh) if old is not None else None
    if shift is None:
        return None
    x, y = control.x + shift[0], control.y + shift[1]
    best = min(same, key=lambda item: abs(item.y - y) + abs(item.x - x))
    return best if abs(best.y - y) + abs(best.x - x) <= 40 else None


def _unique(context) -> dict:
    """Label -> its control or text, for labels that appear once."""
    counts: dict[str, list] = {}
    for item in [*context.controls, *context.texts]:
        counts.setdefault(_norm(item.label), []).append(item)
    return {label: items[0] for label, items in counts.items() if len(items) == 1 and label}


def _shift(old, fresh) -> tuple[float, float] | None:
    """Page shift between two maps: the median move of labels unique in both."""
    before, after = _unique(old), _unique(fresh)
    moves = sorted((after[label].x - item.x, after[label].y - item.y) for label, item in before.items()
                   if label in after)
    if not moves:
        return None
    dxs, dys = sorted(move[0] for move in moves), sorted(move[1] for move in moves)
    return dxs[len(dxs) // 2], dys[len(dys) // 2]


def _nearest(context, text: str, near: str):
    """The ``text`` control closest to the ``near`` label (that row's button)."""
    anchor = context.find(near)
    if anchor is None:
        return None
    needle = _norm(text)
    matches = [item for item in [*context.controls, *context.texts] if needle in _norm(item.label)]
    if not matches:
        return None
    # same row or below is "its" button; above is the previous item's
    return min(matches, key=lambda item: abs(item.x - anchor.x) * 0.3 + abs(item.y - anchor.y)
               + (60 if item.y < anchor.y - 8 else 0))


def _under(ctx: ActionContext, context, x: float, y: float, reach: float = 40.0) -> str:
    """Label under a point: the smallest frame holding it, else the nearest sizeless control within ``reach``."""
    for candidate in (context, None):
        if candidate is None:
            try:
                candidate = ctx.observe()             # the model's map may be gone
            except Exception:
                candidate = None
        controls = list(getattr(candidate, "controls", None) or [])
        holding = [c for c in controls if c.w > 0 and c.h > 0 and abs(c.x - x) <= c.w / 2 and abs(c.y - y) <= c.h / 2]
        if holding:
            return min(holding, key=lambda c: c.w * c.h).label
        sizeless = [c for c in controls if c.w <= 0 or c.h <= 0]
        near = min(sizeless, key=lambda c: abs(c.x - x) + abs(c.y - y), default=None)
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
    # what it lands on, or the model's label (a blind page names nothing)
    if not (RISKY.search(label) or RISKY.search(str(args.get("label") or ""))):
        return None
    return Preview(title=f"Click “{label[:40]}”", lines=["This one can't be undone, so I'm checking first."],
                   confirm="Click it", state=(x, y, label))


OPENING_ROLES = {"link", "tab", "menu item", "menu bar item", "row", "cell", "outline row"}


def _role(ctx: ActionContext, args: dict) -> str:
    """The clicked control's role, if the map knows it."""
    _, context = _screen(ctx)
    if context is None:
        return ""
    raw = args.get("id")
    if raw is not None:
        try:
            control = context.ids.get(int(str(raw).lstrip("#")))
        except ValueError:
            control = None
        return control.role if control is not None else ""
    found = context.find(str(args.get("text") or "")) if args.get("text") else None
    return found.role if found is not None else ""


def click(ctx: ActionContext, args: dict, state: tuple | None = None) -> ActionResult:
    x, y, label = state if state is not None else resolve(ctx, args)
    button = "right" if str(args.get("button", "")).lower() == "right" else "left"
    count = 2 if args.get("double") else 1
    _glide(ctx, x, y, label)
    ctx.host.click(x, y, button, count)
    role = _role(ctx, args)
    _remember(ctx, x, y, role)                  # "scroll up" next means that panel
    verb = {("left", 1): "Clicked", ("left", 2): "Double-clicked"}.get((button, count), "Right-clicked")
    return ActionResult(report=f"{verb.lower()} {label!r}", look_after=0.15, settle=4.0, detail=f"{verb} {label[:30]}",
                        opens=role in OPENING_ROLES)


# -- scroll -----------------------------------------------------------------------------------
# wheel at the best-guess panel; if nothing moved: its scroll bar, other spots, then page keys
MOVED = 2.5                   # mean gray change (0-255) that counts as moved
NEAR = 40                     # points: spots this close wheel the same thing
SHIFT = 6                     # points a label must move to count as scrolled
_EDGES = {"down": "bottom", "up": "top", "left": "left edge", "right": "right edge"}
_OTHER_WAY = {"down": "up", "up": "down", "left": "right", "right": "left"}
# focus roles where page keys scroll, not type or change a value
_PAGEABLE = {"", "AXWebArea", "AXScrollArea", "AXGroup", "AXLayoutArea", "AXSplitGroup", "AXWindow", "AXTable",
             "AXOutline", "AXRow", "AXCell", "AXBrowser", "AXStaticText", "AXHeading", "AXLink", "AXButton",
             "AXImage", "AXUnknown"}
_PAGE_KEYS = {("down", False): "pagedown", ("up", False): "pageup", ("down", True): "end", ("up", True): "home"}
_SWITCHES = {"tab", "menu", "menu item", "pop-up menu", "menubutton", "radio button", "dock item"}
_SCROLLED_TO = re.compile(r" \((at the top|scrolled to the bottom|scrolled \d+% down)\)$")
_DIGITS = re.compile(r"\d+")


class _Spot(NamedTuple):
    """Somewhere to wheel, and what's there for the report."""

    x: float
    y: float
    where: str                  # "the 'Inbox' panel", "the page"
    aside: bool = False         # not the main area: the report names it
    raw: bool = False           # a raw point, not a panel's middle


class _Click(NamedTuple):
    """Plip's last click: a plain scroll next means that panel."""

    x: float
    y: float
    app: str
    window: str                 # title, digits masked (unread counts)
    url: str


def _ask(ctx: ActionContext, name: str, *args, default=None, **kwargs):
    """Call an optional host method; ``default`` if it's missing or fails."""
    method = getattr(ctx.host, name, None)
    if method is None:
        return default
    try:
        return method(*args, **kwargs)
    except Exception:
        return default


def _near(a, b, gap: float = NEAR) -> bool:
    return abs(a[0] - b[0]) < gap and abs(a[1] - b[1]) < gap


def _deepest(areas, x: float, y: float):
    """The innermost (smallest) scroll area under a point."""
    holding = [area for area in areas if area.w > 0 and area.h > 0
               and abs(x - area.x) <= area.w / 2 and abs(y - area.y) <= area.h / 2]
    return min(holding, key=lambda area: area.w * area.h, default=None)


def _named(area) -> str:
    name = _SCROLLED_TO.sub("", area.label).strip()      # drop the "(scrolled 40% down)" suffix
    if area.role == "page" or name in {"", "page"}:
        return "the page"
    return "the scroll area" if name == "scroll area" else f"the {name[:30]!r} panel"


def _remember(ctx: ActionContext, x: float, y: float, role: str) -> None:
    """Remember this panel for the next plain scroll, unless the click switched content (tab, menu, sidebar)."""
    ctx.state["pointer"] = (x, y)
    _, context = _screen(ctx)
    if role in _SWITCHES or _sidebar(list(getattr(context, "scroll_areas", None) or []),
                                     getattr(context, "window_frame", None), x, y):
        ctx.state.pop("last_click", None)
        return
    ctx.state["last_click"] = _Click(x, y, getattr(context, "app", "") or "",
                                     _DIGITS.sub("#", getattr(context, "window", "") or ""),
                                     getattr(context, "url", "") or "")


def _sidebar(areas, frame, x: float, y: float) -> bool:
    """A narrow column at the window's left edge (Mail's mailboxes)."""
    if frame is None or frame.width < 600:
        return False
    biggest = max(areas, key=lambda area: area.w * area.h, default=None)
    inner = _deepest(areas, x, y)
    if inner is not None and inner is not biggest:
        return inner.w <= frame.width * 0.3 and inner.x - inner.w / 2 <= frame.x + 24
    return x - frame.x <= min(300.0, frame.width * 0.2)


def _clicked(ctx: ActionContext, now, frame, *, moved_on: bool) -> _Click | None:
    """The last click, if a plain scroll still means its panel (pointer unmoved, same app and page)."""
    click = ctx.state.get("last_click")
    if not isinstance(click, _Click):
        return None
    app = getattr(now, "app", "") or ""
    if moved_on or (click.app and app and click.app != app) or (frame is not None and not frame.contains(*click[:2])) \
            or not _same_page(click, getattr(now, "url", "") or "", getattr(now, "window", "") or ""):
        ctx.state.pop("last_click", None)
        return None
    return click


def _same_page(click: _Click, url: str, window: str) -> bool:
    if click.url and url:
        old, new = _address(click.url), _address(url)
        return new == old or new.startswith((old + "/", old + "#"))
    return not (click.window and window) or click.window == _DIGITS.sub("#", window)


def _address(url: str) -> str:
    """Host, path and #part, no ?query: which page a web app is on."""
    parts = urlsplit(url)
    return parts.netloc + parts.path.rstrip("/") + (f"#{parts.fragment}" if parts.fragment else "")


def _spots(ctx: ActionContext, args: dict, seen, lead: list | None = None) -> list[_Spot]:
    """Where to wheel, best first: a named target and its panel's middle, else the guesses."""
    _, context = _screen(ctx)
    now = seen if seen is not None and not seen.empty else context
    areas = list(getattr(now, "scroll_areas", None) or getattr(context, "scroll_areas", None) or [])
    frame = getattr(now, "window_frame", None) or getattr(context, "window_frame", None)
    biggest = max(areas, key=lambda area: area.w * area.h, default=None)
    spots = list(lead or [])
    if any(key in args for key in ("id", "text", "x")):
        x, y, label = resolve(ctx, args, refind=False)
        spots.append(_Spot(x, y, "that spot" if label == "there" else f"{label[:30]!r}", raw=True))
        inner = _deepest(areas, x, y)
        if inner is not None:
            spots.append(_Spot(inner.x, inner.y, _named(inner)))
    else:
        spots += _guesses(ctx, now, areas, frame, biggest)
    if not spots:
        shots, _ = _screen(ctx)
        if not shots:
            raise ActionError("I need to look at the screen before I can scroll.")
        frame = next((item for item in shots if item.screen.is_cursor_screen), shots[0]).screen.frame
        spots.append(_Spot(frame.x + frame.width / 2, frame.y + frame.height / 2, "the middle of the screen"))
    return _distinct(spots, areas, biggest)


def _guesses(ctx: ActionContext, now, areas, frame, biggest) -> list[_Spot]:
    spots = []
    mouse = _ask(ctx, "mouse_position")
    left = ctx.state.get("pointer")                                      # where Plip last left it
    theirs = mouse is not None and (left is None or not _near(mouse, left))
    click = _clicked(ctx, now, frame, moved_on=theirs)
    if click is not None:
        inner = _deepest(areas, click.x, click.y)
        if inner is not None and inner is not biggest:
            spots.append(_Spot(inner.x, inner.y, _named(inner), aside=True))     # side panel's middle
        elif inner is not None or not areas:                                 # maybe a panel the map can't name
            spots.append(_Spot(click.x, click.y, "the panel you last clicked in", aside=True, raw=True))
    focused = _ask(ctx, "focused_scroll_area")
    if focused is not None:
        whole = biggest.w * biggest.h if biggest is not None else frame.width * frame.height if frame else 0
        spots.append(_Spot(*focused.center, "the focused panel",
                           aside=focused.width * focused.height < 0.8 * whole))
    if theirs and frame is not None and frame.contains(*mouse):
        spots.append(_Spot(*mouse, "the panel under the pointer", aside=True, raw=True))
    if biggest is not None:
        spots.append(_Spot(biggest.x, biggest.y, _named(biggest)))
    if frame is not None and frame.width > 120 and frame.height > 120:
        spots.append(_Spot(frame.x + frame.width / 2, frame.y + frame.height * 0.6, "the window"))   # below the toolbar
    return spots


def _distinct(spots: list[_Spot], areas, biggest) -> list[_Spot]:
    """Drop spots that wheel the same thing (close by or same panel); a raw point keeps the page's middle."""
    kept: list[tuple[_Spot, Any]] = []
    for spot in spots:
        area = _deepest(areas, spot.x, spot.y)
        if any(_near(spot, other) or (area is not None and area is where
                                      and (area is not biggest or not (spot.raw or other.raw)))
               for other, where in kept):
            continue
        kept.append((spot, area))
    return [spot for spot, _ in kept]


def _amount(args: dict) -> int:
    raw = args.get("amount", "page")
    if isinstance(raw, str):
        lowered = raw.lower()
        if lowered in {"page", "a page", "one page"}:
            return PAGE_LINES
        if lowered in {"half", "half page"}:
            return PAGE_LINES // 2
        if lowered in {"a lot", "lots", "far"}:
            return PAGE_LINES * 3
        try:
            raw = float(lowered)
        except ValueError:
            return PAGE_LINES
    try:
        pages = float(raw)
    except (TypeError, ValueError):
        return PAGE_LINES
    return max(1, min(60, round(pages * PAGE_LINES)))


def _deltas(direction: str, lines: int) -> tuple[int, int]:
    direction = (direction or "down").lower()
    return {"down": (lines, 0), "up": (-lines, 0), "right": (0, lines), "left": (0, -lines)}.get(direction, (lines, 0))


def _pixels(ctx: ActionContext, x: float, y: float) -> bytes | None:
    try:
        return ctx.fingerprint(x, y)
    except Exception:
        return None


def _shifted(before, after) -> bool:
    """The map says it scrolled: a label moved, or most of the text swapped (recycled rows)."""
    if before is None or after is None or before.empty or after.empty:
        return False
    old, new = _unique(before), _unique(after)
    if any(abs(new[label].x - item.x) >= SHIFT or abs(new[label].y - item.y) >= SHIFT
           for label, item in old.items() if label in new):
        return True
    was, now = {_norm(item.label) for item in before.texts}, {_norm(item.label) for item in after.texts}
    return len(was - now) >= 3 and len(now - was) >= 3


def _moved(ctx: ActionContext, seen, way: _Way, pixels: list, tries: int):
    """Did a push move anything (pixels, then map)? -> (moved, map after); moved is None when it can't tell."""
    mapped = seen is not None and not seen.empty
    watched = [(point, shot) for point, shot in zip(way.watch, pixels, strict=False) if shot is not None]
    if watched:
        for _ in range(tries):
            time.sleep(0.1)
            if any(_changed(ctx, point, shot) for point, shot in watched):
                return True, None
        if not mapped:
            return False, None
        after = ctx.observe() or seen
        return _shifted(seen, after), after
    if not mapped:
        return None, seen
    after = seen
    for _ in range(tries):                  # smooth scrolling and lazy loading take a moment
        time.sleep(0.15)
        after = ctx.observe() or after
        if _shifted(seen, after):
            return True, after
    return False, after


def _changed(ctx: ActionContext, point, shot: bytes) -> bool:
    now = _pixels(ctx, *point)
    return now is not None and difference(shot, now) >= MOVED


def _aim(ctx: ActionContext, x: float, y: float) -> bool:
    """Pointer onto the spot before the 'before' look, so hover effects aren't a scroll. True if it moved."""
    left = ctx.state.get("pointer")
    hover = getattr(ctx.host, "hover", None)
    if hover is None or (left is not None and _near(left, (x, y), 1)):
        return False
    try:
        hover(x, y)
    except Exception:
        return False
    ctx.state["pointer"] = (x, y)
    time.sleep(0.06)                        # its hover state shows
    return True


def _before(ctx: ActionContext, way: _Way, seen, stale: bool, *, first: bool):
    """Aim, then the 'before' pixels (and map, if checked). -> (map, pixels, mapped, still stale)."""
    stale = way.aim() or stale
    pixels = [_pixels(ctx, x, y) for x, y in way.watch]
    mapped = seen is not None and (first or way.wide or all(shot is None for shot in pixels))
    if mapped and stale:
        seen, stale = ctx.observe() or seen, False
        pixels = [_pixels(ctx, x, y) for x, y in way.watch]
    return seen, pixels, mapped, stale


@dataclass
class _Way:
    """One way to scroll: the wheel at a spot, a scroll bar, or a page key."""

    what: str                                   # for the report: "its scroll bar"
    watch: list[tuple[float, float]]            # where to look for it moving
    push: Callable[[str], bool]                 # one push; False: nothing to push with
    aim: Callable[[], bool] = lambda: False     # True when it moved the pointer
    jumps: bool = False                         # one push goes all the way (home/end)
    spot: _Spot | None = None                   # the spot it scrolls, for the report
    wide: bool = False                          # moves the focused thing: check the whole map


def _ways(ctx: ActionContext, spots: list[_Spot], direction: str, lines: int, seen=None, *, to_end: bool = False):
    """Wheel at the best spot, its scroll bar, two more spots, then page keys where they scroll."""
    def wheel(spot: _Spot) -> _Way:
        def push(toward: str) -> bool:
            ctx.host.scroll(spot.x, spot.y, *_deltas(toward, lines))
            ctx.state["pointer"] = (spot.x, spot.y)
            return True
        return _Way(f"the wheel at {spot.where}", [(spot.x, spot.y)], push, lambda: _aim(ctx, spot.x, spot.y),
                    spot=spot)

    first = spots[0]
    yield wheel(first)
    frame = getattr(seen, "window_frame", None) or getattr(_screen(ctx)[1], "window_frame", None)
    within = frame if first.aside else None            # a side panel's bar, not the page's
    yield _Way("its scroll bar", [(first.x, first.y)],
               lambda toward: bool(_ask(ctx, "scroll_bar_step", first.x, first.y, toward, to_end=to_end,
                                        pages=lines / PAGE_LINES, within=within, default=False)),
               jumps=to_end, spot=first)
    for spot in spots[1:3]:
        yield wheel(spot)
    key = _PAGE_KEYS.get((direction, to_end))
    if key is not None and _ask(ctx, "focused_role") in _PAGEABLE:    # None: can't tell, so no keys
        yield _Way(f"pressing {key}", [(spot.x, spot.y) for spot in spots[:3]],
                   lambda toward: _press(ctx, _PAGE_KEYS.get((toward, to_end))), jumps=to_end, wide=True)


def _press(ctx: ActionContext, key: str | None) -> bool:
    if key is None:
        return False
    try:
        ctx.host.press(key)
    except Exception:
        return False
    return True


@dataclass
class _Push:
    moved: bool | None = False                  # None: can't tell, so trusted
    way: _Way | None = None                     # what moved it
    tried: list[str] = field(default_factory=list)
    seen: Any = None                            # the map after
    end: str = ""                               # already at this end, per its scroll bar


def _push(ctx: ActionContext, ways, direction: str, seen) -> _Push:
    """Each way in turn until something moves (first: 0.4 s + map; fallbacks: 0.2 s of pixels)."""
    tried: list[str] = []
    stale = False                               # the pointer moved since ``seen`` was read
    generation = ctx.generation
    for way in ways:
        _stop_if_cut_off(ctx, generation)
        seen, pixels, mapped, stale = _before(ctx, way, seen, stale, first=not tried)
        if not way.push(direction):
            continue
        moved, after = _moved(ctx, seen if mapped else None, way, pixels, tries=2 if tried else 4)
        if moved is not False:
            return _Push(moved, way, tried, after)
        seen = after or seen
        tried.append(way.what)
        if len(tried) == 1 and _at_end(seen, *way.watch[0], direction):
            return _Push(False, None, tried, seen, end=_EDGES[direction])     # elsewhere = the wrong thing
    return _Push(False, None, tried, seen)


def _at_end(seen, x: float, y: float, direction: str) -> bool:
    """The map says the scroll area there is already at that end."""
    area = _deepest(seen.scroll_areas, x, y) if seen is not None else None
    says = {"down": "to the bottom)", "up": "(at the top)"}.get(direction)
    return area is not None and says is not None and says in area.label


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else "nothing"


def _how(pushed: _Push) -> str:
    """' (X did nothing; Y did)' when the first way failed."""
    return f" ({_listed(pushed.tried)} did nothing; {pushed.way.what} did)" if pushed.tried and pushed.way else ""


def _said(what: str, pushed: _Push, then: str = "") -> str:
    """'scrolled down', naming the panel when it guessed a side one."""
    spot = pushed.way.spot if pushed.way is not None else None
    if spot is None or not spot.aside:
        return f"scrolled {what}{_how(pushed)}{then}"
    return (f"scrolled {spot.where} {what}{_how(pushed)}{then}; for another panel give its x,y or the id of "
            "anything inside it")


def _stuck(direction: str, pushed: _Push) -> str:
    """Nothing moved: one actionable line for the model."""
    if pushed.end:
        where = pushed.tried[0].removeprefix("the wheel at ")
        return (f"scrolled {direction}, but nothing moved: {where} is already at the {pushed.end}. if you meant "
                "another panel, give its x,y from scrollable or the id of anything inside it")
    return (f"scrolled {direction}, but nothing moved. tried {_listed(pushed.tried)}: it's at the "
            f"{_EDGES[direction]}, or that part doesn't scroll. give the panel's x,y from scrollable or the id of "
            "anything inside it, or read_page for the whole text")


_ALL = {"all", "top", "bottom", "end", "start", "max"}


def scroll(ctx: ActionContext, args: dict) -> ActionResult:
    direction = str(args.get("direction") or "down").lower()
    direction = direction if direction in _EDGES else "down"
    seen = ctx.observe()
    spots = _spots(ctx, args, seen)
    if str(args.get("amount", "")).lower() in _ALL:
        return _scroll_all_the_way(ctx, spots, direction, seen)
    pushed = _push(ctx, _ways(ctx, spots, direction, _amount(args), seen), direction, seen)
    if pushed.moved is False:
        return ActionResult(report=_stuck(direction, pushed), look_after=0.1, detail="Nothing moved")
    ctx.state["scrolled"] = True            # the numbers the model saw have moved
    return ActionResult(report=_said(direction, pushed), look_after=0.15, detail=f"Scrolled {direction}")


def _scroll_all_the_way(ctx: ActionContext, spots, direction: str, seen) -> ActionResult:
    """Push until it stops: the top or bottom in one action."""
    edge = _EDGES[direction]
    pushed = _push(ctx, _ways(ctx, spots, direction, 60, seen, to_end=True), direction, seen)
    if pushed.moved is False:
        return ActionResult(report=_stuck(direction, pushed), look_after=0.1, detail="Nothing moved")
    ctx.state["scrolled"] = True
    if pushed.moved is None:
        return ActionResult(report=f"scrolled toward the {edge}; i can't tell from here if it's all the way",
                            look_after=0.3, detail=f"Scrolled toward the {edge}")
    way, seen, stopped = pushed.way, pushed.seen, pushed.way.jumps     # no map after: the pixels saw it
    generation = ctx.generation
    for _ in range(0 if stopped else 9):
        _stop_if_cut_off(ctx, generation)
        pixels = [_pixels(ctx, x, y) for x, y in way.watch]
        way.push(direction)
        moved, seen = _moved(ctx, seen, way, pixels, tries=4)
        if not moved:
            stopped = True
            break
    if not stopped:
        return ActionResult(report=_said(f"a long way toward the {edge}", pushed, ", and it's still going: there's "
                                         "more"), look_after=0.3, detail=f"Scrolled toward the {edge}")
    return ActionResult(report=_said(f"to the {edge}", pushed), look_after=0.3, detail=f"Scrolled to the {edge}")


def _reveal(ctx: ActionContext, text: str, seen):
    """App scrolls ``text`` into view (AXScrollToVisible). -> (host reply, control, map)."""
    reveal = _ask(ctx, "scroll_to_visible", text) or Reveal()
    if not reveal.asked:
        return reveal, None, seen
    before = seen
    for _ in range(4):                      # it may animate there
        time.sleep(0.15)
        seen = ctx.observe() or seen
        found = seen.find(text)
        if found is not None:
            return reveal, found, seen
    if seen.signature(values=False) != before.signature(values=False):     # a live field isn't the page moving
        ctx.state["scrolled"] = True        # it moved, even if the text isn't mapped
    now = _ask(ctx, "scroll_to_visible", text, ask=False)       # re-check: a slow app may be half way
    reveal = now if now is not None and now.found else reveal
    if reveal.at is not None:               # in view, past what the map lists
        ctx.state["scrolled"] = True
        return reveal, Control(text, "text", *reveal.at), seen
    return reveal, None, seen


def scroll_to(ctx: ActionContext, args: dict) -> ActionResult:
    """Bring ``text`` on screen: the app's own scroll-into-view, else wheel + re-read the map (no model calls)."""
    text = str(args.get("text") or "").strip()
    if not text:
        raise ActionError("Tell me what to look for.")
    direction = str(args.get("direction") or "down").lower()
    direction = direction if direction in _EDGES else "down"
    either_way = "direction" not in args        # no direction: turn around once at the end
    limit = max(1, min(int(args.get("max") or MAX_SCROLL_TO), 30))
    seen = ctx.observe()
    if seen is None:
        raise ActionError("I can't read this app's layout, so I'll scroll a page at a time instead.")
    found = seen.find(text)
    if found is not None:
        return _found(ctx, found, "after 0 scrolls", "Found after 0 scrolls")
    reveal, found, seen = _reveal(ctx, text, seen)
    if found is not None:
        ctx.state["scrolled"] = True
        return _found(ctx, found, "and scrolled it into view", "Scrolled it into view")
    lead = []
    if reveal.found and reveal.scroller is not None:
        lead = [_Spot(*reveal.scroller, "the panel it's in", raw=True)]    # app ignored the ask: wheel there
        if reveal.direction in _EDGES:
            direction, either_way = reveal.direction, False
    spots = _spots(ctx, {key: value for key, value in args.items() if key in {"id", "x", "y"}}, seen, lead)
    ways = _ways(ctx, spots, direction, PAGE_LINES, seen)
    way, start, tried, stale = next(ways), direction, [], False
    scrolls, still, turned, moved_ever, ended = 0, 0, False, False, False
    generation = ctx.generation
    while found is None and way is not None and scrolls < limit * (2 if either_way else 1):
        _stop_if_cut_off(ctx, generation)
        seen, pixels, _, stale = _before(ctx, way, seen, stale, first=True)     # map read anyway, for the text
        if not way.push(direction):
            way = next(ways, None)
            continue
        scrolls += 1
        moved, after = _moved(ctx, seen, way, pixels, tries=2 if tried else 3)
        seen = after or ctx.observe() or seen
        found = seen.find(text)
        if moved:
            moved_ever, still = True, 0
            continue
        still += 1
        if moved_ever and still < 2:
            continue                            # lazy loading at the end: one more go
        if either_way and not turned:           # hit the end: look the other way
            direction, turned, still = _OTHER_WAY[direction], True, 0
        elif moved_ever or _at_end(seen, *way.watch[0], direction):
            ended = True                        # it moved, or its scroll bar says so
            break
        else:
            tried.append(way.what)              # never moved: next way
            way, direction, turned, still = next(ways, None), start, False, 0
    if moved_ever:
        ctx.state["scrolled"] = True
    if found is not None:
        plural = "s" if scrolls != 1 else ""
        how = f" ({_listed(tried)} did nothing; {way.what} did)" if tried else ""
        return _found(ctx, found, f"after {scrolls} scroll{plural}{how}", f"Found after {scrolls} scroll{plural}")
    if not (moved_ever or ended):
        if way is not None and way.what not in tried:
            tried.append(way.what)
        return ActionResult(ok=True, report=f"{text!r} isn't on screen and nothing moved: tried {_listed(tried)}. "
                                            "that part may not scroll this way. give the panel's x,y from scrollable "
                                            "or the id of anything inside it, or read_page with find",
                            look_after=0.2, detail="Nothing moved")
    where = "both ends" if turned else "the end" if ended else f"{scrolls} pages"
    if reveal.found:
        return ActionResult(ok=True, report=f"scrolled to {where} but couldn't get {text!r} on screen, though it's "
                                            "on the page: read_page with find has it", look_after=0.2,
                            detail=f"Not found after {scrolls} scrolls")
    return ActionResult(ok=True, report=f"scrolled to {where}; {text!r} isn't on this page. it may be "
                                        "worded differently: read_page with find, or look",
                        look_after=0.2, detail=f"Not found after {scrolls} scrolls")


def _found(ctx: ActionContext, found, how: str, detail: str) -> ActionResult:
    _glide(ctx, found.x, found.y, found.label[:30])
    return ActionResult(report=f"found {found.label[:80]!r} {how}; it's on screen now", look_after=0.2, detail=detail)


# -- keys, drag, waiting ----------------------------------------------------------------------
def preview_press(ctx: ActionContext, args: dict) -> Preview | None:
    keys = _keys(args)
    if parse_keys(keys) not in RISKY_KEYS:
        return None
    return Preview(title=f"Press {keys}", lines=["That can close, delete, log out or send things."], confirm="Press it",
                   state=keys)


def _keys(args: dict) -> str:
    keys = str(args.get("keys") or args.get("key") or "").strip()
    try:
        parse_keys(keys)
    except ValueError:
        raise ActionError(f"I don't know the key {keys or 'you meant'}.") from None
    return keys


def press(ctx: ActionContext, args: dict, state: str | None = None) -> ActionResult:
    keys = state or _keys(args)
    times = max(1, min(int(args.get("times") or 1), 20))
    for _ in range(times):
        ctx.host.press(keys)
        time.sleep(0.05)
    return ActionResult(report=f"pressed {keys}" + (f" x{times}" if times > 1 else ""), look_after=0.15,
                        settle=3.0, detail=f"Pressed {keys}")


_BINS = re.compile(r"\b(trash|bin|recycle|delete)\b", re.IGNORECASE)


def preview_drag(ctx: ActionContext, args: dict) -> Preview | None:
    """Ask before dragging onto the Trash or a delete zone."""
    x1, y1, label1 = resolve(ctx, args, key="from")
    x2, y2, label2 = resolve(ctx, args, key="to")
    if not (_BINS.search(label2) or RISKY.search(label2)):
        return None
    return Preview(title=f"Drag “{label1[:30]}” to {label2[:30]}", lines=["That throws it away."],
                   confirm="Drag it", state=(x1, y1, label1, x2, y2, label2))


def drag(ctx: ActionContext, args: dict, state: tuple | None = None) -> ActionResult:
    x1, y1, label1, x2, y2, label2 = state if state is not None else \
        (*resolve(ctx, args, key="from"), *resolve(ctx, args, key="to"))
    _glide(ctx, x1, y1, label1)
    ctx.host.drag(x1, y1, x2, y2)
    ctx.state["pointer"] = (x2, y2)
    ctx.state.pop("last_click", None)
    return ActionResult(report=f"dragged {label1!r} onto {label2!r}", look_after=0.6, detail="Dragged it")


def wait(ctx: ActionContext, args: dict) -> ActionResult:
    try:
        seconds = max(0.5, min(float(args.get("seconds") or 2), 15.0))
    except (TypeError, ValueError):
        seconds = 2.0
    # ends early once the screen holds still
    return ActionResult(report=f"waited for it to settle (up to {seconds:g}s)", look_after=min(seconds, 1.0),
                        settle=seconds, detail="Waiting")


def look(ctx: ActionContext, args: dict) -> ActionResult:
    ctx.state["force_image"] = True
    return ActionResult(report="here's a full screenshot", look_after=0.1, detail="Taking a closer look")


SPECS = (
    ActionSpec("click", "control", "Clicking {text}", click, preview=preview_click,
               args='{"id"} or {"text", "near"?} or {"x","y"}, "double"?, "button"?: right'),
    ActionSpec("scroll", "control", "Scrolling {direction}", scroll,
               args='{"direction": down|up|left|right, "amount"?: pages | "all", "x","y"? | "id"?}'),
    ActionSpec("scroll_to", "control", "Looking for {text}", scroll_to, args='{"text", "direction"?}'),
    ActionSpec("press", "control", "Pressing {keys}", press, preview=preview_press,
               args='{"keys": "cmd+t" | "return" | "pagedown", "times"?}'),
    ActionSpec("drag", "control", "Dragging", drag, preview=preview_drag, args='{"from_id", "to_id"}'),
    ActionSpec("wait", "control", "Waiting a moment", wait, args='{"seconds"}'),
    ActionSpec("look", "control", "Taking a closer look", look, args="{}"),
)
