from __future__ import annotations

import os
import tempfile

collect_ignore = ["run_workflow_test.py"]

# Tests never touch the real Plip: its history, prefs, memory and logs live here instead.
_SANDBOX = tempfile.mkdtemp(prefix="plip-tests-")
os.environ["MCP_VISION_STATE_DIR"] = os.path.join(_SANDBOX, "state")
os.environ["MCP_VISION_CONFIG_DIR"] = os.path.join(_SANDBOX, "config")
os.environ["MCP_VISION_NO_ANALYTICS"] = "1"

import pytest

from mcp_vision.core.actuate import RecordingActuator, set_actuator
from mcp_vision.overlay.hud import set_forced_result
from mcp_vision.server import reset_session


@pytest.fixture(autouse=True)
def _isolate() -> None:
    reset_session()
    set_forced_result(False)
    set_actuator(RecordingActuator())
    yield
    reset_session()
    set_forced_result(None)
