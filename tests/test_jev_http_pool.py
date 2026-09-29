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


# ---------------------------------------------------------------------------
# jev_status tests
# ---------------------------------------------------------------------------

def test_jev_status_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    status = jev_status()
    assert status["configured"] is False
    assert status["key_source"] == "unset"


def test_jev_status_configured_with_env_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-123")
    status = jev_status()
    assert status["configured"] is True
    assert status["key_source"] == "env"
    assert "****" in status["key_preview"]
