"""Chrome profile discovery and resolver.

Maps human profile names (Personal, Work, School, or email addresses)
to Chrome profile directories (Default, Profile 1, Profile 2, etc.)
by inspecting Chrome's Local State file.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def get_default_chrome_user_data_dir() -> Optional[Path]:
    """Find the host system's real Google Chrome user data directory."""
    if sys.platform == "darwin":
        p = Path.home() / "Library/Application Support/Google/Chrome"
        if p.exists():
            return p
    elif sys.platform.startswith("linux"):
        for name in ("google-chrome", "chromium", "google-chrome-stable"):
            p = Path.home() / f".config/{name}"
            if p.exists():
                return p
    elif sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            p = Path(local_app_data) / "Google/Chrome/User Data"
            if p.exists():
                return p
    return None


def get_profile_info_cache(user_data_dir: Optional[Path | str] = None) -> Dict[str, Dict[str, Any]]:
    """Read Chrome's Local State and extract the profile cache."""
    base = Path(user_data_dir) if user_data_dir else get_default_chrome_user_data_dir()
    if not base or not base.exists():
        return {}

    local_state_file = base / "Local State"
    if not local_state_file.exists():
        return {}

    try:
        data = json.loads(local_state_file.read_text(encoding="utf-8"))
        return data.get("profile", {}).get("info_cache", {})
    except Exception:
        return {}


def list_profiles(user_data_dir: Optional[Path | str] = None) -> Dict[str, Dict[str, Any]]:
    """List all available Chrome profiles with directory key and metadata."""
    cache = get_profile_info_cache(user_data_dir)
    profiles = {}
    for dir_name, info in cache.items():
        name = info.get("name") or dir_name
        email = info.get("user_name") or ""
        gaia_name = info.get("gaia_name") or ""
        profiles[dir_name] = {
            "dir_name": dir_name,
            "name": name,
            "email": email,
            "gaia_name": gaia_name,
            "active_time": info.get("active_time", 0),
        }
    return profiles


def resolve_profile(query: str, user_data_dir: Optional[Path | str] = None) -> Tuple[str, str]:
    """Resolve a user-provided profile name/alias to (dir_name, display_name).

    Handles aliases like 'personal', 'work', 'school', email addresses,
    exact directory names ('Profile 1', 'Default'), or custom names.
    """
    profiles = list_profiles(user_data_dir)
    if not profiles:
        # Fallback: return query directly as directory name
        return query.strip() or "Default", query.strip() or "Default"

    q = (query or "").strip().lower()
    if not q:
        return "Default", profiles.get("Default", {}).get("name", "Default")

    # 1. Exact directory name match (e.g. "Default", "Profile 1")
    for dir_name, p in profiles.items():
        if dir_name.lower() == q:
            return dir_name, p["name"]

    # 2. Exact profile name or email match
    for dir_name, p in profiles.items():
        if p["name"].lower() == q or (p["email"] and p["email"].lower() == q):
            return dir_name, p["name"]

    # 3. Semantic keyword alias: 'work'
    if q in ("work", "job", "office", "corp", "enterprise"):
        for dir_name, p in profiles.items():
            if "work" in p["name"].lower() or "work" in p["gaia_name"].lower():
                return dir_name, p["name"]

    # 4. Semantic keyword alias: 'school' / 'study' / 'university'
    if q in ("school", "student", "uni", "university", "college", "edu"):
        for dir_name, p in profiles.items():
            combined = f"{p['name']} {p['email']}".lower()
            if any(k in combined for k in ("school", "student", "edu", "uni", "college")):
                return dir_name, p["name"]

    # 5. Semantic keyword alias: 'personal' / 'home' / 'main'
    if q in ("personal", "home", "main", "default", "primary"):
        for dir_name, p in profiles.items():
            if "personal" in p["name"].lower():
                return dir_name, p["name"]
        if "Default" in profiles:
            return "Default", profiles["Default"]["name"]

    # 6. Substring match on profile name or email
    for dir_name, p in profiles.items():
        if q in p["name"].lower() or (p["email"] and q in p["email"].lower()):
            return dir_name, p["name"]

    # If no match, return query as directory name
    return query, query


_PROFILE_PROMPT_RE = re.compile(
    r"\b(?:use|using|in|with)\s+(?:my\s+)?([a-zA-Z0-9_\-\.]+)\s+(?:chrome|browser)?\s*profile\b",
    re.IGNORECASE,
)


def extract_profile_from_prompt(prompt: str) -> Optional[str]:
    """Check if the user prompt explicitly requested a specific Chrome profile."""
    if not prompt:
        return None
    m = _PROFILE_PROMPT_RE.search(prompt)
    if m:
        val = m.group(1).strip()
        if val.lower() not in ("a", "the", "this", "that"):
            return val
    return None


def is_system_chrome_user_data_dir(path: Optional[Path | str]) -> bool:
    """True when path points at the host's normal Chrome profile root."""
    if not path:
        return False
    system = get_default_chrome_user_data_dir()
    if not system:
        return False
    try:
        return Path(path).expanduser().resolve() == system.resolve()
    except Exception:
        return False


def agent_profile_slug(profile_query: Optional[str] = None) -> str:
    """Filesystem-safe slug for an agent-managed Chrome profile directory."""
    q = (profile_query or "").strip().lower()
    if not q:
        return "default"
    if q in ("personal", "home", "main", "default", "primary"):
        return "personal"
    if q in ("work", "job", "office", "corp", "enterprise"):
        return "work"
    if q in ("school", "student", "uni", "university", "college", "edu"):
        return "school"
    # email / custom names
    slug = re.sub(r"[^a-z0-9._-]+", "-", q).strip("-")
    return slug or "default"


def get_agent_chrome_user_data_dir(profile_query: Optional[str] = None) -> Path:
    """Separate Chrome data dir the agent can control with CDP (Chrome 136+ safe)."""
    base = Path.home() / ".mcp-vision/chrome-profiles"
    return base / agent_profile_slug(profile_query)


def get_launch_user_data_dir(
    configured: Optional[str],
    profile_query: Optional[str] = None,
) -> tuple[Path, str, Optional[str]]:
    """Pick a CDP-safe user-data-dir and profile metadata for launch.

    Chrome 136+ refuses --remote-debugging-port on the host's default Chrome
  directory, so we keep agent sessions in ~/.mcp-vision/chrome-profiles/*.
    """
    system = get_default_chrome_user_data_dir()
    if configured:
        configured_path = Path(os.path.expanduser(configured))
        if is_system_chrome_user_data_dir(configured_path):
            profile_dir = get_agent_chrome_user_data_dir(profile_query)
            _, display = resolve_profile(profile_query or "", system)
            return profile_dir, display, None
        profile_dir = configured_path
        if profile_query:
            dir_name, display = resolve_profile(profile_query, configured_path)
            return profile_dir, display, dir_name
        return profile_dir, profile_dir.name, None

    profile_dir = get_agent_chrome_user_data_dir(profile_query)
    display = agent_profile_slug(profile_query)
    if system and profile_query:
        _, display = resolve_profile(profile_query, system)
    return profile_dir, display, None


def demo():
    base = get_default_chrome_user_data_dir()
    profiles = list_profiles(base)
    print(f"Discovered Chrome user data dir: {base}")
    print(f"Available profiles: {profiles}")

    if profiles:
        d, n = resolve_profile("personal", base)
        assert d in profiles, f"Failed to resolve personal: {d}"

        # test prompt extraction
        ext = extract_profile_from_prompt("Check my Gmail using my personal Chrome profile please")
        assert ext == "personal", ext
        ext_work = extract_profile_from_prompt("Look at my calendar in work profile")
        assert ext_work == "work", ext_work

    print("ok")


if __name__ == "__main__":
    demo()
