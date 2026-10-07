"""Tells you when a newer Plip is out, so you can download it.

Once a day Plip asks GitHub for the latest release of hussainn7/plip-oss: one GET to api.github.com with
nothing about you in it (GitHub sees the request, like any download). A newer version shows in the menu
bar, on Home and in Settings → General, and once in the notch. Download opens the release's DMG.

Turn it off in Settings → General → Tell me about new versions: then nothing is ever asked.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp_vision import __version__

log = logging.getLogger("mcp_vision.buddy.updates")

LATEST = "https://api.github.com/repos/hussainn7/plip-oss/releases/latest"
RELEASES = "https://github.com/hussainn7/plip-oss/releases/"     # Download only ever opens a page under here
EVERY = 24 * 60 * 60                                             # ask GitHub at most once a day


def version_tuple(text: str) -> tuple[int, ...] | None:
    """'v0.9.0' -> (0, 9, 0); None for anything that isn't a plain release number (betas, junk)."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def newer(latest: str, current: str) -> bool:
    a, b = version_tuple(latest), version_tuple(current)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def github_latest(url: str = LATEST) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"Plip/{__version__}"})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read() or b"{}")
    return data if isinstance(data, dict) else {}


def _release(data: dict[str, Any]) -> dict[str, str] | None:
    """The parts Plip uses: the version, the DMG to download (else the release page), and the page."""
    version = str(data.get("tag_name") or "").strip().lstrip("v")
    if data.get("draft") or data.get("prerelease") or not version_tuple(version):
        return None
    page = str(data.get("html_url") or "")
    page = page if page.startswith(RELEASES) else RELEASES + "latest"
    dmg = next((str(asset.get("browser_download_url") or "") for asset in data.get("assets") or []
                if isinstance(asset, dict) and str(asset.get("name") or "").endswith(".dmg")), "")
    return {"version": version, "url": dmg if dmg.startswith(RELEASES) else page, "page": page}


class Updates:
    def __init__(self, current: str = __version__, *, path: Path | None = None,
                 fetch: Callable[[], dict[str, Any]] = github_latest, enabled: Callable[[], bool] = lambda: True,
                 on_found: Callable[[dict[str, str]], None] = lambda release: None,
                 clock: Callable[[], float] = time.time):
        from mcp_vision.paths import state_dir

        self.current = current
        self.path = path or state_dir() / "updates.json"
        self.fetch = fetch
        self.enabled = enabled
        self.on_found = on_found               # a newer version, the first time it's seen (the notch says so once)
        self.clock = clock

    def _state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state))

    @property
    def available(self) -> dict[str, str] | None:
        """The newer release to offer, or None (up to date, never checked, or turned off)."""
        if not self.enabled():
            return None
        latest = self._state().get("latest")
        if isinstance(latest, dict) and newer(str(latest.get("version") or ""), self.current):
            return {key: str(latest.get(key) or "") for key in ("version", "url", "page")}
        return None

    def check(self, force: bool = False) -> dict[str, str] | None:
        """Ask GitHub (once a day unless forced; offline keeps the last answer). Returns the newer release."""
        if not self.enabled():
            return None
        state = self._state()
        checked = state.get("checked")
        if force or not isinstance(checked, (int, float)) or self.clock() - checked >= EVERY:
            try:
                release = _release(self.fetch())
            except Exception as exc:
                log.info("updates: couldn't ask GitHub (%s)", type(exc).__name__)
                return self.available
            state["checked"] = self.clock()
            if release is not None:
                state["latest"] = release
            self._save(state)
        found = self.available
        if found and state.get("told") != found["version"]:
            state["told"] = found["version"]
            self._save(state)
            self.on_found(found)
        return found

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self.enabled(), "current": self.current, "available": self.available}


__all__ = ["RELEASES", "Updates", "newer", "version_tuple"]
