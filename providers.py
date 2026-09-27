"""Provider-neutral model descriptors and structured response contracts."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


class CapabilityError(ValueError):
    pass


class StructuredOutputError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    tools: bool = True
    structured_output: bool = True
    continuation: bool = True
    vision: bool = False


@dataclass(frozen=True)
class Provider:
    name: str
    capabilities: ProviderCapabilities
    key_env: str | None = None


PROVIDERS = {
    "local": Provider("local", ProviderCapabilities(continuation=False, vision=True)),
    "anthropic": Provider("anthropic", ProviderCapabilities(vision=True), "ANTHROPIC_API_KEY"),
    "openai": Provider("openai", ProviderCapabilities(vision=True), "OPENAI_API_KEY"),
    "gemini": Provider("gemini", ProviderCapabilities(vision=True), "GEMINI_API_KEY"),
    "nvidia": Provider("nvidia", ProviderCapabilities(vision=True), "NVIDIA_API_KEY"),
    # Optional bounded selector. It is never a required default.
    "jev": Provider("jev", ProviderCapabilities(tools=False, vision=False), "JEV_API_KEY"),
}


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    @classmethod
    def parse(cls, value: str) -> "ModelRef":
        provider, separator, model = (value or "").partition(":")
        if not separator or not provider.strip() or not model.strip():
            raise ValueError("model references must use provider:model")
        provider = provider.strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider '{provider}'")
        return cls(provider, model.strip())

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class ModelReply:
    value: dict
    continuation_id: str | None = None
    provider: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)


def require_capabilities(ref: ModelRef, *required: str) -> None:
    capabilities = PROVIDERS[ref.provider].capabilities
    unsupported = [name for name in required if not getattr(capabilities, name, False)]
    if unsupported:
        raise CapabilityError(f"{ref.provider} does not support: {', '.join(sorted(unsupported))}")


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool), "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def validate_structured_output(value: Any, schema: dict) -> dict:
    """Validate the bounded JSON-schema subset used by policy decisions."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise StructuredOutputError("provider returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise StructuredOutputError("structured output must be an object")

    def check(item: Any, spec: dict, path: str):
        expected = spec.get("type")
        if expected and not _matches_type(item, expected):
            raise StructuredOutputError(f"{path} must be {expected}")
        if isinstance(item, dict):
            for key in spec.get("required", []):
                if key not in item:
                    raise StructuredOutputError(f"{path}.{key} is required")
            properties = spec.get("properties", {})
            if spec.get("additionalProperties") is False:
                extra = set(item) - set(properties)
                if extra:
                    raise StructuredOutputError(f"{path} has unsupported fields: {', '.join(sorted(extra))}")
            for key, child in properties.items():
                if key in item:
                    check(item[key], child, f"{path}.{key}")
        if isinstance(item, list) and spec.get("items"):
            for index, child in enumerate(item):
                check(child, spec["items"], f"{path}[{index}]")
        if "enum" in spec and item not in spec["enum"]:
            raise StructuredOutputError(f"{path} is outside the allowed enum")
    check(value, schema, "output")
    return value


def invoke_structured(call: Callable, ref: ModelRef, messages: list[dict], schema: dict,
                      *, continuation_id: str | None = None) -> ModelReply:
    require_capabilities(ref, "structured_output")
    raw = call(messages, response_format={"type": "json_schema", "json_schema": schema},
               continuation_id=continuation_id)
    if isinstance(raw, ModelReply):
        raw.value = validate_structured_output(raw.value, schema)
        return raw
    content = raw.get("structured") if isinstance(raw, dict) and "structured" in raw else raw.get("content", raw)
    return ModelReply(value=validate_structured_output(content, schema),
                      continuation_id=raw.get("continuation_id") if isinstance(raw, dict) else None,
                      provider=ref.provider, model=ref.model,
                      usage=raw.get("usage", {}) if isinstance(raw, dict) else {})
