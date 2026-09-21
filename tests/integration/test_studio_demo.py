"""End-to-end proof that Studio renders data from the real FastPath loop."""
import asyncio
import os

import pytest

from mcp_vision.demo import studio_demo


pytestmark = pytest.mark.skipif(os.environ.get("MCP_VISION_BROWSER_TESTS") != "1",
                                reason="enable explicitly on a browser test executor")


def test_studio_demo_runs_fastpath_and_preserves_guardrails():
    result = asyncio.run(studio_demo("Ideas worth building"))

    assert result["demo_passed"]
    assert result["session"]["status"] == "verified"
    assert [step["operation"] for step in result["session"]["steps"]] == ["type", "press"]
    assert all(step["execution_path"] == "dom" for step in result["session"]["steps"])
    assert result["session"]["metrics"]["background_actions"] == 2
    assert all(item["passed"] for item in result["session"]["guardrails"])
    assert result["checks"] == {"draft_retained": True, "form_unsubmitted": True}
    assert [receipt["status"] for receipt in result["receipts"]][-2:] == ["stale", "blocked"]
