"""Routines (taught and learned) and texting Plip from your phone."""
from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from buddy_fakes import Capturer, Events, FakeHost, Pointer, ScriptedBrain, Speaker
from mcp_vision.buddy.actions import ActionContext, ActionEngine, ActionError
from mcp_vision.buddy.companion import Companion
from mcp_vision.buddy.phone import HELP, REMOTE_NOTE, CollectSpeaker, PhoneRelay, same_handle
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


# -- phone ---------------------------------------------------------------------------------

def attributed(text: str) -> bytes:
    body = text.encode()
    return (b"streamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85"
            b"\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + bytes([len(body)]) + body + b"\x86\x84")


def chat_db(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, is_from_me INT,
                                  handle_id INT, date INT, account TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, account_login TEXT);
            CREATE TABLE chat_message_join (chat_id INT, message_id INT);
            INSERT INTO handle VALUES (1, '+15550102000'), (2, '+15559990000');
            INSERT INTO chat VALUES (1, '+15550102000', 'E:me@icloud.com'), (2, '+15559990000', 'E:me@icloud.com');
            INSERT INTO message (text, is_from_me, handle_id, date) VALUES ('/plip old command', 1, 1, 0);
            INSERT INTO chat_message_join VALUES (1, 1);
        """)
        db.commit()


def add_message(path, text=None, body=None, from_me=1, handle=1, chat=1):
    with closing(sqlite3.connect(path)) as db:
        cursor = db.execute("INSERT INTO message (text, attributedBody, is_from_me, handle_id, date) "
                            "VALUES (?, ?, ?, ?, 0)", (text, body, from_me, handle))
        db.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, cursor.lastrowid))
        db.commit()


def relay(tmp_path, run=None, **kw):
    path = tmp_path / "chat.db"
    chat_db(path)
    ran, sent = [], []
    phone = PhoneRelay(path, ["+1 (555) 010-2000"], run or (lambda text: ran.append(text) or f"did {text}"),
                       lambda handle, text: sent.append((handle, text)), **kw)
    return phone, path, ran, sent


def test_phone_relay_runs_your_own_commands_once(tmp_path):
    phone, path, ran, sent = relay(tmp_path)
    assert phone.tick() == [] and phone.status == "listening"           # first tick: skip history
    add_message(path, "/plip open spotify", from_me=1)
    add_message(path, "/plip open spotify", from_me=0)                   # the same note-to-self, received
    add_message(path, "/plip wire money to me", handle=2, chat=2, from_me=0)   # someone else: ignored
    add_message(path, "just a normal text")
    add_message(path, body=attributed("/PLIP find my lease"))           # text only in attributedBody
    commands = phone.tick()
    assert [command.text for command in commands] == ["open spotify", "find my lease"]
    assert ran == ["open spotify", "find my lease"]
    assert sent == [("+1 (555) 010-2000", "Plip: did open spotify"), ("+1 (555) 010-2000", "Plip: did find my lease")]
    assert phone.tick() == [] and phone.snapshot()["lastCommand"] == "find my lease"


def test_phone_help_rate_limit_and_errors(tmp_path):
    phone, path, ran, sent = relay(tmp_path, max_per_hour=1)
    phone.tick()
    add_message(path, "/plip help")
    add_message(path, "/plip one")
    add_message(path, "/plip two")
    phone.tick()
    assert sent[0][1] == HELP and ran == ["one"] and "a lot of requests" in sent[-1][1]
    broken = PhoneRelay(tmp_path / "missing" / "chat.db", ["+15550102000"], lambda t: "", lambda h, t: None)
    broken.tick()
    assert broken.status == "error" and "Full Disk Access" in broken.error
    nobody = PhoneRelay(tmp_path / "chat.db", [], lambda t: "", lambda h, t: None)
    nobody.tick()
    assert "Add your phone number" in nobody.error


def test_handles_compare_like_people_write_them():
    assert same_handle("+1 (555) 010-2000", "p:+15550102000") and same_handle("5550102000", "+15550102000")
    assert same_handle("E:Me@iCloud.com", "me@icloud.com") and not same_handle("me@icloud.com", "+15550102000")
    assert not same_handle("+15550102000", "+15550102001")


def test_texting_plip_end_to_end_with_confirmation(tmp_path):
    os.makedirs(tmp_path / "Desktop")
    for name in ("a.png", "b.pdf"):
        (tmp_path / "Desktop" / name).write_text("x")
    speaker = CollectSpeaker()
    brain = ScriptedBrain("Opening Spotify. [DO:open_app {\"name\": \"spotify\"}]",
                          "I can tidy your desktop. [DO:organize_desktop {}]")
    host = FakeHost(home=str(tmp_path))
    remote = Companion(brain=brain, capturer=Capturer(), speaker=speaker, pointer=None,
                       actions=ActionEngine(ActionContext(host=host, state={"desktop": str(tmp_path / "Desktop")})),
                       notes=lambda: REMOTE_NOTE, walkthroughs=False)
    loop = asyncio.new_event_loop()

    def run(text):
        result = loop.run_until_complete(remote.respond(text))
        return speaker.take() or result.spoken
    phone, path, _, sent = relay(tmp_path, run=run)
    phone.tick()
    for text in ("/plip open spotify", "/plip clean my desktop", "/plip yes"):
        add_message(path, text)
        phone.tick()
    assert sent[0][1] == "Plip: Opening Spotify."
    assert sent[1][1] == "Plip: I can tidy your desktop. Tidy 2 files into 2 folders. Say yes and I'll do it."
    assert sent[2][1].startswith("Plip: Done. I tidied 2 files into 2 folders.")
    assert (tmp_path / "Desktop" / "Images" / "a.png").exists()
    assert "texting you from their phone" in brain.calls[0][-1].text
    loop.close()
