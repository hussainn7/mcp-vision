"""Live-session Chrome attach (Chrome 144+).

Chrome 136+ ignores --remote-debugging-port on the default profile directory.
The supported path is: open the user's real Chrome (no debug flags), enable
chrome://inspect/#remote-debugging, then connect to the WebSocket in
DevToolsActivePort — same handshake as chrome-devtools-mcp --autoConnect.

Playwright connect_over_cdp(ws://...) is the Python equivalent of Puppeteer's
browserWSEndpoint connect. Isolated user-data-dir launch is fallback only.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from phase2_mcp.chrome_profiles import get_default_chrome_user_data_dir

INSPECT_URL = "chrome://inspect/#remote-debugging"
CONNECT_WAIT_S = 90.0


def open_chrome_native() -> bool:
    """Bring up the user's installed Chrome. Does not pass debug flags."""
    if sys.platform == "darwin":
        r = subprocess.run(["open", "-a", "Google Chrome"], capture_output=True)
        return r.returncode == 0
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "start", "", "chrome"], capture_output=True)
        return r.returncode == 0
    for cmd in (["google-chrome"], ["google-chrome-stable"], ["chromium"], ["chromium-browser"]):
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0:
            return True
    return False


def open_remote_debugging_page() -> bool:
    """Open the inspect UI in the already-running Chrome (new tab, same profile)."""
    if sys.platform == "darwin":
        r = subprocess.run(
            ["open", "-a", "Google Chrome", INSPECT_URL],
            capture_output=True,
        )
        return r.returncode == 0
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "start", "", "chrome", INSPECT_URL], capture_output=True)
        return r.returncode == 0
    for bin_name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        r = subprocess.run([bin_name, INSPECT_URL], capture_output=True)
        if r.returncode == 0:
            return True
    return False


def parse_devtools_active_port(user_data_dir: Optional[Path | str] = None) -> Optional[tuple[int, str]]:
    """Read DevToolsActivePort → (port, websocket path) or None."""
    base = Path(user_data_dir) if user_data_dir else get_default_chrome_user_data_dir()
    if not base:
        return None
    port_file = Path(base) / "DevToolsActivePort"
    if not port_file.exists():
        return None
    try:
        lines = [ln.strip() for ln in port_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        port = int(lines[0])
    except ValueError:
        return None
    if port <= 0 or port > 65535:
        return None
    path = lines[1] if len(lines) > 1 else "/devtools/browser"
    if not path.startswith("/"):
        path = "/" + path
    return port, path


def websocket_endpoint(user_data_dir: Optional[Path | str] = None) -> Optional[str]:
    parsed = parse_devtools_active_port(user_data_dir)
    if not parsed:
        return None
    port, path = parsed
    return f"ws://127.0.0.1:{port}{path}"


def permission_banner() -> str:
    return (
        "\n"
        + "=" * 72 + "\n"
        "[CHROME] Connect to your real Chrome (cookies, tabs, logins).\n"
        "  1. Chrome opened chrome://inspect/#remote-debugging\n"
        "  2. Turn on Remote debugging if it is off\n"
        "  3. Click Allow when Chrome asks to start a debugging session\n"
        f"Waiting up to {int(CONNECT_WAIT_S)}s for DevToolsActivePort…\n"
        + "=" * 72 + "\n"
    )


def wait_for_websocket(
    user_data_dir: Optional[Path | str] = None,
    timeout_s: float = CONNECT_WAIT_S,
    poll_s: float = 0.5,
) -> Optional[str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ws = websocket_endpoint(user_data_dir)
        if ws:
            return ws
        time.sleep(poll_s)
    return None


def request_live_session(user_data_dir: Optional[Path | str] = None) -> Optional[str]:
    """Open real Chrome + inspect page, wait until the user allows debugging."""
    existing = websocket_endpoint(user_data_dir)
    if existing:
        return existing
    open_chrome_native()
    open_remote_debugging_page()
    print(permission_banner(), file=sys.stderr)
    return wait_for_websocket(user_data_dir)


def demo():
    sample = "9222\n/devtools/browser/abc-123\n"
    p = Path("/tmp/mcp-vision-devtools-port-test")
    p.mkdir(exist_ok=True)
    (p / "DevToolsActivePort").write_text(sample)
    parsed = parse_devtools_active_port(p)
    assert parsed == (9222, "/devtools/browser/abc-123"), parsed
    assert websocket_endpoint(p) == "ws://127.0.0.1:9222/devtools/browser/abc-123"
    assert parse_devtools_active_port("/tmp/does-not-exist-chrome") is None
    assert "Remote debugging" in permission_banner()
    print("ok")


if __name__ == "__main__":
    demo()
