"""Worker threads that clean up after macOS calls.

PyObjC work on a thread (Accessibility walks, Quartz grabs, AppKit) autoreleases objects that only go
away when that thread's autorelease pool drains. An executor's threads live as long as the app, so
without a pool per job they never drain.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
from collections.abc import Callable
from typing import Any

try:                                   # macOS; elsewhere there's nothing to drain
    from objc import autorelease_pool as _pool
except ImportError:                    # pragma: no cover - Linux CI
    _pool = contextlib.nullcontext


def _drained(fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
    with _pool():
        return fn(*args, **kwargs)


class PooledExecutor(concurrent.futures.ThreadPoolExecutor):
    """A ThreadPoolExecutor that wraps every job in its own autorelease pool."""

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(_drained, fn, args, kwargs)


def install(loop) -> PooledExecutor:
    """Make ``loop``'s default executor (what ``asyncio.to_thread`` uses) a pooled one."""
    executor = PooledExecutor(thread_name_prefix="plip-work")
    loop.set_default_executor(executor)
    return executor
