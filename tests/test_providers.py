from mcp_vision.contextual import infer_capability
from mcp_vision.providers import normalize_provider, resolve_provider


def test_provider_aliases():
    assert normalize_provider("Claude") == "anthropic"
    assert normalize_provider("chatgpt") == "openai"
    assert normalize_provider("gpt") == "openai"
    assert normalize_provider("ollama") == "local"
    assert normalize_provider("OpenRouter") == "openrouter"


def test_auto_defaults_to_openrouter_without_keys(monkeypatch):
    import config
    monkeypatch.setattr(config.cfg, "openrouter_api_key", None)
    monkeypatch.setattr(config.cfg, "anthropic_api_key", None)
    monkeypatch.setattr(config.cfg, "openai_api_key", None)
    monkeypatch.setattr(config.cfg, "gemini_api_key", None)
    monkeypatch.setattr(config.cfg, "nvidia_api_key", None)
    assert resolve_provider("auto") == "openrouter"


def test_auto_prefers_configured_openrouter(monkeypatch):
    import config
    monkeypatch.setattr(config.cfg, "openrouter_api_key", "configured")
    assert resolve_provider("auto") == "openrouter"


def test_search_requests_route_to_read_only_ask():
    assert infer_capability("Search for flights in SF") == "ask"
    assert infer_capability("Find hotels near me") == "ask"
    assert infer_capability("What does this error mean?") == "ask"
    assert infer_capability("How do I enable this?") == "guide"
