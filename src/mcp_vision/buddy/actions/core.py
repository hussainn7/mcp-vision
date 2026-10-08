"""Everyday skills: apps, links, files, the desktop, system toggles, Shortcuts, text, reminders, timers, flights."""
from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import re
import shutil
import time
import urllib.parse
from pathlib import Path

from mcp_vision.buddy.actions.base import ActionContext, ActionError, ActionResult, ActionSpec, Preview
from mcp_vision.buddy.actions.host import applescript_string

APP_ALIASES = {
    "chrome": "google chrome", "vscode": "visual studio code", "vs code": "visual studio code",
    "code": "visual studio code", "settings": "system settings", "system preferences": "system settings",
    "preferences": "system settings", "word": "microsoft word", "excel": "microsoft excel",
    "powerpoint": "microsoft powerpoint", "outlook": "microsoft outlook", "teams": "microsoft teams",
    "imessage": "messages", "texts": "messages", "itunes": "music", "app store": "app store",
    "files": "finder", "browser": "safari", "mail app": "mail", "calculator app": "calculator",
}


def _need(args: dict, key: str, what: str) -> str:
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ActionError(f"I need {what}.")
    return str(value).strip()


# -- apps, links ---------------------------------------------------------------------------

def match_app(name: str, apps: dict[str, str]) -> str | None:
    wanted = " ".join(name.lower().replace(".app", "").split())
    wanted = APP_ALIASES.get(wanted, wanted)
    if wanted in apps:
        return apps[wanted]
    starts = sorted((app for app in apps if app.startswith(wanted)), key=len)
    if starts:
        return apps[starts[0]]
    contains = sorted((app for app in apps if wanted in app), key=len)
    if contains:
        return apps[contains[0]]
    close = difflib.get_close_matches(wanted, list(apps), n=1, cutoff=0.72)
    return apps[close[0]] if close else None


def open_app(ctx: ActionContext, args: dict) -> ActionResult:
    name = _need(args, "name", "the app's name")
    path = match_app(name, ctx.host.list_apps())
    if path is None:
        try:
            ctx.host.open_app(name)                     # LaunchServices may still know it
        except Exception as exc:
            raise ActionError(f"I couldn't find an app called {name}.") from exc
        return ActionResult(detail=name, settle=3.0, opens=True)
    ctx.host.open_app(path)
    return ActionResult(detail=Path(path).stem, settle=3.0, opens=True)


def open_url(ctx: ActionContext, args: dict) -> ActionResult:
    url = _need(args, "url", "a link")
    if not re.match(r"^(https?://|mailto:)", url):
        url = "https://" + url.lstrip("/") if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url) else ""
    if not url:
        raise ActionError("That doesn't look like a web link.")
    ctx.host.open(url)
    return ActionResult(detail=urllib.parse.urlparse(url).netloc or url, settle=5.0, opens=True)


def web_search(ctx: ActionContext, args: dict) -> ActionResult:
    query = _need(args, "query", "something to search for")
    ctx.host.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(query))
    return ActionResult(detail=query, settle=4.0, opens=True)


# -- files -----------------------------------------------------------------------------------

def search_files(ctx: ActionContext, args: dict) -> ActionResult:
    query = _need(args, "query", "what to look for")
    kind = str(args.get("kind") or "").lower()
    hits = ctx.host.find_files(query, kind, limit=8)
    ctx.state["files"] = [hit.path for hit in hits]
    items = [hit.as_item(ctx.host.home) for hit in hits]
    if not hits:
        return ActionResult(report=f'no files matched "{query}"', detail="nothing found")
    lines = []
    for index, item in enumerate(items, start=1):
        when = dt.datetime.fromtimestamp(item["modified"]).strftime("%b %-d %Y")
        lines.append(f'{index}. {item["title"]} in {item["detail"]} (changed {when})')
    return ActionResult(report=f'files matching "{query}":\n' + "\n".join(lines), items=items,
                        detail=f"{len(hits)} found")


def _file_from(ctx: ActionContext, args: dict) -> str:
    files = ctx.state.get("files") or []
    if args.get("index") is not None:
        try:
            return files[int(args["index"]) - 1]
        except (ValueError, IndexError) as exc:
            raise ActionError("I don't have that file from the last search.") from exc
    path = os.path.expanduser(_need(args, "path", "which file"))
    real = os.path.realpath(path)
    if not os.path.exists(real):
        raise ActionError("That file isn't there anymore.")
    if real not in {os.path.realpath(item) for item in files} and not real.startswith(os.path.realpath(ctx.host.home)):
        raise ActionError("I only open files in your home folder.")
    return real


def open_file(ctx: ActionContext, args: dict) -> ActionResult:
    path = _file_from(ctx, args)
    ctx.host.open(path)
    return ActionResult(detail=os.path.basename(path), settle=3.0)


def reveal_file(ctx: ActionContext, args: dict) -> ActionResult:
    path = _file_from(ctx, args)
    ctx.host.reveal(path)
    return ActionResult(detail=os.path.basename(path), settle=3.0)


# -- desktop ------------------------------------------------------------------------------------

DESKTOP_GROUPS = (
    ("Screenshots", None),
    ("Images", {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp", ".tiff", ".tif", ".svg", ".bmp"}),
    ("Documents", {".pdf", ".doc", ".docx", ".pages", ".txt", ".rtf", ".md", ".key", ".ppt", ".pptx",
                   ".numbers", ".xls", ".xlsx", ".csv", ".epub"}),
    ("Videos", {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"}),
    ("Audio", {".mp3", ".wav", ".m4a", ".aiff", ".flac", ".aac"}),
    ("Archives", {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2"}),
    ("Installers", {".dmg", ".pkg", ".app"}),
    ("Code", {".py", ".js", ".ts", ".tsx", ".json", ".html", ".css", ".swift", ".ipynb", ".sh", ".yaml", ".yml"}),
)
SCREENSHOT_RE = re.compile(r"^(screenshot|screen shot|cleanshot|screen recording)", re.IGNORECASE)
GROUP_NAMES = {name for name, _ in DESKTOP_GROUPS} | {"Other"}


def desktop_dir(ctx: ActionContext) -> Path:
    return Path(ctx.state.get("desktop") or os.path.join(ctx.host.home, "Desktop"))


def group_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if SCREENSHOT_RE.match(path.name) and suffix in {".png", ".jpg", ".jpeg", ".heic", ".mov", ".mp4", ".gif"}:
        return "Screenshots"
    for name, extensions in DESKTOP_GROUPS:
        if extensions and suffix in extensions:
            return name
    return "Other"


def plan_desktop(desktop: Path, older_than_days: float | None = None) -> dict[str, list[str]]:
    plan: dict[str, list[str]] = {}
    cutoff = time.time() - older_than_days * 86400 if older_than_days else None
    for entry in sorted(desktop.iterdir()) if desktop.is_dir() else []:
        if entry.name.startswith(".") or entry.name in GROUP_NAMES or entry.is_symlink():
            continue
        if entry.is_dir() and entry.suffix.lower() != ".app":
            continue                                    # leave the user's own folders alone
        if cutoff and entry.stat().st_mtime > cutoff:
            continue
        plan.setdefault(group_for(entry), []).append(entry.name)
    return plan


def preview_organize(ctx: ActionContext, args: dict) -> Preview:
    desktop = desktop_dir(ctx)
    plan = plan_desktop(desktop, args.get("older_than_days"))
    total = sum(len(names) for names in plan.values())
    if not total:
        raise ActionError("Your desktop's already tidy.")
    order = [name for name, _ in DESKTOP_GROUPS] + ["Other"]
    lines = [f"{group}: {len(names)}" for group, names in
             sorted(plan.items(), key=lambda item: (-len(item[1]), order.index(item[0])))]
    return Preview(title=f"Tidy {total} files into {len(plan)} folders", lines=lines, confirm="Tidy up", state=plan)


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix, count = target.stem, target.suffix, 2
    while target.exists():
        target = folder / f"{stem} {count}{suffix}"
        count += 1
    return target


def organize_desktop(ctx: ActionContext, args: dict, plan: dict | None = None) -> ActionResult:
    desktop = desktop_dir(ctx)
    plan = plan if plan is not None else plan_desktop(desktop, args.get("older_than_days"))
    moves = []
    for group, names in plan.items():
        folder = desktop / group
        folder.mkdir(exist_ok=True)
        for name in names:
            source = desktop / name
            if not source.exists():
                continue
            target = _free_name(folder, name)
            shutil.move(str(source), str(target))
            moves.append([str(source), str(target)])
    folders = len({Path(target).parent.name for _, target in moves})
    return ActionResult(say=f"Done. I tidied {len(moves)} files into {folders} folders. Say undo if you want them back.",
                        detail=f"{len(moves)} files moved", undo={"kind": "moves", "moves": moves},
                        report=f"moved {len(moves)} desktop files into {folders} folders")


def undo(ctx: ActionContext, args: dict) -> ActionResult:
    history = ctx.state.setdefault("undo", [])
    if not history:
        raise ActionError("There's nothing for me to undo.")
    record = history.pop()
    if record.get("kind") == "moves":
        restored = 0
        for source, target in reversed(record["moves"]):
            if os.path.exists(target) and not os.path.exists(source):
                shutil.move(target, source)
                restored += 1
        for folder in {os.path.dirname(target) for _, target in record["moves"]}:
            try:
                os.rmdir(folder)                        # only removes folders we emptied
            except OSError:
                pass
        return ActionResult(say=f"Put {restored} files back where they were.", detail=f"{restored} restored")
    raise ActionError("I can't undo that one.")


# -- system ---------------------------------------------------------------------------------------

def system(ctx: ActionContext, args: dict) -> ActionResult:
    setting = _need(args, "setting", "which setting").lower().replace(" ", "_")
    value = args.get("value")
    if setting in {"dark_mode", "dark", "appearance"}:
        target = {"on": "true", "true": "true", "dark": "true", "off": "false", "false": "false",
                  "light": "false"}.get(str(value).lower(), "not dark mode")
        ctx.host.osascript("tell application \"System Events\" to tell appearance preferences to "
                           f"set dark mode to {target}")
        return ActionResult(detail="appearance updated")
    if setting == "volume":
        current = None
        if str(value).lower() in {"up", "down"}:
            current = int(ctx.host.osascript("output volume of (get volume settings)") or 50)
            level = current + (15 if str(value).lower() == "up" else -15)
        else:
            try:
                level = int(float(value))
            except (TypeError, ValueError) as exc:
                raise ActionError("Tell me a volume from zero to a hundred.") from exc
        level = max(0, min(100, level))
        ctx.host.osascript(f"set volume output volume {level}")
        return ActionResult(detail=f"volume {level}")
    if setting == "mute":
        muted = str(value).lower() not in {"off", "false", "unmute", "no"}
        ctx.host.osascript(f"set volume {'with' if muted else 'without'} output muted")
        return ActionResult(detail="muted" if muted else "unmuted")
    if setting in {"sleep_display", "sleep_screen", "lock"}:
        ctx.host.osascript('do shell script "pmset displaysleepnow"')
        return ActionResult(detail="display asleep")
    raise ActionError(f"I can't change {setting.replace('_', ' ')} yet.")


# -- shortcuts & text ----------------------------------------------------------------------------

def run_shortcut(ctx: ActionContext, args: dict) -> ActionResult:
    name = _need(args, "name", "the shortcut's name")
    available = ctx.host.shortcuts()
    match = next((item for item in available if item.lower() == name.lower()), None)
    if match is None:
        close = difflib.get_close_matches(name, available, n=1, cutoff=0.6)
        match = close[0] if close else None
    if match is None:
        raise ActionError(f"I couldn't find a shortcut called {name}.")
    output = ctx.host.run_shortcut(match)
    return ActionResult(detail=match, report=f"the {match} shortcut said: {output[:600]}" if output else "")


def list_shortcuts(ctx: ActionContext, args: dict) -> ActionResult:
    names = ctx.host.shortcuts()
    if not names:
        return ActionResult(report="the user has no shortcuts", detail="none")
    return ActionResult(report="the user's shortcuts: " + ", ".join(names[:60]), detail=f"{len(names)} shortcuts")


def type_text(ctx: ActionContext, args: dict) -> ActionResult:
    text = _need(args, "text", "what to type")
    ctx.host.type_text(text)
    return ActionResult(detail=f"{len(text)} characters")


def replace_selection(ctx: ActionContext, args: dict) -> ActionResult:
    text = _need(args, "text", "the new text")
    context = ctx.screen[1] if isinstance(ctx.screen, tuple) and len(ctx.screen) > 1 else None
    if getattr(context, "selection_cut", False):
        # It only saw the start: swapping the whole selection for a rewrite of that part would lose the rest.
        raise ActionError("That's more text than I can rewrite in one go. Select a smaller part and ask me again.")
    ctx.host.replace_selection(text)
    return ActionResult(detail="text replaced")


# -- reminders, notes, timers ----------------------------------------------------------------

def parse_when(value: str | None, now: dt.datetime | None = None) -> dt.datetime | None:
    if not value:
        return None
    now = now or dt.datetime.now()
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            when = dt.datetime.strptime(text[:19], pattern)
            return when.replace(hour=9) if pattern == "%Y-%m-%d" else when
        except ValueError:
            continue
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if match:
        when = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        return when if when > now else when + dt.timedelta(days=1)
    raise ActionError("Give me the time as a date and time, like tomorrow at nine.")


def _applescript_date(when: dt.datetime) -> str:
    return ("set dueDate to current date\n"
            f"set year of dueDate to {when.year}\nset month of dueDate to {when.month}\n"
            f"set day of dueDate to {when.day}\nset hours of dueDate to {when.hour}\n"
            f"set minutes of dueDate to {when.minute}\nset seconds of dueDate to 0\n")


def create_reminder(ctx: ActionContext, args: dict) -> ActionResult:
    title = _need(args, "title", "what to remind you about")
    when = parse_when(args.get("due"))
    props = f"name:{applescript_string(title)}" + (", due date:dueDate" if when else "")
    script = (_applescript_date(when) if when else "") + \
        f'tell application "Reminders" to make new reminder with properties {{{props}}}'
    ctx.host.osascript(script)
    return ActionResult(detail=title + (when.strftime(" · %b %-d, %-I:%M %p") if when else ""))


def create_note(ctx: ActionContext, args: dict) -> ActionResult:
    title = _need(args, "title", "a title for the note")
    body = str(args.get("body") or "")
    html = f"<h1>{_html(title)}</h1>" + "".join(f"<div>{_html(line) or '<br>'}</div>" for line in body.splitlines())
    ctx.host.osascript(f'tell application "Notes" to make new note with properties {{body:{applescript_string(html)}}}')
    return ActionResult(detail=title)


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def set_timer(ctx: ActionContext, args: dict) -> ActionResult:
    try:
        minutes = float(args.get("minutes") or 0) + float(args.get("seconds") or 0) / 60
    except (TypeError, ValueError) as exc:
        raise ActionError("How long should the timer be?") from exc
    if not 0 < minutes <= 24 * 60:
        raise ActionError("How long should the timer be?")
    label = str(args.get("label") or "").strip()
    name = f"{label} timer" if label else "timer"

    def ring() -> None:
        ctx.host.notify("Plip", f"Your {name} is done.")
        ctx.announce(f"Your {name} is done.")
    ctx.schedule(minutes * 60, ring)
    shown = f"{minutes:g} min" if minutes >= 1 else f"{round(minutes * 60)} sec"
    return ActionResult(detail=f"{shown}" + (f" · {label}" if label else ""))


# -- travel -----------------------------------------------------------------------------------------

def flights_url(origin: str, destination: str, depart: str, back: str | None = None,
                adults: int = 1, cabin: str = "") -> str:
    query = f"Flights from {origin} to {destination} on {depart}"
    if back:
        query += f" returning {back}"
    else:
        query += " one way"
    if adults and int(adults) > 1:
        query += f" for {int(adults)} adults"
    if cabin and cabin.lower() not in {"economy", "coach"}:
        query += f" {cabin} class"
    return "https://www.google.com/travel/flights?hl=en&q=" + urllib.parse.quote(query)


def find_flights(ctx: ActionContext, args: dict) -> ActionResult:
    origin = _need(args, "from", "where you're flying from")
    destination = _need(args, "to", "where you're going")
    depart = _need(args, "depart", "the departure date")
    back = args.get("return") or args.get("back")
    url = flights_url(origin, destination, depart, back, int(args.get("adults") or 1), str(args.get("cabin") or ""))
    ctx.host.open(url)
    trip = f"{origin} to {destination}, {depart}" + (f" to {back}" if back else "")
    return ActionResult(detail=trip, look_after=5.0,
                        report=f"opened google flights for {trip}. look at the screen now: tell the user the best "
                               "two or three options (price, airline, times, stops) and point at the cheapest good one. "
                               "if it's still loading, say so briefly.")


SPECS = (
    ActionSpec("open_app", "apps", "Opening {name}", open_app, args='{"name"}'),
    ActionSpec("open_url", "apps", "Opening {url}", open_url, args='{"url"}'),
    ActionSpec("web_search", "apps", "Searching the web for {query}", web_search, args='{"query"}'),
    ActionSpec("search_files", "files", "Searching files for {query}", search_files,
               args='{"query", "kind"?: pdf|image|document|video|audio|archive}'),
    ActionSpec("open_file", "files", "Opening the file", open_file, args='{"index"} or {"path"}'),
    ActionSpec("reveal_file", "files", "Showing it in Finder", reveal_file, args='{"index"} or {"path"}'),
    ActionSpec("organize_desktop", "files", "Tidying your desktop", organize_desktop,
               preview=preview_organize, args='{"older_than_days"?}'),
    ActionSpec("undo", "files", "Undoing that", undo, args="{}"),
    ActionSpec("system", "system", "Changing {setting}", system,
               args='{"setting": dark_mode|volume|mute|sleep_display, "value"}'),
    ActionSpec("run_shortcut", "system", "Running {name}", run_shortcut, args='{"name"}'),
    ActionSpec("list_shortcuts", "system", "Checking your shortcuts", list_shortcuts, args="{}"),
    ActionSpec("type_text", "writing", "Typing it out", type_text, args='{"text"}'),
    ActionSpec("replace_selection", "writing", "Rewriting your selection", replace_selection, args='{"text"}'),
    ActionSpec("create_reminder", "planning", "Reminder: {title}", create_reminder, args='{"title", "due"?}'),
    ActionSpec("create_note", "planning", "Note: {title}", create_note, args='{"title", "body"}'),
    ActionSpec("set_timer", "planning", "Timer for {minutes} min", set_timer, args='{"minutes", "label"?}'),
    ActionSpec("find_flights", "travel", "Flights {from} → {to}", find_flights,
               args='{"from", "to", "depart": "YYYY-MM-DD", "return"?, "adults"?}'),
)


def as_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)
