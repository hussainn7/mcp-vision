"""Shared fakes for Plip's companion-level tests."""
from __future__ import annotations

from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot

PRIMARY = Rect(0, 0, 1512, 982)


def shot(index=1, size=(1280, 831), cursor=True) -> Screenshot:
    screen = ScreenInfo(index=index, frame=PRIMARY, scale=2.0, is_cursor_screen=cursor)
    return Screenshot(screen=screen, data=b"\xff\xd8jpeg", width=size[0], height=size[1])


class ScriptedBrain:
    """Replies with the next scripted answer on each call, streamed in small chunks."""

    name = "scripted"
    label = "Scripted"
    kind = "api"
    vision = True

    def __init__(self, *replies: str, chunk: int = 5):
        self.replies = list(replies)
        self.chunk = chunk
        self.calls = []
        self.systems = []

    async def stream(self, *, system, turns, detailed=False):
        self.calls.append(turns)
        self.systems.append(system)
        reply = self.replies.pop(0) if self.replies else "okay."
        for start in range(0, len(reply), self.chunk):
            yield reply[start:start + self.chunk]


class Speaker:
    def __init__(self):
        self.said, self.stopped = [], 0

    def speak(self, text):
        self.said.append(text)

    async def drain(self):
        return None

    def stop(self):
        self.stopped += 1


class Pointer:
    def __init__(self):
        self.events = []

    def set_state(self, state, detail=""):
        self.events.append(("state", state))

    def point(self, x, y, label):
        self.events.append(("point", round(x), round(y), label))

    def release(self):
        self.events.append(("release",))


class Capturer:
    def __init__(self, shots=None):
        self.shots = shots if shots is not None else [shot()]
        self.captures = 0

    def screens(self):
        return [item.screen for item in self.shots]

    def capture(self, *, only_cursor_screen=False):
        self.captures += 1
        return list(self.shots)


class Events:
    """Collects observer events: ``events.of("step")`` returns the data dicts."""

    def __init__(self):
        self.items = []

    def __call__(self, kind, data):
        self.items.append((kind, data))

    def of(self, kind):
        return [data for name, data in self.items if name == kind]


class FakeHost:
    """Records what actions asked the platform to do."""

    name = "fake"

    def __init__(self, home="/Users/test", apps=None, shortcuts=None, osa_reply=""):
        self.home = home
        self.calls = []
        self.pointed = []             # where the real pointer went: clicks, scrolls and hovers all move it
        self.apps = apps if apps is not None else {
            "safari": "/Applications/Safari.app", "google chrome": "/Applications/Google Chrome.app",
            "visual studio code": "/Applications/Visual Studio Code.app", "messages": "/System/Applications/Messages.app",
            "system settings": "/System/Applications/System Settings.app", "notes": "/System/Applications/Notes.app",
            "slack": "/Applications/Slack.app", "spotify": "/Applications/Spotify.app"}
        self._shortcuts = shortcuts or ["Morning Routine", "Log Water"]
        self.osa_reply = osa_reply
        self.files = []

    def list_apps(self):
        return self.apps

    def open_app(self, path):
        self.calls.append(("open_app", path))

    def open(self, target):
        self.calls.append(("open", target))

    def reveal(self, path):
        self.calls.append(("reveal", path))

    def find_files(self, query, kind="", limit=8):
        self.calls.append(("find_files", query, kind))
        return self.files[:limit]

    def osascript(self, script, timeout=10.0):
        self.calls.append(("osascript", script))
        return self.osa_reply

    def notify(self, title, text):
        self.calls.append(("notify", title, text))

    def shortcuts(self):
        return list(self._shortcuts)

    def run_shortcut(self, name):
        self.calls.append(("shortcut", name))
        return "ok"

    def type_text(self, text):
        self.calls.append(("type", text))

    def replace_selection(self, text):
        self.calls.append(("replace", text))

    def click(self, x, y, button="left", count=1):
        self.pointed.append((round(x), round(y)))
        self.calls.append(("click", round(x), round(y)) + ((button, count) if (button, count) != ("left", 1) else ()))

    def scroll(self, x, y, dy, dx=0):
        self.pointed.append((round(x), round(y)))
        self.calls.append(("scroll", round(x), round(y), dy, dx))

    # what a scroll asks when the wheel moves nothing (tests set these)
    mouse = None                      # the pointer, global points (where the user left it: it doesn't follow plip)
    focused_area = None               # a Rect: the focused element's scroll area
    focus_role = None                 # "AXTextField", "AXWebArea"…; None: can't tell
    has_bar = False                   # the panel has a scroll bar Plip can move itself
    revealed = None                   # what scroll_to_visible finds (a host.Reveal)
    revealed_now = None               # what it says when asked again without asking the app (default: the same)

    def hover(self, x, y):
        self.pointed.append((round(x), round(y)))

    def mouse_position(self):
        return self.mouse

    def focused_scroll_area(self):
        return self.focused_area

    def focused_role(self):
        return self.focus_role

    def scroll_bar_step(self, x, y, direction, *, to_end=False, pages=1.0, within=None):
        if not self.has_bar:
            return False
        self.calls.append(("scroll_bar", round(x), round(y), direction, to_end))
        return True

    def scroll_to_visible(self, text, *, ask=True):
        from mcp_vision.buddy.actions.host import Reveal

        if not ask:
            return self.revealed_now or self.revealed or Reveal()
        self.calls.append(("scroll_to_visible", text))
        return self.revealed or Reveal()

    def press(self, keys):
        self.calls.append(("press", keys))

    def drag(self, x1, y1, x2, y2):
        self.calls.append(("drag", round(x1), round(y1), round(x2), round(y2)))

    def set_field(self, x, y, value):
        self.calls.append(("field", round(x), round(y), value))
        return True
