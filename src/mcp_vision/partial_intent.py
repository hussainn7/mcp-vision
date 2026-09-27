"""Safe partial-intent preparation with generation-scoped artifacts."""
from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Observer = Callable[[str], Any | Awaitable[Any]]


@dataclass(frozen=True)
class Preparation:
    capability: str
    observe_app: bool = True
    capture_window: bool = True
    attach_browser: bool = False
    warm_provider: bool = True
    resolve_app: bool = True


@dataclass
class PreparedArtifacts:
    generation: int
    partial: str
    preparation: Preparation
    values: dict[str, Any] = field(default_factory=dict)
    valid: bool = True


def _words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


def preparation_for(text: str) -> Preparation:
    """Classify by required runtime capability, never by a named task/domain."""
    words = set(_words(text))
    browser = bool(words & {"browser", "website", "web", "page", "tab", "url", "online",
                            "search", "navigate", "open", "click", "select", "fill"})
    capability = "act" if words & {"open", "click", "type", "fill", "select", "press", "submit"} \
        else "observe"
    return Preparation(capability=capability, attach_browser=browser)


def compatible(partial: str, final: str) -> bool:
    p, f = _words(partial), _words(final)
    if not p or not f:
        return False
    common = 0
    for left, right in zip(p, f):
        if left != right:
            break
        common += 1
    return common >= max(1, min(len(p), len(f)) * 2 // 3)


class PartialIntentWatcher:
    """Runs only injected read-only preparation and adopts it after final text."""

    def __init__(self, observers: dict[str, Observer] | None = None, *, metrics=None):
        self.observers = observers or {}
        self.metrics = metrics
        self.generation = 0
        self.current: PreparedArtifacts | None = None
        self._last = ""
        self._tasks: set[asyncio.Task] = set()
        self._sync_previous = ""

    async def partial(self, text: str) -> PreparedArtifacts | None:
        normalized = " ".join(text.split())
        if not normalized:
            return None
        if self.metrics and not self._last:
            self.metrics.mark("first_partial", generation=self.generation)
        old_words, new_words = _words(self._last), _words(normalized)
        stable = normalized == self._last or (len(new_words) >= 2 and old_words and new_words[:len(old_words)] == old_words)
        self._last = normalized
        if not stable:
            self.invalidate()
            return None
        self.invalidate()
        generation = self.generation
        plan = preparation_for(normalized)
        prepared = PreparedArtifacts(generation, normalized, plan)
        self.current = prepared
        if self.metrics:
            self.metrics.mark("stable_partial", generation=generation)
            self.metrics.mark("preparation_started", generation=generation)
        requested = {
            "app_observed": plan.observe_app,
            "window_captured": plan.capture_window,
            "browser_attached": plan.attach_browser,
            "provider_warm": plan.warm_provider,
            "app_resolved": plan.resolve_app,
        }

        async def run(name: str, callback: Observer):
            value = callback(normalized)
            if inspect.isawaitable(value):
                value = await value
            if prepared.valid and self.current is prepared:
                prepared.values[name] = value
                if self.metrics:
                    self.metrics.mark(name, generation=generation)

        for name, enabled in requested.items():
            if enabled and (callback := self.observers.get(name)):
                task = asyncio.create_task(run(name, callback))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        return prepared

    def invalidate(self) -> None:
        self.generation += 1
        if self.current:
            self.current.valid = False
        self.current = None
        for task in tuple(self._tasks):
            task.cancel()
        self._tasks.clear()

    def finalize(self, text: str) -> PreparedArtifacts | None:
        prepared = self.current
        if prepared and prepared.valid and compatible(prepared.partial, text):
            if self.metrics:
                self.metrics.mark("preparation_adopted", generation=prepared.generation)
            self.current = None
            return prepared
        if self.metrics:
            self.metrics.mark("preparation_discarded", generation=self.generation)
        self.invalidate()
        return None

    def cancel(self) -> None:
        self.invalidate()

    def observe(self, text: str) -> dict | None:
        """Compatibility path for the native UI; still preparation-only."""
        normalized = " ".join(text.split())
        previous = _words(self._sync_previous)
        current = _words(normalized)
        stable = bool(previous and len(current) >= 2 and current[:len(previous)] == previous)
        self._sync_previous = normalized
        if not stable:
            return None
        plan = preparation_for(normalized)
        return {"kind": plan.capability, "label": "Preparing…", "browser": plan.attach_browser}

    def reset(self) -> None:
        self.cancel()
        self._last = ""
        self._sync_previous = ""
