"""Assemble Plip from settings + preferences. Platform pieces are chosen at runtime."""
from __future__ import annotations

import sys
from typing import Any

from mcp_vision.buddy.capture import ScreenCapturer
from mcp_vision.buddy.companion import Companion, Observer, Pointer, Router, Snapper, Speaker
from mcp_vision.buddy.conversation import Conversation
from mcp_vision.buddy.settings import BuddySettings
from mcp_vision.buddy.store import Prefs

DEPTH_EFFORT = {"fast": "low", "balanced": "medium", "deep": "high"}


class SetupError(RuntimeError):
    """A missing key or permission the user has to fix."""


def apply_prefs(settings: BuddySettings, prefs: Prefs | None) -> BuddySettings:
    """User choices from the Settings window win over environment defaults."""
    if prefs is None:
        return settings
    changes: dict[str, Any] = {}
    if prefs.depth in DEPTH_EFFORT and "effort" not in settings.model_fields_set:
        changes["effort"] = DEPTH_EFFORT[prefs.depth]
    if prefs.tts in {"elevenlabs", "say", "off"}:
        changes["tts"] = prefs.tts
    if prefs.stt in {"assemblyai", "apple", "parakeet"}:
        changes["stt"] = prefs.stt
    if prefs.engine:
        changes["engine"] = prefs.engine
    return settings.model_copy(update=changes) if changes else settings


def make_brain(settings: BuddySettings, engines: list | None = None) -> Any:
    from mcp_vision.buddy.engines import choose_engine, make_engine_brain

    engine = choose_engine(settings, engines)
    if engine is None:
        raise SetupError("Pick a brain: sign in to Claude Code, Codex, or Cursor, or add an API key in Plip's settings.")
    return make_engine_brain(engine, settings)


def make_jev(settings: BuddySettings):
    if not settings.typesafe_api_key:
        return None
    from mcp_vision.buddy.jev import JevClient

    return JevClient(settings.typesafe_api_key, base_url=settings.typesafe_base_url,
                     model=settings.typesafe_model, timeout=settings.jev_timeout)


def make_router(settings: BuddySettings, jev=None) -> Router | None:
    from mcp_vision.buddy.router import JevRouter, RuleRouter

    choice = settings.router.lower()
    if choice == "off":
        return None
    if choice in {"auto", "jev"} and jev is not None:
        return JevRouter(jev)
    if choice == "jev":
        raise SetupError("BUDDY_ROUTER=jev needs TYPESAFE_API_KEY.")
    return RuleRouter()


def make_snapper(settings: BuddySettings, jev=None) -> Snapper | None:
    if not settings.snap_to_elements or sys.platform != "darwin":
        return None
    from mcp_vision.buddy.ax_locator import elements_near
    from mcp_vision.buddy.snap import ElementSnapper, JevChooser

    return ElementSnapper(elements_near, JevChooser(jev) if jev is not None else None)


def make_context(settings: BuddySettings):
    if sys.platform != "darwin":
        return None
    from mcp_vision.buddy.ax_context import MacAXContext

    return MacAXContext()


def make_actions(settings: BuddySettings, prefs: Prefs | None = None, *, memory=None,
                 host=None, source: str = "voice"):
    from mcp_vision.buddy.actions import ActionContext, ActionEngine, ActionLog
    from mcp_vision.buddy.actions.host import default_host
    from mcp_vision.paths import state_dir

    ctx = ActionContext(host=host or default_host(), memory=memory)
    return ActionEngine(ctx, log=ActionLog(), undo_path=state_dir() / "undo.json", source=source)


def make_speaker(settings: BuddySettings) -> Speaker | None:
    if settings.tts.lower() == "off":
        return None
    from mcp_vision.buddy.speech_out import QueueSpeaker, default_voice

    voice, fallback = default_voice(settings)
    return QueueSpeaker(voice, fallback=fallback)


def make_companion(settings: BuddySettings, *, pointer: Pointer | None = None,
                   speaker: Speaker | None = None, capturer: ScreenCapturer | None = None,
                   brain: Any = None, observer: Observer | None = None, prefs: Prefs | None = None,
                   engines: list | None = None, watch: bool = False, actions=None,
                   notes=None, memory=None, usage=None, usage_kind: str = "voice") -> Companion:
    settings = apply_prefs(settings, prefs)
    jev = make_jev(settings)
    capturer = capturer or ScreenCapturer(max_edge=settings.max_image_edge, quality=settings.jpeg_quality)
    watcher = None
    if watch and hasattr(capturer, "fingerprint"):
        from mcp_vision.buddy.watch import ScreenWatcher

        watcher = ScreenWatcher(capturer.fingerprint)
    return Companion(
        brain=brain or make_brain(settings, engines),
        capturer=capturer,
        speaker=speaker if speaker is not None else make_speaker(settings),
        pointer=pointer,
        router=make_router(settings, jev),
        snapper=make_snapper(settings, jev),
        conversation=Conversation(max_turns=settings.history_turns),
        context=make_context(settings),
        observer=observer,
        watcher=watcher,
        walkthroughs=prefs.walkthroughs if prefs is not None else True,
        actions=actions if actions is not None else make_actions(settings, prefs, memory=memory),
        notes=notes if notes is not None else make_notes(memory),
        usage=usage,
        usage_kind=usage_kind,
    )


def make_notes(memory=None):
    """What Plip knows about the user, added to the system prompt."""
    if memory is None:
        return None
    return memory.summary


__all__ = ["SetupError", "apply_prefs", "make_actions", "make_brain", "make_companion", "make_jev", "make_notes",
           "make_router",
           "make_speaker"]
