"""The benchmark itself is executable evidence, not a table of invented numbers."""
import asyncio
import os

import pytest

from mcp_vision.benchmarks import TASKS, run_browser_benchmark


pytestmark = pytest.mark.skipif(os.environ.get("MCP_VISION_BROWSER_TESTS") != "1",
                                reason="enable explicitly on a browser test executor")


def test_stepwise_and_fastpath_complete_the_same_dynamic_tasks():
    report = asyncio.run(run_browser_benchmark(iterations=1))

    assert report["tasks"] == [task.name for task in TASKS]
    assert report["strategies"]["foundation_stepwise"]["successes"] == len(TASKS)
    assert report["strategies"]["rules_fastpath"]["successes"] == len(TASKS)
    assert report["strategies"]["foundation_stepwise"]["planner_handoffs"] == len(TASKS) * 2
    assert report["strategies"]["rules_fastpath"]["planner_handoffs"] == len(TASKS)
    assert not any(run["wrong_target_actions"] for run in report["runs"])
