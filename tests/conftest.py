from __future__ import annotations

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
