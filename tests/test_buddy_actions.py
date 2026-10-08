"""Plip doing things: the [DO]/[PLAN] protocol, every core skill, the engine, and whole turns."""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.parse
from pathlib import Path

import pytest

from buddy_fakes import Capturer, Events, FakeHost, Pointer, ScriptedBrain, Speaker
from mcp_vision.buddy.actions import ActionContext, ActionEngine, ActionLog, answer_kind
from mcp_vision.buddy.actions.core import (
    flights_url, group_for, match_app, parse_when, plan_desktop,
)
from mcp_vision.buddy.actions.host import FileHit, PortableHost
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.pointing import ActionTag, ReplyStream, SpeechChunk


def run(coro):
    return asyncio.run(coro)


def engine(host=None, **kw):
    return ActionEngine(ActionContext(host=host or FakeHost()), **kw)


# -- protocol ---------------------------------------------------------------------------

def stream(text, size):
    reply, events = ReplyStream(), []
    for start in range(0, len(text), size):
        events += reply.feed(text[start:start + size])
    return reply, events + reply.close()


@pytest.mark.parametrize("size", [1, 2, 5, 13, 999])
def test_action_and_plan_tags_survive_any_chunking(size):
    text = ('[STEPS:3] [PLAN: open settings | privacy & security | turn it on] Opening settings. '
            '[DO:open_app {"name": "System Settings"}] Then I\'ll fill it in. '
            '[DO:fill_form {"fields": [{"x": 10, "y": 20, "label": "Name [first]", "value": "Hussain \\"H\\""}]}] '
            'All set. [DO:undo]')
    reply, events = stream(text, size)
    kinds = [type(event).__name__ for event in events]
    assert kinds.count("ActionTag") == 3 and kinds.count("PlanTag") == 1
    assert reply.plan == ("open settings", "privacy & security", "turn it on")
    assert reply.actions[0] == ActionTag("open_app", {"name": "System Settings"})
    assert reply.actions[1].args["fields"][0] == {"x": 10, "y": 20, "label": "Name [first]", "value": 'Hussain "H"'}
    assert reply.actions[2] == ActionTag("undo")
    assert reply.spoken_text == "Opening settings. Then I'll fill it in. All set."
    assert "DO" not in reply.spoken_text and "{" not in reply.spoken_text
    first_action = kinds.index("ActionTag")
    assert isinstance(events[first_action - 1], SpeechChunk)        # "Opening settings." is said first


def test_broken_action_tags_are_never_spoken():
    reply, _ = stream('Sure. [DO:open_app {"name": "Safari"] more words. [DO:x {"a": ', 4)
    assert reply.actions == []
    assert "DO" not in reply.spoken_text and "Safari" not in reply.spoken_text
    assert reply.spoken_text.startswith("Sure.") and "more words." in reply.spoken_text
    reply, _ = stream("[PLAN: ] ok [DO:open_app [nope]] fine", 3)
    assert reply.plan == () and reply.actions == [] and reply.spoken_text == "ok fine"


def test_yes_no_answers():
    for text in ["yes", "Yeah do it", "sure, send it", "go ahead", "OK", "tidy up"]:
        assert answer_kind(text) == "yes", text
    for text in ["no", "nope", "cancel that", "wait", "never mind", "don't"]:
        assert answer_kind(text) == "no", text
    for text in ["what's the weather", "yesterday's file", "nobody knows", "okra recipes"]:
        assert answer_kind(text) == "", text


# -- skills --------------------------------------------------------------------------------

def test_open_app_matches_names_aliases_and_typos():
    apps = FakeHost().apps
    assert match_app("Safari", apps).endswith("Safari.app")
    assert match_app("chrome", apps).endswith("Google Chrome.app")
    assert match_app("vs code", apps).endswith("Visual Studio Code.app")
    assert match_app("settings", apps).endswith("System Settings.app")
    assert match_app("spotfy", apps).endswith("Spotify.app")
    assert match_app("photoshop", apps) is None
    host = FakeHost()
    outcome = run(engine(host).handle("open_app", {"name": "slack"}))
    assert outcome.status == "done" and host.calls == [("open_app", "/Applications/Slack.app")]
    assert outcome.result.detail == "Slack"


def test_open_url_and_web_search_only_open_web_links():
    host = FakeHost()
    e = engine(host)
    assert run(e.handle("open_url", {"url": "github.com/hussainn7"})).status == "done"
    assert run(e.handle("open_url", {"url": "file:///etc/passwd"})).status == "failed"
    assert run(e.handle("open_url", {"url": "javascript:alert(1)"})).status == "failed"
    run(e.handle("web_search", {"query": "best ramen near me"}))
    assert host.calls == [("open", "https://github.com/hussainn7"),
                          ("open", "https://www.google.com/search?q=best+ramen+near+me")]


def make_home(tmp_path):
    home = tmp_path / "home"
    (home / "Documents" / "Taxes").mkdir(parents=True)
    (home / "Downloads").mkdir()
    (home / "Library").mkdir()
    (home / "Documents" / "Taxes" / "Invoice-September.pdf").write_text("x")
    (home / "Downloads" / "invoice_old.pdf").write_text("x")
    (home / "Downloads" / "Invoice notes.txt").write_text("x")
    (home / "Library" / "invoice-cache.pdf").write_text("x")
    old = time.time() - 86400 * 30
    os.utime(home / "Downloads" / "invoice_old.pdf", (old, old))
    return home


def test_portable_file_search_finds_newest_first_and_skips_library(tmp_path):
    home = make_home(tmp_path)
    hits = PortableHost(str(home)).find_files("invoice", "pdf")
    assert [Path(hit.path).name for hit in hits] == ["Invoice-September.pdf", "invoice_old.pdf"]
    assert PortableHost(str(home)).find_files("invoice notes")[0].path.endswith("Invoice notes.txt")


def test_search_then_open_by_index(tmp_path):
    home = make_home(tmp_path)
    host = FakeHost(home=str(home))
    host.files = [FileHit(str(home / "Documents/Taxes/Invoice-September.pdf"), 1759370000.0)]
    e = engine(host)
    outcome = run(e.handle("search_files", {"query": "invoice", "kind": "pdf"}))
    assert outcome.status == "done" and outcome.result.items[0]["title"] == "Invoice-September.pdf"
    assert outcome.result.items[0]["detail"] == "~/Documents/Taxes"
    assert "1. Invoice-September.pdf in ~/Documents/Taxes" in outcome.result.report
    assert run(e.handle("open_file", {"index": 1})).status == "done"
    assert run(e.handle("reveal_file", {"index": 1})).status == "done"
    assert run(e.handle("open_file", {"index": 7})).message == "I don't have that file from the last search."
    assert run(e.handle("open_file", {"path": "/etc/hosts"})).message == "I only open files in your home folder."
    assert host.calls[-2:] == [("open", str(home / "Documents/Taxes/Invoice-September.pdf")),
                               ("reveal", str(home / "Documents/Taxes/Invoice-September.pdf"))]
    host.files = []
    empty = run(e.handle("search_files", {"query": "unicorn"}))
    assert empty.status == "done" and empty.result.report == 'no files matched "unicorn"'


def desktop(tmp_path):
    folder = tmp_path / "Desktop"
    folder.mkdir()
    for name in ["Screenshot 2026-10-01 at 9.41.00 AM.png", "Screen Shot 2026-09-01.png", "cat.jpg", "Resume.pdf",
                 "notes.md", "Installer.dmg", "song.mp3", "archive.zip", "script.py", "mystery.xyz", ".DS_Store"]:
        (folder / name).write_text("x")
    (folder / "Projects").mkdir()               # the user's own folder: untouched
    (folder / "Images").mkdir()                 # an existing group folder: reused, not moved
    (folder / "Images" / "Resume.pdf").write_text("older copy")
    return folder


def test_desktop_grouping_rules(tmp_path):
    assert group_for(Path("Screenshot 2026.png")) == "Screenshots"
    assert group_for(Path("CleanShot 2026.mp4")) == "Screenshots"
    assert group_for(Path("photo.HEIC")) == "Images"
    assert group_for(Path("thing.unknown")) == "Other"
    plan = plan_desktop(desktop(tmp_path))
    assert plan["Screenshots"] == ["Screen Shot 2026-09-01.png", "Screenshot 2026-10-01 at 9.41.00 AM.png"]
    assert "Projects" not in str(plan) and ".DS_Store" not in str(plan)
    assert plan["Other"] == ["mystery.xyz"]


def test_organize_desktop_asks_first_moves_and_undoes(tmp_path):
    folder = desktop(tmp_path)
    undo_file = tmp_path / "undo.json"
    e = ActionEngine(ActionContext(host=FakeHost(home=str(tmp_path)), state={"desktop": str(folder)}),
                     undo_path=undo_file)
    outcome = run(e.handle("organize_desktop", {}))
    assert outcome.status == "pending" and outcome.preview.title == "Tidy 10 files into 8 folders"
    assert outcome.preview.lines[0] == "Screenshots: 2"
    assert (folder / "cat.jpg").exists()                       # nothing moved before the yes
    done = run(e.answer(True))
    assert done.status == "done" and "tidied 10 files into 8 folders" in done.result.say
    assert sorted(path.name for path in folder.iterdir() if not path.name.startswith(".")) == [
        "Archives", "Audio", "Code", "Documents", "Images", "Installers", "Other", "Projects", "Screenshots"]
    assert (folder / "Images" / "cat.jpg").exists() and (folder / "Documents" / "Resume.pdf").exists()
    assert json.loads(undo_file.read_text())[0]["kind"] == "moves"

    # a fresh engine (Plip restarted) can still undo it
    e2 = ActionEngine(ActionContext(host=FakeHost(home=str(tmp_path)), state={"desktop": str(folder)}),
                      undo_path=undo_file)
    restored = run(e2.handle("undo", {}))
    assert restored.status == "done" and restored.result.say == "Put 10 files back where they were."
    assert (folder / "cat.jpg").exists() and not (folder / "Screenshots").exists()
    assert (folder / "Images" / "Resume.pdf").read_text() == "older copy"       # pre-existing folder kept
    assert run(e2.handle("undo", {})).message == "There's nothing for me to undo."
    assert run(e2.answer(True)) is None                                           # nothing pending


def test_organize_on_a_tidy_desktop_says_so(tmp_path):
    (tmp_path / "Desktop").mkdir()
    e = ActionEngine(ActionContext(host=FakeHost(home=str(tmp_path)), state={"desktop": str(tmp_path / "Desktop")}))
    outcome = run(e.handle("organize_desktop", {}))
    assert outcome.status == "failed" and outcome.message == "Your desktop's already tidy."


def test_cancelled_confirmation_does_nothing(tmp_path):
    folder = desktop(tmp_path)
    e = ActionEngine(ActionContext(host=FakeHost(home=str(tmp_path)), state={"desktop": str(folder)}))
    run(e.handle("organize_desktop", {}))
    cancelled = run(e.answer(False))
    assert cancelled.status == "cancelled" and (folder / "cat.jpg").exists()


def test_system_settings_build_the_right_applescript():
    host = FakeHost(osa_reply="40")
    e = engine(host)
    for args in ({"setting": "dark_mode", "value": "on"}, {"setting": "dark mode", "value": "toggle"},
                 {"setting": "volume", "value": 130}, {"setting": "volume", "value": "up"},
                 {"setting": "mute", "value": "on"}, {"setting": "mute", "value": "off"}):
        assert run(e.handle("system", args)).status == "done", args
    scripts = [call[1] for call in host.calls]
    assert scripts[0].endswith("set dark mode to true") and scripts[1].endswith("set dark mode to not dark mode")
    assert scripts[2] == "set volume output volume 100"
    assert scripts[3] == "output volume of (get volume settings)" and scripts[4] == "set volume output volume 55"
    assert scripts[5] == "set volume with output muted" and scripts[6] == "set volume without output muted"
    assert run(e.handle("system", {"setting": "wifi", "value": "off"})).message == "I can't change wifi yet."


def test_shortcuts_fuzzy_match_and_report():
    host = FakeHost()
    e = engine(host)
    assert run(e.handle("run_shortcut", {"name": "morning routine"})).status == "done"
    assert run(e.handle("run_shortcut", {"name": "log watr"})).status == "done"
    assert run(e.handle("run_shortcut", {"name": "launch rockets"})).status == "failed"
    assert host.calls == [("shortcut", "Morning Routine"), ("shortcut", "Log Water")]
    listed = run(e.handle("list_shortcuts", {}))
    assert listed.result.report == "the user's shortcuts: Morning Routine, Log Water"


def test_reminders_notes_and_dates():
    import datetime as dt

    now = dt.datetime(2026, 10, 2, 15, 0)
    assert parse_when("2026-10-03 09:30", now) == dt.datetime(2026, 10, 3, 9, 30)
    assert parse_when("2026-10-03", now) == dt.datetime(2026, 10, 3, 9, 0)
    assert parse_when("08:15", now) == dt.datetime(2026, 10, 3, 8, 15)        # already past today
    assert parse_when("16:15", now) == dt.datetime(2026, 10, 2, 16, 15)
    host = FakeHost()
    e = engine(host)
    assert run(e.handle("create_reminder", {"title": 'Call "Mom"', "due": "2026-10-03 09:30"})).status == "done"
    script = host.calls[-1][1]
    assert "set day of dueDate to 3" in script and "set hours of dueDate to 9" in script
    assert 'name:"Call \\"Mom\\"", due date:dueDate' in script
    assert run(e.handle("create_reminder", {"title": "x", "due": "someday"})).status == "failed"
    run(e.handle("create_note", {"title": "Groceries", "body": "eggs\n<milk>"}))
    assert "<h1>Groceries</h1><div>eggs</div><div>&lt;milk&gt;</div>" in host.calls[-1][1]


def test_timer_schedules_and_announces():
    scheduled, announced = [], []
    host = FakeHost()
    ctx = ActionContext(host=host, schedule=lambda delay, fn: scheduled.append((delay, fn)),
                        announce=announced.append)
    e = ActionEngine(ctx)
    outcome = run(e.handle("set_timer", {"minutes": 5, "label": "tea"}))
    assert outcome.status == "done" and outcome.result.detail == "5 min · tea"
    delay, ring = scheduled[0]
    assert delay == 300
    ring()
    assert announced == ["Your tea timer is done."] and host.calls[-1] == ("notify", "Plip", "Your tea timer is done.")
    assert run(e.handle("set_timer", {"minutes": 0})).status == "failed"
    assert run(e.handle("set_timer", {"seconds": 30})).result.detail == "30 sec"


def test_find_flights_opens_google_flights_and_looks_again():
    url = flights_url("JFK", "SFO", "2026-10-12", "2026-10-15", 2, "business")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
    assert url.startswith("https://www.google.com/travel/flights?hl=en&q=")
    assert query == "Flights from JFK to SFO on 2026-10-12 returning 2026-10-15 for 2 adults business class"
    assert "one way" in urllib.parse.unquote(flights_url("NYC", "Paris", "2026-11-01"))
    host = FakeHost()
    outcome = run(engine(host).handle("find_flights", {"from": "JFK", "to": "SFO", "depart": "2026-10-12"}))
    assert outcome.result.look_after == 5.0 and "point at the cheapest" in outcome.result.report
    assert run(engine(host).handle("find_flights", {"from": "JFK"})).message == "I need where you're going."


def test_engine_guards_unknown_disabled_slow_and_logs(tmp_path):
    log = ActionLog(tmp_path / "actions.jsonl")
    e = engine(enabled=lambda skill: skill != "travel", log=log)
    assert run(e.handle("launch_rocket", {})).message == "I don't know how to launch rocket yet."
    assert run(e.handle("find_flights", {"from": "a"})).message == "My travel skill is switched off in settings."
    run(e.handle("type_text", {"text": "secret words"}))
    run(e.handle("open_app", {"name": "Safari"}))
    entries = log.entries()
    assert [(entry["name"], entry["ok"]) for entry in entries] == [("type_text", True), ("open_app", True)]
    assert entries[0]["args"] == {} and "secret" not in (tmp_path / "actions.jsonl").read_text()

    class SlowHost(FakeHost):
        def type_text(self, text):
            time.sleep(1.0)
    slow = ActionEngine(ActionContext(host=SlowHost()), timeout=0.2)
    assert run(slow.handle("type_text", {"text": "x"})).message == "That took too long, so I stopped."
    portable = ActionEngine(ActionContext(host=PortableHost()))
    assert run(portable.handle("type_text", {"text": "x"})).message == "Typing for you needs macOS."


# -- whole turns ------------------------------------------------------------------------------

def companion(brain, host=None, *, tmp_path=None, **kw):
    events, speaker = Events(), Speaker()
    state = {"desktop": str(tmp_path / "Desktop")} if tmp_path else {}
    actions = ActionEngine(ActionContext(host=host or FakeHost(), state=state))
    buddy = Companion(brain=brain, capturer=Capturer(), speaker=speaker, pointer=Pointer(), observer=events,
                      actions=actions, walkthroughs=False, **kw)
    return buddy, events, speaker


def test_turn_runs_actions_and_hands_results_back(tmp_path):
    home = make_home(tmp_path)
    host = FakeHost(home=str(home))
    host.files = [FileHit(str(home / "Documents/Taxes/Invoice-September.pdf"), 1759370000.0)]
    brain = ScriptedBrain('Looking now. [DO:search_files {"query": "invoice", "kind": "pdf"}]',
                          "Found it: Invoice September, in your Taxes folder. Opening it. [DO:open_file {\"index\": 1}]")
    buddy, events, speaker = companion(brain, host)
    result = run(buddy.respond("find my september invoice"))
    assert result.state == "done" and result.turns == 2
    assert result.did == ["Searching files for invoice", "Opening the file"]
    followup = brain.calls[1][-1]
    assert followup.text.startswith("now: ") and "(action results, not from the user)" in followup.text
    assert "1. Invoice-September.pdf in ~/Documents/Taxes" in followup.text
    assert followup.images == ()                                  # results need no new screenshot
    assert speaker.said == ["Looking now.", "Found it: Invoice September, in your Taxes folder.", "Opening it."]
    steps = events.of("step")
    assert {"id": "action-1", "label": "Searching files for invoice", "status": "done", "detail": "1 found"} in steps
    assert events.of("action")[0]["items"][0]["title"] == "Invoice-September.pdf"
    history = buddy.conversation.history()                       # the finished task folded into one exchange
    assert [turn.text for turn in history] == [
        "find my september invoice",
        "Looking now. (did: Searching files for invoice) Found it: Invoice September, in your Taxes folder. "
        "Opening it. (did: Opening the file)"]


def test_turn_asks_before_consequential_actions_and_voice_yes_runs_it(tmp_path):
    desktop(tmp_path)
    brain = ScriptedBrain("I can tidy that. [DO:organize_desktop {}]")
    buddy, events, speaker = companion(brain, tmp_path=tmp_path)
    first = run(buddy.respond("clean up my desktop"))
    assert first.pending == "Tidy 10 files into 8 folders"
    assert speaker.said[-1] == "Tidy 10 files into 8 folders. Say yes and I'll do it."
    assert events.of("confirm")[0]["lines"][0] == "Screenshots: 2"
    assert (tmp_path / "Desktop" / "cat.jpg").exists()
    second = run(buddy.respond("yes please"))
    assert second.state == "done" and second.did == ["Tidying your desktop"]
    assert "tidied 10 files" in speaker.said[-1] and len(brain.calls) == 1      # no model call for the yes
    assert (tmp_path / "Desktop" / "Images" / "cat.jpg").exists()
    assert events.of("confirm")[-1] == {"cleared": True}


def test_new_question_clears_a_pending_action_and_buttons_work(tmp_path):
    desktop(tmp_path)
    brain = ScriptedBrain("[DO:organize_desktop {}] Want me to tidy it?", "It's sunny.", "[DO:organize_desktop {}] ok?")
    buddy, events, speaker = companion(brain, tmp_path=tmp_path)
    run(buddy.respond("tidy my desktop"))
    assert speaker.said[-1] == "Want me to tidy it?"                 # the model asked; no extra prompt
    run(buddy.respond("what's the weather"))
    assert buddy.actions.pending is None and len(brain.calls) == 2
    run(buddy.respond("tidy it again"))
    pressed = run(buddy.answer_pending(False))
    assert pressed.spoken == "Okay, I won't." and (tmp_path / "Desktop" / "cat.jpg").exists()
    assert run(buddy.answer_pending(True)) is None


def test_failed_and_unknown_actions_are_explained():
    brain = ScriptedBrain('On it. [DO:open_app {"name": "Photoshop"}] [DO:teleport {}]')
    host = FakeHost()
    host.open_app = lambda path: (_ for _ in ()).throw(RuntimeError("no app")) if path == "Photoshop" else None
    buddy, events, speaker = companion(brain, host)
    run(buddy.respond("open photoshop"))
    assert speaker.said == ["On it.", "I couldn't find an app called Photoshop.", "I don't know how to teleport yet."]
    assert [step["status"] for step in events.of("step") if step["id"].startswith("action")] == [
        "active", "failed", "active", "failed"]


def test_flights_turn_takes_a_fresh_look(tmp_path, monkeypatch):
    brain = ScriptedBrain('Pulling up flights. [DO:find_flights {"from": "JFK", "to": "SFO", "depart": "2026-10-12"}]',
                          "Cheapest is JetBlue at two hundred ten dollars nonstop. [POINT:400,300:JetBlue]")
    buddy, events, speaker = companion(brain)
    real_sleep = asyncio.sleep

    async def no_wait(delay):
        await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    result = run(buddy.respond("find me flights from new york to sf on the twelfth"))
    look = brain.calls[1][-1]
    assert len(look.images) == 1 and "here's my screen now" in look.text
    assert result.targets and result.targets[0].label == "JetBlue"
    assert any(step.get("label") == "Waiting for it to load" for step in events.of("step"))


def test_plan_tag_reaches_the_ui():
    brain = ScriptedBrain("[STEPS:3] [PLAN: open settings | privacy | turn on] First, open settings.")
    buddy, events, _ = companion(brain)
    result = run(buddy.respond("how do I turn on location"))
    assert result.plan == ("open settings", "privacy", "turn on")
    assert events.of("plan") == [{"steps": ["open settings", "privacy", "turn on"]}]


def test_timer_announces_later_through_the_companion():
    brain = ScriptedBrain('Timer set. [DO:set_timer {"seconds": 0.05, "label": "tea"}]')
    buddy, events, speaker = companion(brain)

    async def scenario():
        await buddy.respond("set a tea timer")
        await asyncio.sleep(3.3 if False else 0.2)
    run(scenario())
    assert speaker.said[-1] == "Your tea timer is done." and events.of("notice") == [{"text": "Your tea timer is done."}]


def test_a_failed_screen_capture_still_gets_an_answer():
    class Blind(Capturer):
        def capture(self, *, only_cursor_screen=False):
            raise OSError("no display")
    brain = ScriptedBrain("It's Tuesday.")
    events = Events()
    buddy = Companion(brain=brain, capturer=Blind(), speaker=Speaker(), pointer=Pointer(), observer=events)
    result = run(buddy.respond("what day is it"))
    assert result.state == "done" and result.spoken == "It's Tuesday."
    assert brain.calls[0][-1].images == () and "no screenshot" in brain.calls[0][-1].text
    assert {"id": "look", "label": "Couldn't see your screen", "status": "skipped",
            "detail": "check Screen Recording"} in events.of("step")


def test_cursor_lookup_survives_pyautogui_exiting(monkeypatch):
    import sys
    import types

    from mcp_vision.buddy import capture

    broken = types.ModuleType("pyautogui")

    def position():
        raise SystemExit("NOTE: You must install tkinter on Linux to use MouseInfo.")
    broken.position = position
    monkeypatch.setitem(sys.modules, "pyautogui", broken)
    monkeypatch.setattr(capture.sys, "platform", "linux")
    assert capture.cursor_position() is None


def test_pointer_queries_never_overlap(monkeypatch):
    # python-xlib isn't thread safe; overlapping queries on one display can hang forever.
    import sys
    import threading
    import types

    from mcp_vision.buddy import capture

    inside, overlaps = [0], []
    fake = types.ModuleType("pyautogui")

    def position():
        inside[0] += 1
        overlaps.append(inside[0])
        time.sleep(0.02)
        inside[0] -= 1
        return 10, 20
    fake.position = position
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setattr(capture.sys, "platform", "linux")
    threads = [threading.Thread(target=capture.cursor_position) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlaps == [1, 1, 1, 1] and capture.cursor_position() == (10.0, 20.0)


@pytest.mark.parametrize("stuck_in", ["capture", "screens"])
def test_a_hung_screen_grab_times_out_and_still_answers(stuck_in):
    import threading

    from mcp_vision.buddy.router import RuleRouter

    gate = threading.Event()

    class Stuck(Capturer):                  # e.g. an X server that never answers a pointer query
        def capture(self, *, only_cursor_screen=False):
            if stuck_in == "capture":
                gate.wait(5)
            return []

        def screens(self):
            if stuck_in == "screens":
                gate.wait(5)
            return []
    brain = ScriptedBrain("Here you go.")
    buddy = Companion(brain=brain, capturer=Stuck(), speaker=Speaker(), pointer=Pointer(),
                      router=RuleRouter(), capture_timeout=0.2)

    async def timed():
        started = time.monotonic()
        result = await buddy.respond("what's up")
        elapsed = time.monotonic() - started
        gate.set()                      # let the stuck grab finish so the loop can close
        return result, elapsed
    result, elapsed = run(timed())
    assert result.spoken == "Here you go." and elapsed < 2


def test_a_rewrite_sees_the_whole_email_and_never_replaces_more_than_it_saw():
    from mcp_vision.buddy.geometry import Rect, ScreenInfo, Screenshot
    from mcp_vision.buddy.screen_context import SELECTION_LIMIT, ScreenContext

    shot = Screenshot(screen=ScreenInfo(1, Rect(0, 0, 1512, 982), is_cursor_screen=True), data=b"", width=1280,
                      height=831)
    email = "Hi Sam,\n\nThanks for the   notes on the deck.\r\n\n\n\nBest,\nAda " + "and more. " * 70
    text = ScreenContext(app="Mail", selection=email, selection_chars=len(email)).describe([shot])
    # The model gets every paragraph back, not the first 600 characters run together.
    assert 'selected text:\n"""\nHi Sam,\n\nThanks for the notes on the deck.\n\nBest,\nAda and more.' in text
    assert text.rstrip().endswith('and more.\n"""') and "only the first" not in text

    host = FakeHost()
    e = engine(host)
    long = "x" * (SELECTION_LIMIT + 500)
    e.ctx.screen = ([shot], ScreenContext(selection=long[:SELECTION_LIMIT], selection_chars=len(long)))
    assert "only the first 3,000 of 3,500 characters" in e.ctx.screen[1].describe([shot])
    outcome = asyncio.run(e.handle("replace_selection", {"text": "shorter"}))
    assert outcome.status == "failed" and "smaller part" in outcome.message
    e.ctx.screen = ([shot], ScreenContext(selection=email, selection_chars=len(email)))
    assert asyncio.run(e.handle("replace_selection", {"text": "Hey Sam!"})).status == "done"
