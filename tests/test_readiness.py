import io
import json

import pytest

from mcp_vision.readiness import ensure_model_ready


def test_configured_local_model_is_checked_without_cloud_switch(monkeypatch):
    monkeypatch.setattr('config.cfg.planning_model', 'test:1')
    monkeypatch.setattr('mcp_vision.readiness.urlopen', lambda *a, **k: io.StringIO(json.dumps({'models': [{'name': 'test:1'}]})))
    ensure_model_ready('local')


def test_missing_model_is_actionable(monkeypatch):
    monkeypatch.setattr('mcp_vision.readiness.urlopen', lambda *a, **k: io.StringIO('{"models": []}'))
    with pytest.raises(RuntimeError, match='not installed'):
        ensure_model_ready('local')


def test_remote_outage_does_not_launch_local_service(monkeypatch):
    monkeypatch.setattr('config.cfg.ollama_host', 'https://model.example')
    def fail(*a, **k):
        raise OSError('unavailable')
    monkeypatch.setattr('mcp_vision.readiness.urlopen', fail)
    monkeypatch.setattr('mcp_vision.readiness.subprocess.run', lambda *a, **k: pytest.fail('must not start a local service for a remote endpoint'))
    with pytest.raises(RuntimeError, match='unavailable'):
        ensure_model_ready('local')
