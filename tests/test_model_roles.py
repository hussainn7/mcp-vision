import time
from types import SimpleNamespace

import pytest

from providers import (CapabilityError, ModelRef, StructuredOutputError,
                       validate_structured_output)
from readiness import role_readiness
from reasoning.model_routing import RoleRunner, resolve_role


SCHEMA = {
    "type": "object", "required": ["index"], "additionalProperties": False,
    "properties": {"index": {"type": "integer"}},
}


def _config(**overrides):
    roles = {"fast_policy": "openai:cheap", "writer": "anthropic:writer",
             "planner": "local:planner", "reviewer": "gemini:reviewer", "vision": None}
    roles.update(overrides.pop("model_roles", {}))
    return SimpleNamespace(model_roles=roles, model_role_fallbacks=overrides.pop(
        "model_role_fallbacks", {"fast_policy": ["local:fallback"]}),
        model_backend="local", planning_model="default", **overrides)


def test_provider_neutral_role_resolution_and_optional_vision():
    route = resolve_role(_config(), "fast_policy")
    assert str(route.primary) == "openai:cheap"
    assert [str(ref) for ref in route.fallbacks] == ["local:fallback"]
    assert resolve_role(_config(), "vision") is None


def test_unsupported_capability_is_rejected():
    with pytest.raises(CapabilityError):
        resolve_role(_config(model_roles={"planner": "jev:selector"}), "planner")


def test_malformed_structured_output_is_rejected():
    with pytest.raises(StructuredOutputError):
        validate_structured_output("{not-json", SCHEMA)
    with pytest.raises(StructuredOutputError):
        validate_structured_output({"index": "1"}, SCHEMA)


def test_timeout_and_invalid_output_fall_back():
    route = resolve_role(_config(), "fast_policy")
    calls = []
    def resolver(ref):
        def call(messages, **kwargs):
            calls.append(ref.provider)
            if ref.provider == "openai":
                time.sleep(.05)
                return {"content": '{"index": 0}'}
            return {"content": '{"index": 2}', "continuation_id": "next-2"}
        return call
    reply = RoleRunner(resolver, timeout_s=.005).structured(route, [], SCHEMA,
                                                            continuation_id="previous-1")
    assert reply.value == {"index": 2}
    assert reply.continuation_id == "next-2"
    assert calls[:2] == ["openai", "local"]


def test_continuation_round_trip():
    seen = []
    route = resolve_role(_config(model_role_fallbacks={}), "fast_policy")
    def resolver(ref):
        def call(messages, **kwargs):
            seen.append(kwargs["continuation_id"])
            return {"content": {"index": 1}, "continuation_id": "response-9"}
        return call
    reply = RoleRunner(resolver).structured(route, [], SCHEMA, continuation_id="response-8")
    assert seen == ["response-8"] and reply.continuation_id == "response-9"


def test_readiness_never_contains_secret(monkeypatch):
    secret = "top-secret-provider-token"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    report = role_readiness(_config())
    assert report["roles"]["fast_policy"]["status"] == "configured_unverified"
    assert secret not in repr(report)
    assert report["secrets"] == "redacted"
