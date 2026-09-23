from __future__ import annotations

import asyncio

from mcp_vision.browser import BrowserSnapshot, Receipt
from mcp_vision.fast_policy import MockPolicy
from mcp_vision.fastpath import FastPath, FastPathConfig, FastPathStatus, FastPathTask
from mcp_vision.transactions import TransactionRuntime
from mcp_vision.verification import VerificationOutcome, VerificationPredicate


def snap(state_id: str, *, text: str = "Waiting", submits: bool = False):
    return BrowserSnapshot(
        snapshot_id=state_id, root_id="root", url="https://fixture.test/", title="Fixture", text=text,
        elements=[{"index": 0, "role": "button", "name": "Continue", "value": "",
                   "x": 1, "y": 1, "w": 80, "h": 20, "input_type": "submit" if submits else "button",
                   "submits": submits, "identity": {"dom": "button:continue"}}],
    )


class Backend:
    def __init__(self, snapshots, receipts):
        self.snapshots = list(snapshots)
        self.receipts = list(receipts)
        self.calls = 0

    async def snapshot(self):
        return self.snapshots.pop(0)

    async def click(self, *_):
        self.calls += 1
        return self.receipts.pop(0)

    async def fill(self, *_):
        raise AssertionError("unexpected")

    async def select(self, *_):
        raise AssertionError("unexpected")

    async def set_checked(self, *_):
        raise AssertionError("unexpected")

    async def scroll(self, *_):
        raise AssertionError("unexpected")


def task():
    return FastPathTask(subgoal="Press Continue",
                        completion=VerificationPredicate(kind="text_contains", expected="Done"))


def test_fastpath_reobserves_after_stale_then_verifies():
    async def run():
        backend = Backend(
            [snap("s1"), snap("s2"), snap("s3", text="Done")],
            [Receipt(status="stale", action="click", message="rerendered", executed=False),
             Receipt(status="unverified", action="click", message="pressed", executed=True)],
        )
        result = await FastPath(TransactionRuntime(backend), MockPolicy("A1")).run(task())
        assert result.status is FastPathStatus.VERIFIED and result.subgoal_complete
        assert result.metrics.stale_rejections == 1 and result.metrics.retries == 1
        assert result.metrics.observations == 3 and backend.calls == 2
    asyncio.run(run())


def test_fastpath_detects_noop_loop():
    async def run():
        backend = Backend(
            [snap("s1"), snap("s2"), snap("s3")],
            [Receipt(status="unverified", action="click", message="pressed", executed=True),
             Receipt(status="unverified", action="click", message="pressed", executed=True)],
        )
        result = await FastPath(
            TransactionRuntime(backend), MockPolicy("A1"),
            config=FastPathConfig(max_steps=5, max_noops=2),
        ).run(task())
        assert result.status is FastPathStatus.REPLAN
        assert result.reason == "No-op limit reached." and result.metrics.noops == 2
    asyncio.run(run())


def test_fastpath_escalates_low_confidence_and_cannot_select_restricted_action():
    async def run():
        low = Backend([snap("s1")], [])
        uncertain = await FastPath(TransactionRuntime(low), MockPolicy("A1", confidence=0.2)).run(task())
        assert uncertain.status is FastPathStatus.UNCERTAIN and low.calls == 0

        restricted = Backend([snap("s1", submits=True)], [])
        escalated = await FastPath(TransactionRuntime(restricted), MockPolicy("A1")).run(task())
        assert escalated.status is FastPathStatus.REPLAN and restricted.calls == 0
    asyncio.run(run())


def test_fastpath_does_not_act_when_degraded_perception_makes_completion_unknown():
    async def run():
        degraded = snap("s1")
        degraded.identity = {"fallback_reasons": ["ax_tree_empty", "ocr_unavailable"]}
        backend = Backend([degraded], [])
        result = await FastPath(TransactionRuntime(backend), MockPolicy("A1")).run(
            FastPathTask(
                subgoal="Dismiss the missing dialog",
                completion=VerificationPredicate(kind="element_missing", role="dialog", name="Warning"),
            )
        )
        assert result.status is FastPathStatus.UNCERTAIN
        assert result.verification.outcome is VerificationOutcome.UNKNOWN
        assert result.metrics.actions == 0 and backend.calls == 0
        assert "perception is degraded" in result.reason

    asyncio.run(run())
