"""Buddy configuration from the environment or a local ``.env`` file."""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file() -> Path | None:
    candidates = (Path.cwd() / ".env", Path.home() / ".config" / "mcp-vision" / ".env",
                  Path(__file__).resolve().parents[3] / ".env")
    return next((path for path in candidates if path.is_file()), None)


class BuddySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUDDY_", extra="ignore", populate_by_name=True)

    # brain
    anthropic_api_key: str | None = Field(default=None, validation_alias=AliasChoices(
        "ANTHROPIC_API_KEY", "BUDDY_ANTHROPIC_API_KEY"))
    model: str = "claude-opus-5-5"
    effort: str = "low"                 # low | medium | high | xhigh | max
    max_tokens: int = 4096
    history_turns: int = 20             # messages, i.e. the last 10 exchanges (text only)

    # eyes
    max_image_edge: int = 1280
    jpeg_quality: int = 70

    # fast System-1 router (Jev). "auto" uses Jev when TYPESAFE_API_KEY is set.
    router: str = "auto"                # auto | jev | rules | off
    typesafe_api_key: str | None = Field(default=None, validation_alias=AliasChoices(
        "TYPESAFE_API_KEY", "BUDDY_TYPESAFE_API_KEY"))
    typesafe_base_url: str = Field(default="https://api.typesafe.ai", validation_alias=AliasChoices(
        "TYPESAFE_BASE_URL", "BUDDY_TYPESAFE_BASE_URL"))
    typesafe_model: str = Field(default="jev-latest", validation_alias=AliasChoices(
        "TYPESAFE_DEFAULT_MODEL", "TYPESAFE_MODEL", "BUDDY_TYPESAFE_MODEL"))
    jev_timeout: float = 2.5
    snap_to_elements: bool = True

    # voice out
    tts: str = "auto"                   # auto | elevenlabs | say | print | off
    elevenlabs_api_key: str | None = Field(default=None, validation_alias=AliasChoices(
        "ELEVENLABS_API_KEY", "BUDDY_ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = "kPzsL2i3teMYv0FxEYQ6"     # the voice Clicky ships with
    elevenlabs_model: str = "eleven_flash_v2_5"
    say_voice: str | None = None

    # voice in
    stt: str = "apple"                  # apple | assemblyai
    assemblyai_api_key: str | None = Field(default=None, validation_alias=AliasChoices(
        "ASSEMBLYAI_API_KEY", "BUDDY_ASSEMBLYAI_API_KEY"))

    # shortcut: hold to talk
    hotkey: str = "ctrl+option"         # ctrl+option | option+space


def load_settings(**overrides) -> BuddySettings:
    return BuddySettings(_env_file=_env_file(), **overrides)
