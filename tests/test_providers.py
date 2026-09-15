from mcp_vision.contextual import infer_capability
from mcp_vision.providers import normalize_provider, resolve_provider


def test_provider_aliases():
    assert normalize_provider("Claude") == "anthropic"
    assert normalize_provider("chatgpt") == "openai"
    assert normalize_provider("gpt") == "openai"
    assert normalize_provider("ollama") == "local"


def test_auto_falls_back_to_local_without_keys(monkeypatch):
    import config
    monkeypatch.setattr(config.cfg, "anthropic_api_key", None)
    monkeypatch.setattr(config.cfg, "openai_api_key", None)
    monkeypatch.setattr(config.cfg, "gemini_api_key", None)
    monkeypatch.setattr(config.cfg, "nvidia_api_key", None)
    assert resolve_provider("auto") == "local"


def test_search_requests_route_to_act():
    assert infer_capability("Search for flights in SF") == "act"
    assert infer_capability("Find hotels near me") == "act"
    assert infer_capability("What does this error mean?") == "ask"
