"""Secret-safe readiness for configured intelligence roles."""
from __future__ import annotations

import os

from providers import PROVIDERS
from reasoning.model_routing import ROLES, resolve_role


def role_readiness(config) -> dict:
    roles = {}
    for role in ROLES:
        try:
            route = resolve_role(config, role)
            if route is None:
                roles[role] = {"status": "optional_unconfigured"}
                continue
            provider = PROVIDERS[route.primary.provider]
            configured = provider.key_env is None or bool(os.environ.get(provider.key_env))
            roles[role] = {
                "status": "configured_unverified" if configured else "missing_credentials",
                "model": str(route.primary), "fallbacks": [str(ref) for ref in route.fallbacks],
                "capabilities": {
                    name: getattr(provider.capabilities, name)
                    for name in ("tools", "structured_output", "continuation", "vision")
                },
            }
        except Exception as exc:
            roles[role] = {"status": "invalid", "error": type(exc).__name__}
    return {"roles": roles, "secrets": "redacted"}
