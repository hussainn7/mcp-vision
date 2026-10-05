"""Tests for JevPolicy HTTP pool and jev_status helper."""
from __future__ import annotations

import http.client
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from mcp_vision.fast_policy import _JevHTTPPool, jev_status


# ---------------------------------------------------------------------------
# _JevHTTPPool unit tests
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal mock of http.client.HTTPSConnection."""

    def __init__(self, status: int = 200, body: bytes = b'{"ok": true}') -> None:
        self.status = status
        self.body = body
        self.requests: list[dict] = []

    def request(self, method, path, body, headers):
        self.requests.append({"method": method, "path": path, "body": body, "headers": headers})

    def getresponse(self):
        resp = MagicMock()
        resp.status = self.status
        resp.read.return_value = self.body
        return resp


def _pool_with_conn(conn: _FakeConn) -> _JevHTTPPool:
    pool = _JevHTTPPool()
    pool._conns["api.typesafe.ai"] = conn
    return pool


def test_pool_returns_parsed_json():
    conn = _FakeConn(200, b'{"choice": "click"}')
    pool = _pool_with_conn(conn)
    data = pool.post("https://api.typesafe.ai/v1/decisions", b"{}", auth="Bearer k")
    assert data == b'{"choice": "click"}'


def test_pool_raises_on_4xx():
    conn = _FakeConn(400, b'bad request')
    pool = _pool_with_conn(conn)
    with pytest.raises(http.client.HTTPException, match="HTTP 400"):
        pool.post("https://api.typesafe.ai/v1/decisions", b"{}", auth="Bearer k")


def test_pool_reconnects_on_error():
    """Pool should evict the broken connection and retry with a fresh one."""
    good_conn = _FakeConn(200, b'{"ok": true}')

    call_count = [0]
    original_get = _JevHTTPPool._get

    def patched_get(self, host, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("connection reset")
        return good_conn

    pool = _JevHTTPPool()
    with patch.object(_JevHTTPPool, "_get", patched_get):
        data = pool.post("https://api.typesafe.ai/v1/decisions", b"{}", auth="Bearer k")
    assert data == b'{"ok": true}'
    assert call_count[0] == 2


def test_pool_includes_auth_header():
    conn = _FakeConn(200, b'{}')
    pool = _pool_with_conn(conn)
    pool.post("https://api.typesafe.ai/v1/decisions", b"{}", auth="Bearer mykey")
    assert conn.requests[0]["headers"]["Authorization"] == "Bearer mykey"


def test_pool_keep_alive_header():
    conn = _FakeConn(200, b'{}')
    pool = _pool_with_conn(conn)
    pool.post("https://api.typesafe.ai/v1/decisions", b"{}", auth="Bearer k")
    assert conn.requests[0]["headers"]["Connection"] == "keep-alive"


def test_pool_thread_safe():
    """Multiple threads should each get a result without corrupting state."""
    results = []
    errors = []

    def make_conn():
        return _FakeConn(200, b'{"thread": "ok"}')

    pool = _JevHTTPPool()

    def worker():
        try:
            conn = make_conn()
            with pool._lock:
                pool._conns["api.typesafe.ai"] = conn
            data = pool.post("https://api.typesafe.ai/v1/decisions", b"{}", auth="Bearer k")
            results.append(data)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 8


def test_pool_http_error_carries_status():
    from mcp_vision.fast_policy import JevHTTPError

    pool = _pool_with_conn(_FakeConn(429, b'{"detail": "slow down"}'))
    with pytest.raises(JevHTTPError) as caught:
        pool.post("https://api.typesafe.ai/v1/systemone", b"{}", auth="Bearer k")
    assert caught.value.status == 429


def test_pool_retries_a_dropped_keep_alive_connection():
    """RemoteDisconnected is an HTTPException subclass; it must reconnect, not propagate."""

    class Dropped(_FakeConn):
        def getresponse(self):
            raise http.client.RemoteDisconnected("server closed idle connection")

    fresh = _FakeConn(200, b'{"answers": {}}')
    pool = _pool_with_conn(Dropped())
    with patch.object(_JevHTTPPool, "_get", side_effect=[Dropped(), fresh]):
        assert pool.post("https://api.typesafe.ai/v1/systemone", b"{}", auth="Bearer k") == b'{"answers": {}}'


def test_jev_policy_survives_http_errors_through_the_real_pool(monkeypatch):
    """Regression: an HTTP error used to raise AttributeError (exc.status) out of choose()."""
    import asyncio

    from mcp_vision import fast_policy
    from mcp_vision.state import compile_state
    from tests.test_state_runtime import snapshot

    for failing in (_FakeConn(429, b"rate limited"), _FakeConn(500, b"boom")):
        monkeypatch.setattr(fast_policy, "_jev_pool", _pool_with_conn(failing))
        state = compile_state(snapshot("s1"), epoch=1)
        decision = asyncio.run(fast_policy.JevPolicy(api_key="k", load_env=False).choose("press Continue", state))
        assert decision.candidate_id is None and decision.needs_system2
        assert decision.provider_call == "failed" and decision.reason.endswith(f"HTTP {failing.status}")


def test_jev_settings_honor_typesafe_sdk_variables(monkeypatch):
    from mcp_vision.fast_policy import _jev_settings

    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://proxy.example/")
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "jev-1.13.0")
    settings = _jev_settings(load_env=False)
    assert settings.endpoint == "https://proxy.example/v1/systemone"
    assert settings.typesafe_model == "jev-1.13.0"


# ---------------------------------------------------------------------------
# jev_status tests
# ---------------------------------------------------------------------------

def test_jev_status_not_configured_without_key(monkeypatch):
    from mcp_vision import fast_policy

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    no_file = fast_policy._jev_settings
    monkeypatch.setattr(fast_policy, "_jev_settings", lambda load_env=True: no_file(load_env=False))  # not the repo's .env
    status = jev_status()
    assert status["configured"] is False
    assert status["key_source"] == "unset"


def test_jev_status_configured_with_env_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-123")
    status = jev_status()
    assert status["configured"] is True
    assert status["key_source"] == "env"
    assert "****" in status["key_preview"]
