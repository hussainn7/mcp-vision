"""Assemble a buddy from settings. Platform pieces are chosen at runtime."""
from __future__ import annotations

import sys
from typing import Any

from mcp_vision.buddy.capture import ScreenCapturer
from mcp_vision.buddy.companion import Companion, Pointer, Router, Snapper, Speaker
from mcp_vision.buddy.conversation import Conversation
from mcp_vision.buddy.settings import BuddySettings


class SetupError(RuntimeError):
    """A missing key or permission the user has to fix."""


def make_brain(settings: BuddySettings) -> Any:
    if not settings.anthropic_api_key:
        raise SetupError("ANTHROPIC_API_KEY is not set. Add it to your .env (see .env.example).")
    from mcp_vision.buddy.brain_claude import ClaudeBrain

    return ClaudeBrain(api_key=settings.anthropic_api_key, model=settings.model,
                       effort=settings.effort, max_tokens=settings.max_tokens)


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


def make_speaker(settings: BuddySettings) -> Speaker | None:
    if settings.tts.lower() == "off":
        return None
    from mcp_vision.buddy.speech_out import QueueSpeaker, default_voice

    voice, fallback = default_voice(settings)
    return QueueSpeaker(voice, fallback=fallback)


def make_companion(settings: BuddySettings, *, pointer: Pointer | None = None,
                   speaker: Speaker | None = None, capturer: ScreenCapturer | None = None,
                   brain: Any = None) -> Companion:
    jev = make_jev(settings)
    return Companion(
        brain=brain or make_brain(settings),
        capturer=capturer or ScreenCapturer(max_edge=settings.max_image_edge, quality=settings.jpeg_quality),
        speaker=speaker if speaker is not None else make_speaker(settings),
        pointer=pointer,
        router=make_router(settings, jev),
        snapper=make_snapper(settings, jev),
        conversation=Conversation(max_turns=settings.history_turns),
    )
