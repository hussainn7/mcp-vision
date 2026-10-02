"""Routines: taught by voice and learned from habits."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

import pytest

from buddy_fakes import Capturer, Events, FakeHost, Pointer, ScriptedBrain, Speaker
from mcp_vision.buddy.actions import ActionContext, ActionEngine, ActionError
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.routines import Routines, suggest_routines, validate_steps

STEPS = [{"name": "open_app", "args": {"name": "Slack"}}, {"name": "open_app", "args": {"name": "Calendar"}},
         {"name": "system", "args": {"setting": "volume", "value": 20}}]


@pytest.fixture
def routines(tmp_path):
    return Routines(tmp_path / "routines.json")


def test_routines_store_match_and_persist(routines, tmp_path):
    routine = routines.add("Morning setup", "Start my day!", STEPS)
    assert routine.phrase == "start my day"
    assert routines.match("start my day") is routine and routines.match("Hey Plip, start my day please") is routine
    assert routines.match("start my day tomorrow") is None and routines.find("morning setup") is routine
    assert routine.describe() == "open Slack, open Calendar, set volume 20"
    assert 'run it with run_routine' in routines.summary() and '"start my day" (Morning setup)' in routines.summary()
    routines.add("Morning v2", "start my day", STEPS[:1])                 # same phrase replaces the old one
    assert len(routines.items) == 1 and routines.items[0].name == "Morning v2"
    routines.save()
    assert oct(os.stat(tmp_path / "routines.json").st_mode & 0o777) == "0o600"
    assert Routines(tmp_path / "routines.json").items[0].steps == STEPS[:1]


def test_routines_only_hold_safe_steps():
    with pytest.raises(ActionError, match="can't include send message"):
        validate_steps([{"name": "send_message", "args": {"to": "boss", "text": "I quit"}}])
    with pytest.raises(ActionError):
        validate_steps([])
    assert validate_steps([{"name": "open_app", "args": {"name": "Notes"}, "extra": 1}]) == [
        {"name": "open_app", "args": {"name": "Notes"}}]


def test_save_and_run_routine_actions(routines):
    host = FakeHost()
    engine = ActionEngine(ActionContext(host=host, routines=routines))
    saved = asyncio.run(engine.handle("save_routine", {"name": "Focus", "phrase": "focus time", "steps": STEPS}))
    assert saved.status == "done" and 'Say "focus time"' in saved.result.say
    ran = asyncio.run(engine.handle("run_routine", {"name": "focus time"}))
    assert ran.status == "done" and ran.result.detail == "3 of 3 steps"
    assert host.calls == [("open_app", "/Applications/Slack.app"), ("open_app", "Calendar"),
                          ("osascript", "set volume output volume 20")]
    assert routines.items[0].runs == 1
    assert asyncio.run(engine.handle("run_routine", {"name": "nope"})).message == \
        "I don't have a routine by that name yet."


def test_saying_the_phrase_runs_it_instantly(routines):
    routines.add("Morning setup", "start my day", STEPS[:2])
    host, brain, speaker, events = FakeHost(), ScriptedBrain("should not be called"), Speaker(), Events()
    buddy = Companion(brain=brain, capturer=Capturer(), speaker=speaker, pointer=Pointer(), observer=events,
                      actions=ActionEngine(ActionContext(host=host, routines=routines)), routines=routines)
    result = asyncio.run(buddy.respond("Start my day."))
    assert brain.calls == [] and speaker.said == ["Running Morning setup."]
    assert [call[0] for call in host.calls] == ["open_app", "open_app"] and result.did == ["ran Morning setup"]
    assert events.of("step")[-1]["status"] == "done"


# -- learning --------------------------------------------------------------------------------

def log_day(day: datetime, items, start_minute=5, gap=40):
    entries, at = [], day.replace(hour=9, minute=start_minute).timestamp()
    for name, args in items:
        entries.append({"name": name, "args": args, "ok": True, "at": at})
        at += gap
    return entries


MORNING = [("open_app", {"name": "Slack"}), ("open_app", {"name": "Calendar"}), ("open_app", {"name": "spotify"})]


def test_routine_suggestions_from_repeated_sessions():
    monday = datetime(2026, 9, 28)
    entries = []
    for offset in range(4):
        entries += log_day(monday + timedelta(days=offset), MORNING)
    entries += log_day(monday + timedelta(days=1), [("open_app", {"name": "Photos"})], start_minute=50)
    entries.append({"name": "open_app", "args": {"name": "Notes"}, "ok": False, "at": monday.timestamp()})
    entries.append({"name": "send_message", "args": {"to": "x"}, "ok": True, "at": monday.timestamp()})
    suggestions = suggest_routines(entries)
    assert len(suggestions) == 1
    top = suggestions[0]
    assert top["labels"] == ["open Slack", "open Calendar", "open Spotify"]
    assert top["name"] == "Morning setup" and top["phrase"] == "start my day" and top["days"] == 4
    assert top["around"] == "9:05 AM"
    assert suggest_routines(entries, dismissed=[top["key"]]) == []
    from mcp_vision.buddy.routines import Routine
    assert suggest_routines(entries, existing=[Routine("Mine", "go", top["steps"])]) == []
    assert suggest_routines(entries[:6]) == []                             # only two days: not a habit yet


def test_settings_accept_dismiss_and_delete_suggestions(tmp_path):
    from mcp_vision.buddy.settings_service import SettingsService
    from mcp_vision.buddy.settings import BuddySettings
    from mcp_vision.buddy.store import History

    start = datetime.now() - timedelta(days=4)
    entries = [entry for offset in range(3) for entry in log_day(start + timedelta(days=offset), MORNING)]

    class Log:
        def entries(self):
            return entries
    posted, reloads = [], []
    routines = Routines(tmp_path / "routines.json")
    svc = SettingsService(engines=lambda: [], settings=lambda: BuddySettings(_env_file=None),
                          reload=lambda: reloads.append(1), post=posted.extend, prefs_path=tmp_path / "prefs.json",
                          env_path=tmp_path / ".env", history=History(path=tmp_path / "h.jsonl"),
                          routines=routines, action_log=Log())
    svc.push()
    suggestion = posted[-1]["state"]["suggestions"][0]
    assert posted[-1]["state"]["stats"]["actionsWeek"] == 9
    svc.handle({"cmd": "routine-accept", "key": suggestion["key"], "phrase": "good morning"})
    state = posted[-1]["state"]
    assert state["routines"][0]["phrase"] == "good morning" and state["routines"][0]["source"] == "suggested"
    assert state["suggestions"] == [] and reloads == [1]
    svc.handle({"cmd": "routine-delete", "id": state["routines"][0]["id"]})
    assert posted[-1]["state"]["routines"] == [] and posted[-1]["state"]["suggestions"]
    svc.handle({"cmd": "routine-dismiss", "key": suggestion["key"]})
    assert posted[-1]["state"]["suggestions"] == []

