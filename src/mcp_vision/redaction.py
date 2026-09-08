"""Redact common credential fields before diagnostics leave the runtime."""
import re

_FIELD = re.compile(r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|authorization|cookie|secret)", re.I)
_QUERY = re.compile(r"([?&](?:key|api_key|token|access_token)=)[^&\s\"']+", re.I)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if _FIELD.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _BEARER.sub("Bearer [redacted]", _QUERY.sub(r"\1[redacted]", value))
    return value
