"""Resolve intelligence roles independently of any provider."""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from typing import Callable, Literal

from providers import (CapabilityError, ModelRef, ModelReply, StructuredOutputError,
                       invoke_structured, require_capabilities)

Role = Literal["fast_policy", "writer", "planner", "reviewer", "vision"]
ROLES = ("fast_policy", "writer", "planner", "reviewer", "vision")
ROLE_CAPABILITIES = {
    "fast_policy": ("structured_output",), "writer": (), "planner": ("tools",),
    "reviewer": ("structured_output",), "vision": ("vision",),
}


@dataclass(frozen=True)
class ResolvedRole:
    role: Role
    primary: ModelRef
    fallbacks: tuple[ModelRef, ...] = ()


def resolve_role(config, role: Role) -> ResolvedRole | None:
    if role not in ROLES:
        raise ValueError(f"unknown model role '{role}'")
    roles = getattr(config, "model_roles", {}) or {}
    raw = roles.get(role)
    if raw is None and role == "vision":
        return None
    if not raw:
        raw = f"{config.model_backend}:{config.planning_model}"
    primary = ModelRef.parse(raw)
    require_capabilities(primary, *ROLE_CAPABILITIES[role])
    fallback_values = (getattr(config, "model_role_fallbacks", {}) or {}).get(role, [])
    fallbacks = []
    for value in fallback_values:
        ref = ModelRef.parse(value)
        require_capabilities(ref, *ROLE_CAPABILITIES[role])
        if ref != primary:
            fallbacks.append(ref)
    return ResolvedRole(role, primary, tuple(fallbacks))


class RoleRunner:
    """Execute a role with bounded timeout and ordered provider fallback."""
    def __init__(self, resolver: Callable[[ModelRef], Callable], *, timeout_s: float = 20):
        self.resolver, self.timeout_s = resolver, timeout_s

    def structured(self, route: ResolvedRole, messages: list[dict], schema: dict,
                   *, continuation_id: str | None = None) -> ModelReply:
        errors = []
        for ref in (route.primary, *route.fallbacks):
            try:
                call = self.resolver(ref)
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                try:
                    future = pool.submit(invoke_structured, call, ref, messages, schema,
                                         continuation_id=continuation_id)
                    return future.result(timeout=self.timeout_s)
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
            except (TimeoutError, concurrent.futures.TimeoutError, CapabilityError,
                    StructuredOutputError, OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{ref.provider}:{type(exc).__name__}")
        raise RuntimeError("all configured role providers failed: " + ", ".join(errors))
