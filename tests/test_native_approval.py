import subprocess
from types import SimpleNamespace

import pytest

from mcp_vision.overlay import hud


@pytest.mark.parametrize("code,output,allowed", [(0, "allowed\n", True), (0, "denied", False),
    (1, "allowed", False), (0, "", False)])
def test_native_approval_requires_explicit_allow(monkeypatch, code, output, allowed):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=code, stdout=output)
    monkeypatch.setattr(hud.subprocess, "run", run)
    assert hud._mac_confirm('Send "test"? $(nothing)', 20) is allowed
    assert calls[0][0][-2] == 'Send "test"? $(nothing)'
    assert calls[0][1]["timeout"] == 23
    assert 'default button "Deny"' in calls[0][0][2]


def test_timeout_denies(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("osascript", 23)
    monkeypatch.setattr(hud.subprocess, "run", run)
    assert hud._mac_confirm("Send?", 20) is False
