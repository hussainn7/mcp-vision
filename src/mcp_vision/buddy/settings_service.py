"""Backs Plip's Settings window: builds its snapshot and handles its commands.

Platform-neutral. Everything that touches macOS (clipboard, Terminal,
permission prompts, quitting) arrives through ``Platform`` so the logic is
tested on any OS.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_vision import __version__
from mcp_vision.buddy.hotkey import CHORDS, chord, keyboard_owner
from mcp_vision.buddy.store import History, Prefs, config_dir

KEY_NAMES = {"ANTHROPIC_API_KEY", "GEMINI_API_KEY", "TYPESAFE_API_KEY", "ELEVENLABS_API_KEY", "ASSEMBLYAI_API_KEY"}
DEPTHS = {"fast", "balanced", "deep"}
TOUR_STEPS = ("welcome", "permissions", "brain", "try", "done")
IMPORT_SOURCES = {"contacts", "autofill", "mail"}
AI_SOURCES = {"chatgpt", "claude", "gemini", "ai"}
# pasted key shapes: Google AI Studio, Anthropic
KEY_SHAPES = (("GEMINI_API_KEY", re.compile(r"AIza[0-9A-Za-z_\-]{30,60}")),
              ("ANTHROPIC_API_KEY", re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,200}")))


def _check_key(name: str, value: str) -> str:
    """"" if the provider takes the key, else the problem. Only Google's is checked."""
    if name != "GEMINI_API_KEY":
        return ""
    from mcp_vision.buddy.brain_gemini import check_key

    return check_key(value)


@dataclass
class Platform:
    copy: Callable[[str], None] = lambda text: None
    open_url: Callable[[str], None] = lambda url: None
    run_in_terminal: Callable[[str], None] = lambda command: None
    request_permission: Callable[[str], None] = lambda name: None
    permissions: Callable[[], dict[str, Any]] = dict
    say: Callable[[str], None] = lambda text: None
    quit: Callable[[], None] = lambda: None
    open_settings: Callable[[str], None] = lambda tab: None
    restart: Callable[[], None] = lambda: None             # quit and open Plip again
    clipboard: Callable[[], str] = lambda: ""                # read only on Paste key


@dataclass
class SettingsService:
    engines: Callable[[], list[dict[str, Any]]]            # probed engine cards
    settings: Callable[[], Any]                            # current BuddySettings
    reload: Callable[[], None]                             # rebuild the companion after a change
    post: Callable[[list[dict[str, Any]]], None]           # to the Settings web view
    platform: Platform = field(default_factory=Platform)
    prefs_path: Path | None = None
    env_path: Path | None = None
    history: History = field(default_factory=History)
    on_refresh: Callable[[], None] = lambda: None          # re-probe engines/permissions in the background
    memory: Any = None                                     # buddy.memory.Memory
    run_import: Callable[[str], None] = lambda source: None   # background import (Contacts, Mail, ...)
    action_log: Any = None                                 # buddy.actions.ActionLog (for stats)
    usage: Any = None                                      # buddy.usage.UsageLog (the Usage tab)
    parakeet: Any = None                                   # buddy.parakeet.ParakeetModel (the opt-in download)
    account: Any = None                                    # buddy.account.Account (sign in before Plip works)
    updates: Any = None                                    # buddy.updates.Updates (a newer Plip is out)
    check_updates: Callable[[], None] = lambda: None       # ask GitHub now, in the background
    connector: Any = None                                  # buddy.connect.Connector (one-click Connect)
    live: Callable[[], dict | None] = lambda: None         # last request, live (tour's "try it" step)
    check_key: Callable[[str, str], str] = _check_key      # "" when the provider takes a pasted key
    key_check: dict = field(default_factory=dict)          # {name, state: checking | ok | bad, message}
    main: Callable[[Callable[[], None]], None] = lambda job: job()       # run on the UI thread
    background: Callable[[Callable[[], None]], None] = lambda job: threading.Thread(target=job, daemon=True).start()
    connect_note: str = ""                                 # what "Connect AI" just did, shown under the button
    report_note: str = ""                                  # "sent" | "failed" after a bug report or feature request
    hotkey_works: Callable[[], bool] = lambda: True        # macOS passes Plip the keys (see hotkey.can_listen)
    apply_hotkey: Callable[[str], None] = lambda name: None   # switch the talk shortcut now

    @property
    def prefs(self) -> Prefs:
        return Prefs.load(self.prefs_path)

    def snapshot(self) -> dict[str, Any]:
        prefs = self.prefs
        settings = self.settings()
        engines = self.engines()
        if self.connector is not None:                 # live install / sign-in progress on its card
            progress = self.connector.snapshot()
            engines = [{**engine, "connect": progress[engine["id"]]} if engine["id"] in progress else engine
                       for engine in engines]
        perms = self.platform.permissions() or {}
        keys = {name: bool(_key(settings, name)) for name in KEY_NAMES}
        tts = prefs.tts or ("elevenlabs" if settings.tts in {"auto", "elevenlabs"} and keys["ELEVENLABS_API_KEY"]
                            else "off" if settings.tts == "off" else "say")
        stt = prefs.stt or ("assemblyai" if settings.stt in {"auto", "assemblyai"} and keys["ASSEMBLYAI_API_KEY"]
                            else "apple")
        return {
            "version": __version__,
            "engines": engines,
            "depth": prefs.depth if prefs.depth in DEPTHS else "balanced",
            "walkthroughs": prefs.walkthroughs,
            "sounds": prefs.sounds,
            "permissions": {"screen": perms.get("screenRecording"), "accessibility": perms.get("accessibility"),
                            "microphone": perms.get("microphone"), "speech": perms.get("speechRecognition"),
                            "restart": bool(perms.get("restart")), "guiding": str(perms.get("guiding") or "")},
            "voice": {"tts": tts, "stt": stt, "elevenlabs": keys["ELEVENLABS_API_KEY"],
                      "assemblyai": keys["ASSEMBLYAI_API_KEY"],
                      "parakeet": self.parakeet.snapshot() if self.parakeet is not None else None},
            "jev": {"configured": keys["TYPESAFE_API_KEY"], "enabled": settings.router != "off", "latencyMs": None},
            "keys": keys,
            "history": self.history.items()[-50:],
            "memory": self.memory.panel() if self.memory is not None else None,
            "companion": prefs.companion,
            "stats": self._stats(),
            "usage": self._usage(),
            "onboarded": prefs.onboarded,
            "tour": {"step": prefs.tour_step if prefs.tour_step in TOUR_STEPS else "welcome"},
            "live": self.live(),
            "connect": self.connect_note,
            "keyCheck": dict(self.key_check) or None,
            "report": self.report_note,
            "account": self.account.snapshot() if self.account is not None
            else {"available": False, "required": False},
            "update": self.updates.snapshot() if self.updates is not None
            else {"enabled": prefs.update_check, "current": __version__, "available": None},
            "hotkey": {**chord(prefs.hotkey).card(), "works": bool(self.hotkey_works()), "owner": keyboard_owner(),
                       "choices": [chord(name).card() for name in CHORDS]},
        }

    def _stats(self) -> dict:
        entries = self.action_log.entries() if self.action_log is not None else []
        week = [entry for entry in entries if entry.get("at", 0) > time.time() - 7 * 86400 and entry.get("ok")]
        answered = len(self.history.items())
        return {"actionsWeek": len(week), "answers": answered,
                "minutesSaved": round(len(week) * 0.75 + answered * 1.5)}

    def _usage(self) -> dict | None:
        if self.usage is None:
            return None
        from mcp_vision.buddy.usage import summary

        return summary(self.usage.rows())

    def push(self) -> None:
        self.post([{"type": "settings", "state": self.snapshot()}])

    def handle(self, command: dict[str, Any]) -> None:
        name = command.get("cmd", "")
        handler = getattr(self, "_cmd_" + name.replace("-", "_"), None)
        if handler is None:
            return
        handler(command)

    # -- commands -------------------------------------------------------------------
    def _cmd_settings_ready(self, _command):
        self.push()
        self.on_refresh()

    _cmd_refresh = _cmd_settings_ready

    def _cmd_set_key(self, command):
        name, value = str(command.get("name", "")), str(command.get("value", "")).strip()
        if name not in KEY_NAMES or not value or "\n" in value:
            return
        if name == "GEMINI_API_KEY":                    # check with Google before saving
            self._check_then_save(name, value)
            return
        self._save_key(name, value)

    def _cmd_paste_key(self, _command):
        """"Paste key": take an AI key off the clipboard; nothing else is kept."""
        text = (self.platform.clipboard() or "").strip()
        for name, shape in KEY_SHAPES:
            if shape.fullmatch(text):
                self._cmd_set_key({"name": name, "value": text})
                return
        self.key_check = {"name": "", "state": "bad", "message": "There's no key on your clipboard yet. On Google's "
                          "page, click Copy next to your key, then click Paste key again."}
        self.push()

    def _check_then_save(self, name: str, value: str) -> None:
        self.key_check = {"name": name, "state": "checking", "message": "Checking the key with Google…"}
        self.push()

        def check() -> None:
            problem = self.check_key(name, value)

            def done() -> None:
                self.key_check = {"name": name, "state": "bad" if problem else "ok", "message": problem}
                if problem:
                    self.push()
                    return
                from mcp_vision.buddy.cli import write_env

                write_env(self.env_path or config_dir() / ".env", {name: value})
                if not self._ready_engine():
                    self._update_prefs(engine="gemini-api")     # only working brain: select it (one rebuild)
                else:
                    self.reload()
                    self.push()
            self.main(done)
        self.background(check)

    def _ready_engine(self) -> str:
        """A brain that already works (other than the one being added), or ""."""
        return next((engine["id"] for engine in self.engines() if engine.get("status") == "ready"
                     and engine.get("id") != "gemini-api"), "")

    def _save_key(self, name: str, value: str) -> None:
        from mcp_vision.buddy.cli import write_env

        write_env(self.env_path or config_dir() / ".env", {name: value})
        self.reload()
        self.push()

    def _update_prefs(self, **changes) -> None:
        prefs = self.prefs
        for key, value in changes.items():
            setattr(prefs, key, value)
        prefs.save(self.prefs_path)
        self.reload()
        self.push()

    def _cmd_select_engine(self, command):
        engine_id = str(command.get("id", ""))
        if any(engine["id"] == engine_id for engine in self.engines()):
            self._update_prefs(engine=engine_id)

    def _cmd_set_depth(self, command):
        if command.get("depth") in DEPTHS:
            self._update_prefs(depth=command["depth"])

    def _cmd_set_hotkey(self, command):
        name = str(command.get("id", ""))
        if name not in CHORDS:
            return
        prefs = self.prefs
        prefs.hotkey = name
        prefs.save(self.prefs_path)
        self.apply_hotkey(name)                    # no brain rebuild needed
        self.push()

    def _cmd_set_walkthroughs(self, command):
        self._update_prefs(walkthroughs=bool(command.get("enabled")))

    def _cmd_set_sounds(self, command):
        prefs = self.prefs
        prefs.sounds = bool(command.get("enabled"))
        prefs.save(self.prefs_path)                    # read on every sound: no brain rebuild
        self.push()

    def _cmd_set_voice(self, command):
        changes = {}
        if command.get("tts") in {"elevenlabs", "say", "off"}:
            changes["tts"] = command["tts"]
        if command.get("stt") in {"assemblyai", "apple", "parakeet"}:
            changes["stt"] = command["stt"]
        if changes:
            self._update_prefs(**changes)

    # -- Parakeet: picked in Voice, downloaded once (Apple's listens until it's in place) ----------
    def _cmd_parakeet_download(self, _command):
        if self.parakeet is None:
            return
        if self.prefs.stt != "parakeet":
            self._update_prefs(stt="parakeet")
        self.parakeet.start()

    def _cmd_parakeet_cancel(self, _command):
        if self.parakeet is not None:
            self.parakeet.cancel()

    def _cmd_parakeet_remove(self, _command):
        if self.parakeet is None:
            return
        self.parakeet.remove()
        if self.prefs.stt == "parakeet":
            self._update_prefs(stt="apple")
        else:
            self.push()

    def _cmd_finish_onboarding(self, _command):
        self._save_onboarded(True)

    def _cmd_tour_start(self, _command):
        """General → Replay the welcome tour: the walkthrough again, everything set up stays."""
        self._save_onboarded(False, step="welcome")

    def _cmd_tour_go(self, command):
        """Tour step changed: saved, so a restart resumes there."""
        step = str(command.get("step") or "")
        if step in TOUR_STEPS:
            prefs = self.prefs
            prefs.tour_step = step
            prefs.save(self.prefs_path)
            self.push()

    def _save_onboarded(self, done: bool, step: str | None = None) -> None:
        prefs = self.prefs
        prefs.onboarded = done
        if step is not None:
            prefs.tour_step = step
        prefs.save(self.prefs_path)
        self.push()

    # -- the account: sign in with Google once, in the browser ----------------------------------------
    def _cmd_account_sign_in(self, command):
        if self.account is not None:
            self.account.start(str(command.get("provider") or "google"))
            self.push()

    def _cmd_account_open(self, _command):
        if self.account is not None and self.account.status == "waiting" and self.account.link:
            self.platform.open_url(self.account.link)

    def _cmd_account_cancel(self, _command):
        if self.account is not None:
            self.account.cancel()
            self.push()

    def _cmd_account_sign_out(self, _command):
        if self.account is not None:
            self.account.sign_out()                    # its on_change stops Plip until they sign in again
            self.push()

    # -- a newer Plip: download it, or stop asking ----------------------------------------------
    def _cmd_update_download(self, _command):
        from mcp_vision.buddy.updates import RELEASES

        found = self.updates.available if self.updates is not None else None
        if found and found["url"].startswith(RELEASES):
            self.platform.open_url(found["url"])

    def _cmd_set_update_check(self, command):
        prefs = self.prefs
        prefs.update_check = bool(command.get("enabled"))
        prefs.save(self.prefs_path)
        self.check_updates()                           # on: ask now; off: the menu bar item goes away
        self.push()

    def _cmd_quick_connect(self, _command):
        """One button: use a ready AI, else sign in to an installed one; with none, point to the cards."""
        engines = self.engines()
        ready = next((engine for engine in engines if engine.get("status") == "ready"), None)
        if ready:
            self.connect_note = f"Connected to {ready['label']}."
            self._update_prefs(engine=ready["id"])
            return
        signed_out = next((engine for engine in engines if engine.get("kind") == "subscription"
                           and engine.get("status") in {"logged-out", "unknown"}), None)
        if signed_out and self.connector is not None:
            self.connect_note = ""
            self.connector.start(signed_out["id"])
        else:
            self.connect_note = "Pick the AI you use below, or get a free one from Google."
        self.push()

    def _cmd_engine_connect(self, command):
        """One click: install if missing, sign in via the browser, switch to it."""
        if self.connector is not None:
            self.connector.start(str(command.get("id", "")))
            self.push()
            return
        self._cmd_engine_login(command)

    def _cmd_engine_connect_cancel(self, command):
        if self.connector is not None:
            self.connector.cancel(str(command.get("id", "")))
            self.push()

    def _cmd_engine_connect_code(self, command):
        if self.connector is not None and isinstance(command.get("code"), str):
            self.connector.send_code(str(command.get("id", "")), command["code"][:500])

    # -- General → Support: a bug report or a feature request, only what they typed ------------------
    def _cmd_report_issue(self, command):
        from mcp_vision.analytics import report_issue

        message = str(command.get("message") or "").strip()[:5000]
        if not message:
            return
        engine = next((item["label"] for item in self.engines() if item.get("selected")), "")
        self.report_note = "sent" if report_issue(message, engine=engine) else "failed"
        self.push()

    def _cmd_request_feature(self, command):
        from mcp_vision.analytics import request_feature

        message = str(command.get("message") or "").strip()[:5000]
        if not message:
            return
        self.report_note = "sent" if request_feature(message) else "failed"
        self.push()

    def _cmd_report_reset(self, _command):
        self.report_note = ""
        self.push()

    def _cmd_engine_login(self, command):
        if self.connector is not None:               # browser sign-in, not Terminal
            self._cmd_engine_connect(command)
            return
        engine = next((item for item in self.engines() if item["id"] == command.get("id")), None)
        if engine and engine.get("login"):
            self.platform.run_in_terminal(engine["login"])

    def _cmd_copy(self, command):
        if isinstance(command.get("text"), str):
            self.platform.copy(command["text"])

    def _cmd_open_url(self, command):
        url = str(command.get("url", ""))
        if url.startswith(("https://", "x-apple.systempreferences:")):
            self.platform.open_url(url)

    def _cmd_grant(self, command):
        if command.get("permission") in {"screen", "accessibility", "microphone", "speech", "contacts", "automation",
                                         "fulldisk"}:
            self.platform.request_permission(command["permission"])
            self.push()

    def _cmd_restart_app(self, _command):
        self.platform.restart()

    def _cmd_test_voice(self, _command):
        talk = chord(self.prefs.hotkey).label.lower().replace(" + ", " and ")
        self.platform.say(f"Hey, I'm Plip. Hold {talk}, and ask me anything.")

    def _cmd_clear_usage(self, _command):
        if self.usage is not None:
            self.usage.clear()
            self.push()

    def _cmd_clear_history(self, _command):
        self.history.clear()
        self.push()

    def _cmd_open_settings(self, command):
        self.platform.open_settings(str(command.get("tab") or "home"))

    def _cmd_quit(self, _command):
        self.platform.quit()

    # -- memory ---------------------------------------------------------------------------
    def _cmd_memory_import(self, command):
        if command.get("source") in IMPORT_SOURCES:
            self.run_import(command["source"])

    def _cmd_memory_paste(self, command):
        from mcp_vision.buddy.memory.importers import parse_ai_memory

        source = command.get("source") if command.get("source") in AI_SOURCES else "ai"
        text = str(command.get("text") or "")
        if self.memory is None or not text.strip() or len(text) > 60_000:
            return
        self.memory.merge(source, parse_ai_memory(text))
        self.memory.save()
        self.push()

    def _cmd_memory_add(self, command):
        if self.memory is None:
            return
        if self.memory.add(str(command.get("key") or "note"), str(command.get("value") or ""), "you"):
            self.memory.save()
            self.push()

    def _cmd_memory_delete(self, command):
        if self.memory is not None and self.memory.remove(str(command.get("id") or "")):
            self.memory.save()
            self.push()

    def _cmd_memory_forget_source(self, command):
        if self.memory is not None and command.get("source"):
            self.memory.forget_source(str(command["source"]))
            self.memory.save()
            self.push()

    def _cmd_memory_copy_prompt(self, _command):
        from mcp_vision.buddy.memory.importers import MEMORY_PROMPT

        self.platform.copy(MEMORY_PROMPT)

    # -- companion style ------------------------------------------------------------------
    def _cmd_set_companion(self, command):
        if command.get("style") in {"notch", "cursor", "hidden"}:
            self._update_prefs(companion=command["style"])


def _key(settings: Any, name: str) -> str | None:
    import os

    attribute = {"ANTHROPIC_API_KEY": "anthropic_api_key", "GEMINI_API_KEY": "gemini_api_key",
                 "TYPESAFE_API_KEY": "typesafe_api_key",
                 "ELEVENLABS_API_KEY": "elevenlabs_api_key", "ASSEMBLYAI_API_KEY": "assemblyai_api_key"}.get(name)
    value = getattr(settings, attribute, None) if attribute else None
    return value or os.environ.get(name)
