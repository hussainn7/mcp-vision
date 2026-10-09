"""Plip's hands: click, scroll, press keys, drag, on whatever is on screen.

Targets come from the numbered screen map (``{"id": 7}``), a visible label
(``{"text": "Export"}``) or screenshot pixels (``{"x": 410, "y": 88}``).
Numbers and labels are exact and cost a few tokens; pixels are the fallback.

``scroll_to`` is the token saver: it has the app scroll the text into view
itself (found anywhere in the page, scrolled-out parts too), or scrolls and
re-reads the Accessibility map locally until it shows up, so "find the pricing
section" takes one model turn instead of one per page.

A scroll aims at the panel they mean: the one Plip last clicked in (while
they're still on that page and haven't moved the pointer), else the focused
one, the pointer, not just the biggest. Every scroll checks itself: the
pointer goes there first, then the pixels around that spot and the map,
before and after. When nothing moved it tries the panel's scroll bar, other
spots and page keys before saying so, and the result says which panel moved,
what worked or everything it tried, so the model never has to ask them to
scroll for it. After a scroll the numbers in the old map point at
the wrong places; a click by number then re-finds the same label on the fresh
map instead of clicking where the thing used to be.

Clicks on things that spend money, send, delete or submit ask first.
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
# Keys that quit, delete, log out or send (cmd+return sends in mail, chat and comment boxes), by what they press,
# so "command+q", "⌘+q" or "shift+cmd+delete" are caught as well as "cmd+q".
RISKY_KEYS = {parse_keys(keys) for keys in ("cmd+q", "cmd+alt+esc", "cmd+delete", "cmd+shift+delete", "cmd+return",
                                            "cmd+shift+return", "cmd+shift+d", "cmd+shift+q", "cmd+alt+q",
                                            "cmd+shift+alt+q")}
PAGE_LINES = 8                 # wheel steps (x3 lines each) per "page"
MAX_SCROLL_TO = 15


def _screen(ctx: ActionContext):
    shots, context = ctx.screen if isinstance(ctx.screen, tuple) else ([], None)
    return list(shots or []), context


def resolve(ctx: ActionContext, args: dict, *, key: str = "", refind: bool = True) -> tuple[float, float, str]:
    """A target in args -> (global x, global y, label).

    ``refind``: after a scroll, look a numbered control up again by its label (clicks); scrolling
    itself can aim at the old spot, since a scroll area doesn't move when its content does.
    """
    prefix = f"{key}_" if key else ""
    shots, context = _screen(ctx)
    raw_id = args.get(f"{prefix}id", args.get(key) if key and isinstance(args.get(key), int) else None)
    if ctx.state.get("stale_map") and (raw_id is not None or f"{prefix}x" in args):
        # An earlier step in the same reply loaded something new: those numbers point at what used to be there.
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
            # The page scrolled since that map: find the same thing where it is now.
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
        # Named by what it lands on, not what the model calls it: "Buy now" clicked by x,y still asks first.
        return gx, gy, _under(ctx, context, gx, gy) or str(args.get("label") or "there")
    raise ActionError("Tell me what to click: a number from the screen map, its label, or x and y.",
                      hint='give "id", "text" or "x" and "y"')


def _norm(label: str) -> str:
    return " ".join(label.lower().split())


def _refind(ctx: ActionContext, control):
    """The same control on a fresh map after a scroll.

    A unique label is enough. For a repeated one ("Add to cart" on every row), work out how far
    the page moved from labels that appear once in both maps, and take the copy that sits where
    the old one should have moved to.
    """
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
    """Label -> the one control or text with it (labels that show up more than once left out)."""
    counts: dict[str, list] = {}
    for item in [*context.controls, *context.texts]:
        counts.setdefault(_norm(item.label), []).append(item)
    return {label: items[0] for label, items in counts.items() if len(items) == 1 and label}


def _shift(old, fresh) -> tuple[float, float] | None:
    """How far the page moved between two maps: the median move of labels that appear once in both."""
    before, after = _unique(old), _unique(fresh)
    moves = sorted((after[label].x - item.x, after[label].y - item.y) for label, item in before.items()
                   if label in after)
    if not moves:
        return None
    dxs, dys = sorted(move[0] for move in moves), sorted(move[1] for move in moves)
    return dxs[len(dxs) // 2], dys[len(dys) // 2]


def _nearest(context, text: str, near: str):
    """The ``text`` control closest to the ``near`` label: the "Add to cart" in that product's row."""
    anchor = context.find(near)
    if anchor is None:
        return None
    needle = _norm(text)
    matches = [item for item in [*context.controls, *context.texts] if needle in _norm(item.label)]
    if not matches:
        return None
    # Same row or just below the anchor reads as "its" button; above it belongs to the previous item.
    return min(matches, key=lambda item: abs(item.x - anchor.x) * 0.3 + abs(item.y - anchor.y)
               + (60 if item.y < anchor.y - 8 else 0))


def _under(ctx: ActionContext, context, x: float, y: float, reach: float = 40.0) -> str:
    """The label of the control a point lands on: the smallest one whose frame holds it (a wide "Place your order"
    button clicked near its edge too), else the nearest center within ``reach`` points for controls without a size,
    else ""."""
    for candidate in (context, None):
        if candidate is None:
            try:
                candidate = ctx.observe()             # the map the model saw may be gone: read it fresh
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
    # what it lands on, and what the model says it is (a page the map can't see names nothing under the point)
    if not (RISKY.search(label) or RISKY.search(str(args.get("label") or ""))):
        return None
    return Preview(title=f"Click “{label[:40]}”", lines=["This one can't be undone, so I'm checking first."],
                   confirm="Click it", state=(x, y, label))


OPENING_ROLES = {"link", "tab", "menu item", "menu bar item", "row", "cell", "outline row"}


def _role(ctx: ActionContext, args: dict) -> str:
    """What kind of control a click targets (by its map number or label), if the map says."""
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
# Where the wheel goes, best guess first: the panel they last clicked in, the focused one, the pointer if they
# moved it, the biggest area, the window. The pointer goes there first, then a scroll counts as moved when the
# pixels around that spot change or something on the map shifts. If nothing moved: that panel's own scroll bar,
# the wheel at two more spots, then page keys where they scroll (never into a field, a slider or a menu). The
# report says what worked, or every way it tried.
MOVED = 2.5                   # mean gray-level change around the spot (0-255) that means the content moved
NEAR = 40                     # points: two spots this close wheel the same thing
SHIFT = 6                     # points a label has to move on the map to count as scrolled
_EDGES = {"down": "bottom", "up": "top", "left": "left edge", "right": "right edge"}
_OTHER_WAY = {"down": "up", "up": "down", "left": "right", "right": "left"}
# focus where page keys scroll: in a field they'd type, in a slider, stepper, menu or list they'd change it
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
    aside: bool = False         # a guess that isn't the main area: the report names it
    raw: bool = False           # a point (where they clicked, the pointer), not a panel's middle


class _Click(NamedTuple):
    """Where Plip last clicked, and on what: a plain "scroll up" next means that panel."""

    x: float
    y: float
    app: str
    window: str                 # its title, digits masked (an unread count isn't another page)
    url: str


def _ask(ctx: ActionContext, name: str, *args, default=None, **kwargs):
    """A host question the scroll fallbacks need; ``default`` when this host can't answer (or it fails)."""
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
    """The innermost scroll area under a point: a panel inside the page is smaller than the page."""
    holding = [area for area in areas if area.w > 0 and area.h > 0
               and abs(x - area.x) <= area.w / 2 and abs(y - area.y) <= area.h / 2]
    return min(holding, key=lambda area: area.w * area.h, default=None)


def _named(area) -> str:
    name = _SCROLLED_TO.sub("", area.label).strip()      # "Inbox (scrolled 40% down)" -> "Inbox", "Inbox (3)" stays
    if area.role == "page" or name in {"", "page"}:
        return "the page"
    return "the scroll area" if name == "scroll area" else f"the {name[:30]!r} panel"


def _remember(ctx: ActionContext, x: float, y: float, role: str) -> None:
    """Plip left the pointer here, and a plain "scroll up" next means this panel, unless the click switched what
    the window shows (a tab, a menu, a sidebar entry like Mail's mailboxes): then it's that content they mean."""
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
    """A narrow column down the window's left edge: Mail's mailboxes, a web app's folder list."""
    if frame is None or frame.width < 600:
        return False
    biggest = max(areas, key=lambda area: area.w * area.h, default=None)
    inner = _deepest(areas, x, y)
    if inner is not None and inner is not biggest:
        return inner.w <= frame.width * 0.3 and inner.x - inner.w / 2 <= frame.x + 24
    return x - frame.x <= min(300.0, frame.width * 0.2)


def _clicked(ctx: ActionContext, now, frame, *, moved_on: bool) -> _Click | None:
    """The last click while a plain scroll still means its panel: the pointer's where Plip left it (else they've
    moved on), and it's the same app, window and page, or one opened from it (an email from the list)."""
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
    """Where to wheel, best guess first. A named target (the id of anything inside the panel, or its x,y), then
    the middle of the panel it sits in; else the guesses."""
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
            spots.append(_Spot(inner.x, inner.y, _named(inner), aside=True))     # the side panel, mid-panel
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
    """Each spot once: a few points from an earlier one, or in the same panel, wheels the same thing. A point in
    the page itself (where they clicked, the pointer) may be over a panel the map can't see, so the page's middle
    is still worth a go after it."""
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
    """The map says it scrolled: something on both maps sits elsewhere now, or most of the text was swapped (a
    list that reuses its rows). A toolbar that shows on hover or a ticking clock is neither."""
    if before is None or after is None or before.empty or after.empty:
        return False
    old, new = _unique(before), _unique(after)
    if any(abs(new[label].x - item.x) >= SHIFT or abs(new[label].y - item.y) >= SHIFT
           for label, item in old.items() if label in new):
        return True
    was, now = {_norm(item.label) for item in before.texts}, {_norm(item.label) for item in after.texts}
    return len(was - now) >= 3 and len(now - was) >= 3


def _moved(ctx: ActionContext, seen, way: _Way, pixels: list, tries: int):
    """Did a push move anything: the pixels at the spots it watches (every 0.1 s), and the map against ``seen``
    (read once at the end; polled when there are no pixels to watch). (yes/no, the map after: None when nothing
    read it). None for yes/no: nothing to tell by (a blind app off every screen), so it gets the benefit of the
    doubt."""
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
    """The pointer onto the spot before the 'before' look, so a row lighting up or a toolbar showing under it
    isn't taken for a scroll. True when it moved."""
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
    """Aim, then the 'before' look: the pixels it watches, and the map if this way checks it (the first, page
    keys, or no pixels to watch), read again when the pointer moved since. (map, pixels, mapped, still stale)."""
    stale = way.aim() or stale
    pixels = [_pixels(ctx, x, y) for x, y in way.watch]
    mapped = seen is not None and (first or way.wide or all(shot is None for shot in pixels))
    if mapped and stale:
        seen, stale = ctx.observe() or seen, False
        pixels = [_pixels(ctx, x, y) for x, y in way.watch]
    return seen, pixels, mapped, stale


@dataclass
class _Way:
    """One way to scroll a step: the wheel at a spot, that panel's scroll bar, or a page key."""

    what: str                                   # "the wheel at the 'Inbox' panel", "its scroll bar", "pressing pageup"
    watch: list[tuple[float, float]]            # where to look for it moving
    push: Callable[[str], bool]                 # once, toward a direction. False: nothing to do it with
    aim: Callable[[], bool] = lambda: False     # before the 'before' look: True when the pointer moved
    jumps: bool = False                         # one push goes all the way (the scroll bar's value, home/end)
    spot: _Spot | None = None                   # the spot it scrolls, for the report
    wide: bool = False                          # it moves whatever has focus: check the whole map too


def _ways(ctx: ActionContext, spots: list[_Spot], direction: str, lines: int, seen=None, *, to_end: bool = False):
    """The wheel at the best spot, that spot's own scroll bar (cheap and sure), the wheel at two more spots, then
    page keys if the focus is somewhere they scroll."""
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
    within = frame if first.aside else None            # a side panel's own bar, never the whole page's
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
    moved: bool | None = False                  # None: nothing to tell by, the wheel is trusted
    way: _Way | None = None                     # what moved it
    tried: list[str] = field(default_factory=list)
    seen: Any = None                            # the map after
    end: str = ""                               # nothing moved, and that panel says it's already at this end


def _push(ctx: ActionContext, ways, direction: str, seen) -> _Push:
    """Each way in turn until something moves. The first gets 0.4 s and the map; the fallbacks 0.2 s of pixels
    (the map too for page keys, or when there are no pixels to watch)."""
    tried: list[str] = []
    stale = False                               # the pointer moved since ``seen`` was read
    for way in ways:
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
    """The map says the scroll area there is already as far as it goes that way (from its scroll bar)."""
    area = _deepest(seen.scroll_areas, x, y) if seen is not None else None
    says = {"down": "to the bottom)", "up": "(at the top)"}.get(direction)
    return area is not None and says is not None and says in area.label


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else "nothing"


def _how(pushed: _Push) -> str:
    """' (the wheel at the page did nothing; its scroll bar did)' when the plain wheel wasn't enough."""
    return f" ({_listed(pushed.tried)} did nothing; {pushed.way.what} did)" if pushed.tried and pushed.way else ""


def _said(what: str, pushed: _Push, then: str = "") -> str:
    """'scrolled down', or "scrolled the 'Email' panel down; for another …" when it guessed a side panel."""
    spot = pushed.way.spot if pushed.way is not None else None
    if spot is None or not spot.aside:
        return f"scrolled {what}{_how(pushed)}{then}"
    return (f"scrolled {spot.where} {what}{_how(pushed)}{then}; for another panel give its x,y or the id of "
            "anything inside it")


def _stuck(direction: str, pushed: _Push) -> str:
    """Nothing moved: one line the model can act on, never "ask them to scroll"."""
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
    """Keep going until it stops: the top or bottom in one action. Says so when it never moved or never stopped."""
    edge = _EDGES[direction]
    pushed = _push(ctx, _ways(ctx, spots, direction, 60, seen, to_end=True), direction, seen)
    if pushed.moved is False:
        return ActionResult(report=_stuck(direction, pushed), look_after=0.1, detail="Nothing moved")
    ctx.state["scrolled"] = True
    if pushed.moved is None:
        return ActionResult(report=f"scrolled toward the {edge}; i can't tell from here if it's all the way",
                            look_after=0.3, detail=f"Scrolled toward the {edge}")
    way, seen, stopped = pushed.way, pushed.seen, pushed.way.jumps     # no map after: the pixels saw it
    for _ in range(0 if stopped else 9):
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
    """Have the app bring ``text`` into view itself: found anywhere in the page's tree, scrolled-out parts
    too, then AXScrollToVisible on it. Confirmed on a fresh map. (what the host said, the control, the map)."""
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
        ctx.state["scrolled"] = True        # it went somewhere, even if the map can't show the text
    now = _ask(ctx, "scroll_to_visible", text, ask=False)       # where it is now: a slow app may be half way
    reveal = now if now is not None and now.found else reveal
    if reveal.at is not None:               # in view, past what the map lists
        ctx.state["scrolled"] = True
        return reveal, Control(text, "text", *reveal.at), seen
    return reveal, None, seen


def scroll_to(ctx: ActionContext, args: dict) -> ActionResult:
    """Bring ``text`` on screen: the app scrolls it into view itself when it can, else the wheel at the panel
    holding it, reading the Accessibility map between steps (no model calls)."""
    text = str(args.get("text") or "").strip()
    if not text:
        raise ActionError("Tell me what to look for.")
    direction = str(args.get("direction") or "down").lower()
    direction = direction if direction in _EDGES else "down"
    either_way = "direction" not in args        # not told which way: hitting the end turns around once
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
        lead = [_Spot(*reveal.scroller, "the panel it's in", raw=True)]    # the app ignored the ask: wheel there
        if reveal.direction in _EDGES:
            direction, either_way = reveal.direction, False
    spots = _spots(ctx, {key: value for key, value in args.items() if key in {"id", "x", "y"}}, seen, lead)
    ways = _ways(ctx, spots, direction, PAGE_LINES, seen)
    way, start, tried, stale = next(ways), direction, [], False
    scrolls, still, turned, moved_ever, ended = 0, 0, False, False, False
    while found is None and way is not None and scrolls < limit * (2 if either_way else 1):
        seen, pixels, _, stale = _before(ctx, way, seen, stale, first=True)     # it reads the map for the text anyway
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
        if either_way and not turned:           # the end, and it wasn't that way: look the other way
            direction, turned, still = _OTHER_WAY[direction], True, 0
        elif moved_ever or _at_end(seen, *way.watch[0], direction):
            ended = True                        # the end (both ends): it moved, or its scroll bar says so
            break
        else:
            tried.append(way.what)              # this never moved anything: the next way
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
    """Dragging onto the Trash (or a delete zone) throws things away: ask first."""
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
    # Up to that long, and over as soon as the screen holds still (a beat at least), not a blind sleep.
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
