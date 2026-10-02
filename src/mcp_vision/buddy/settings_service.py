"""Backs Plip's Settings window: builds its snapshot and handles its commands.

Platform-neutral. Everything that touches macOS (clipboard, Terminal,
permission prompts, quitting) arrives through ``Platform`` so the logic is
tested on any OS.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_vision import __version__
from mcp_vision.buddy.store import History, Prefs, config_dir

KEY_NAMES = {"ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "ELEVENLABS_API_KEY", "ASSEMBLYAI_API_KEY"}
DEPTHS = {"fast", "balanced", "deep"}
SKILL_IDS = ("apps", "files", "system", "writing", "planning", "travel", "memory", "forms", "messages", "routines")
IMPORT_SOURCES = {"contacts", "autofill", "mail", "imessage"}
AI_SOURCES = {"chatgpt", "claude", "gemini", "ai"}


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
    routines: Any = None                                   # buddy.routines.Routines
    action_log: Any = None                                 # buddy.actions.ActionLog (for suggestions)
    phone_status: Callable[[], dict] = lambda: {"status": "off"}

    @property
    def prefs(self) -> Prefs:
        return Prefs.load(self.prefs_path)

    def snapshot(self) -> dict[str, Any]:
        prefs = self.prefs
        settings = self.settings()
        engines = self.engines()
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
            "permissions": {"screen": perms.get("screenRecording"), "accessibility": perms.get("accessibility"),
                            "microphone": perms.get("microphone"), "speech": perms.get("speechRecognition")},
            "voice": {"tts": tts, "stt": stt, "elevenlabs": keys["ELEVENLABS_API_KEY"],
                      "assemblyai": keys["ASSEMBLYAI_API_KEY"]},
            "jev": {"configured": keys["TYPESAFE_API_KEY"], "enabled": settings.router != "off", "latencyMs": None},
            "keys": keys,
            "history": self.history.items()[-50:],
            "memory": self.memory.panel() if self.memory is not None else None,
            "skills": {skill: bool(prefs.skills.get(skill, True)) for skill in SKILL_IDS},
            "companion": prefs.companion,
            "phone": {"enabled": bool(prefs.phone.get("enabled")), "handles": list(prefs.phone.get("handles") or []),
                      "prefix": prefs.phone.get("prefix") or "/plip",
                      "detected": list(self.memory.handles) if self.memory is not None else [],
                      **self.phone_status()},
            "routines": self.routines.cards() if self.routines is not None else [],
            "suggestions": self._suggestions(),
            "stats": self._stats(),
        }

    def _suggestions(self) -> list[dict]:
        if self.routines is None or self.action_log is None:
            return []
        from mcp_vision.buddy.routines import suggest_routines

        return suggest_routines(self.action_log.entries(), self.routines.items, self.routines.dismissed)

    def _stats(self) -> dict:
        entries = self.action_log.entries() if self.action_log is not None else []
        week = [entry for entry in entries if entry.get("at", 0) > time.time() - 7 * 86400 and entry.get("ok")]
        answered = len(self.history.items())
        return {"actionsWeek": len(week), "answers": answered,
                "minutesSaved": round(len(week) * 0.75 + answered * 1.5)}

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

    def _cmd_set_walkthroughs(self, command):
        self._update_prefs(walkthroughs=bool(command.get("enabled")))

    def _cmd_set_voice(self, command):
        changes = {}
        if command.get("tts") in {"elevenlabs", "say", "off"}:
            changes["tts"] = command["tts"]
        if command.get("stt") in {"assemblyai", "apple"}:
            changes["stt"] = command["stt"]
        if changes:
            self._update_prefs(**changes)

    def _cmd_engine_login(self, command):
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
        if command.get("permission") in {"screen", "accessibility", "microphone", "speech"}:
            self.platform.request_permission(command["permission"])
            self.push()

    def _cmd_test_voice(self, _command):
        self.platform.say("Hey, I'm Plip. Hold control and option, and ask me anything.")

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

    # -- skills, companion style, phone ----------------------------------------------------------
    def _cmd_set_skill(self, command):
        if command.get("skill") in SKILL_IDS:
            skills = dict(self.prefs.skills)
            skills[command["skill"]] = bool(command.get("enabled"))
            self._update_prefs(skills=skills)

    def _cmd_set_companion(self, command):
        if command.get("style") in {"notch", "cursor", "hidden"}:
            self._update_prefs(companion=command["style"])

    # -- routines ----------------------------------------------------------------------------------
    def _cmd_routine_accept(self, command):
        if self.routines is None:
            return
        suggestion = next((item for item in self._suggestions() if item["key"] == command.get("key")), None)
        if suggestion is not None:
            self.routines.add(command.get("name") or suggestion["name"], command.get("phrase") or suggestion["phrase"],
                              suggestion["steps"], source="suggested")
            self.routines.save()
            self.reload()
            self.push()

    def _cmd_routine_dismiss(self, command):
        if self.routines is not None and command.get("key"):
            self.routines.dismissed.append(str(command["key"]))
            self.routines.save()
            self.push()

    def _cmd_routine_delete(self, command):
        if self.routines is not None and self.routines.remove(str(command.get("id") or "")):
            self.routines.save()
            self.reload()
            self.push()

    def _cmd_set_phone(self, command):
        phone = dict(self.prefs.phone)
        if "enabled" in command:
            phone["enabled"] = bool(command["enabled"])
        if isinstance(command.get("handles"), list):
            phone["handles"] = [str(item).strip() for item in command["handles"] if str(item).strip()][:5]
        if isinstance(command.get("prefix"), str) and command["prefix"].strip().startswith("/"):
            phone["prefix"] = command["prefix"].strip().split()[0][:16]
        self._update_prefs(phone=phone)


def _key(settings: Any, name: str) -> str | None:
    import os

    attribute = {"ANTHROPIC_API_KEY": "anthropic_api_key", "TYPESAFE_API_KEY": "typesafe_api_key",
                 "ELEVENLABS_API_KEY": "elevenlabs_api_key", "ASSEMBLYAI_API_KEY": "assemblyai_api_key"}.get(name)
    value = getattr(settings, attribute, None) if attribute else None
    return value or os.environ.get(name)
