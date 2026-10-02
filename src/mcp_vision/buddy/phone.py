"""Text Plip from your phone.

Send yourself an iMessage that starts with ``/plip`` ("/plip open spotify",
"/plip find my lease pdf", "/plip what's on my screen"). Your Mac sees it in
Messages, runs it like a spoken request, and texts the answer back. Only
messages in the chat with your own number or Apple ID count, so nobody else
can drive your Mac. Reading Messages needs Full Disk Access.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from mcp_vision.buddy.memory.importers import decode_attributed_body, open_chat_db, strip_handle

HELP = ("Plip here. Text me things like:\n"
        "/plip open spotify\n/plip find my lease pdf\n/plip what's on my screen\n"
        "/plip remind me to call mom at 6pm\n/plip start my day")
NEW_MESSAGES = (
    "SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me, h.id, c.chat_identifier, m.date "
    "FROM message m LEFT JOIN handle h ON h.ROWID = m.handle_id "
    "LEFT JOIN chat_message_join j ON j.message_id = m.ROWID LEFT JOIN chat c ON c.ROWID = j.chat_id "
    "WHERE m.ROWID > ? ORDER BY m.ROWID ASC LIMIT 100")


@dataclass(frozen=True)
class Incoming:
    rowid: int
    text: str
    handle: str                # whose conversation it is (one of yours)


def same_handle(a: str, b: str) -> bool:
    a, b = strip_handle(a).lower(), strip_handle(b).lower()
    if "@" in a or "@" in b:
        return a == b
    digits_a, digits_b = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return bool(digits_a) and (digits_a == digits_b or digits_a[-10:] == digits_b[-10:] and len(digits_a) >= 10)


class PhoneRelay:
    def __init__(self, db_path: Path, handles: list[str], run: Callable[[str], str],
                 send: Callable[[str, str], None], *, prefix: str = "/plip", poll: float = 3.0,
                 max_per_hour: int = 30, clock: Callable[[], float] = time.time):
        self.db_path = db_path
        self.handles = [handle for handle in handles if handle]
        self.run = run
        self.send = send
        self.prefix = prefix.lower()
        self.poll = poll
        self.max_per_hour = max_per_hour
        self.clock = clock
        self.last_rowid: int | None = None
        self.status = "off"
        self.error = ""
        self.last_command = ""
        self._recent: deque[tuple[float, str]] = deque(maxlen=50)
        self._commands: deque[float] = deque(maxlen=200)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="plip-phone")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
        self.status = "off"

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:          # keep polling; surface the problem in Settings
                self.status, self.error = "error", str(exc)
            self._stop.wait(self.poll)

    # -- one poll -------------------------------------------------------------------------------
    def tick(self) -> list[Incoming]:
        if not self.handles:
            self.status, self.error = "error", "Add your phone number or Apple ID first."
            return []
        try:
            with closing(open_chat_db(self.db_path)) as db:
                if self.last_rowid is None:              # start fresh: never replay old messages
                    self.last_rowid = int(db.execute("SELECT COALESCE(MAX(ROWID), 0) FROM message").fetchone()[0])
                    self.status, self.error = "listening", ""
                    return []
                rows = db.execute(NEW_MESSAGES, (self.last_rowid,)).fetchall()
        except (sqlite3.Error, OSError) as exc:
            self.status = "error"
            self.error = ("Plip needs Full Disk Access to read Messages."
                          if "authoriz" in str(exc).lower() or "unable to open" in str(exc).lower() else str(exc))
            return []
        self.status, self.error = "listening", ""
        commands = []
        for rowid, text, body, _from_me, sender, chat, _date in rows:
            self.last_rowid = max(self.last_rowid, int(rowid))
            message = (text or decode_attributed_body(body) or "").strip()
            if not message.lower().startswith(self.prefix):
                continue
            conversation = next((handle for handle in self.handles
                                 if same_handle(handle, chat or "") or same_handle(handle, sender or "")), None)
            if conversation is None:
                continue                                  # not your own chat: ignore
            command = message[len(self.prefix):].strip(" :,")
            if self._duplicate(command):
                continue                                  # a note-to-self shows up as sent and received
            commands.append(Incoming(int(rowid), command, conversation))
        for incoming in commands:
            self._handle(incoming)
        return commands

    def _duplicate(self, command: str) -> bool:
        now = self.clock()
        key = command.lower()
        if any(seen == key and now - at < 30 for at, seen in self._recent):
            return True
        self._recent.append((now, key))
        return False

    def _handle(self, incoming: Incoming) -> None:
        now = self.clock()
        while self._commands and now - self._commands[0] > 3600:
            self._commands.popleft()
        if not incoming.text or incoming.text.lower() in {"help", "?"}:
            self.send(incoming.handle, HELP)
            return
        if len(self._commands) >= self.max_per_hour:
            self.send(incoming.handle, "Plip: that's a lot of requests. Give me a few minutes.")
            return
        self._commands.append(now)
        self.last_command = incoming.text
        try:
            reply = self.run(incoming.text) or "Done."
        except Exception:
            reply = "Something went wrong on my end."
        self.send(incoming.handle, "Plip: " + reply.strip()[:1500])

    def snapshot(self) -> dict:
        return {"status": self.status, "error": self.error, "lastCommand": self.last_command}


class CollectSpeaker:
    """Speaker for remote turns: keeps the words to text back instead of saying them."""

    def __init__(self):
        self.parts: list[str] = []

    def speak(self, text: str) -> None:
        self.parts.append(text)

    async def drain(self) -> None:
        return None

    def stop(self) -> None:
        pass

    def take(self) -> str:
        text, self.parts = " ".join(self.parts), []
        return text


REMOTE_NOTE = ("(the user is texting you from their phone, away from the mac. they can't see the screen or "
               "hear you: reply in one to three short sentences, don't point, and use actions to get things done. "
               "for actions that ask first, tell them to reply \"/plip yes\" to confirm.)")
