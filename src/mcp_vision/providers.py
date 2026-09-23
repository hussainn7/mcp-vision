"""Friendly model-provider names → backends.py backends."""
from __future__ import annotations

ALIASES = {
    "auto": "auto",
    "local": "local",
    "ollama": "local",
    "openrouter": "openrouter",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "chatgpt": "openai",
    "gpt": "openai",
    "openai": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "nvidia": "nvidia",
}

LABELS = (
    ("OpenRouter", "openrouter"),
    ("Auto", "auto"),
    ("Claude", "anthropic"),
    ("ChatGPT", "openai"),
    ("Gemini", "gemini"),
)


def normalize_provider(name: str | None) -> str:
    text = (name or "auto").strip().lower()
    return ALIASES.get(text, text or "auto")


def available_backends() -> list[str]:
    from config import cfg
    found = []
    if cfg.openrouter_api_key:
        found.append("openrouter")
    if cfg.anthropic_api_key:
        found.append("anthropic")
    if cfg.openai_api_key:
        found.append("openai")
    if cfg.gemini_api_key:
        found.append("gemini")
    if cfg.nvidia_api_key:
        found.append("nvidia")
    return found


def resolve_provider(name: str | None = None) -> str:
    """Pick an executable backend. Auto prefers configured cloud providers."""
    from config import cfg
    choice = normalize_provider(name if name is not None else cfg.model_backend)
    if choice == "auto":
        for backend in ("openrouter", "anthropic", "openai", "gemini", "nvidia"):
            if backend in available_backends():
                return backend
        return "openrouter"
    if choice not in {"local", "openrouter", "anthropic", "openai", "gemini", "nvidia"}:
        raise ValueError(f"Unknown provider '{name}'. Use OpenRouter, Auto, Local, Claude, ChatGPT, or Gemini.")
    return choice


def provider_label(backend: str) -> str:
    for label, value in LABELS:
        if value == backend or (backend != "auto" and normalize_provider(label) == backend):
            if value == "auto":
                continue
            return label
    return backend
