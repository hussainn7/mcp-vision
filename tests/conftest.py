from __future__ import annotations

import os
import tempfile

# Tests never touch the real Plip: its history, prefs, memory and logs live here instead.
_SANDBOX = tempfile.mkdtemp(prefix="plip-tests-")
os.environ["MCP_VISION_STATE_DIR"] = os.path.join(_SANDBOX, "state")
os.environ["MCP_VISION_CONFIG_DIR"] = os.path.join(_SANDBOX, "config")
os.environ["MCP_VISION_NO_ANALYTICS"] = "1"
