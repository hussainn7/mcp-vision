import asyncio

from mcp_vision.browser import BrowserSnapshot
from mcp_vision.perception import (PerceptionPipeline, TileChangeCache, VisualFallbackBackend,
                                   fuse_records, visual_fallback_decision)
from mcp_vision.state import compile_state


def record(index, role, name, x, y, *, identity=None, confidence=1.0):
    return {"index": index, "role": role, "name": name, "value": "", "x": x, "y": y,
            "w": 100, "h": 30, "identity": identity or {}, "confidence": confidence}


def test_fusion_keeps_multiple_identities_and_deduplicates_by_geometry():
    dom = record(0, "button", "Continue", 10, 10, identity={"dom": "button#next"})
    ax = record(0, "button", "Continue", 11, 10, identity={"accessibility": "root/3"})
    ocr = record(0, "", "Continue", 10, 11, identity={"visual": "tile:2:1"}, confidence=.72)
    fused = fuse_records([("dom-accessibility", [dom]), ("macos-accessibility", [ax]), ("ocr", [ocr])])

    assert len(fused) == 1
    assert fused[0]["identity"] == {"dom": "button#next", "accessibility": "root/3", "visual": "tile:2:1"}
    assert fused[0]["sources"] == ["dom-accessibility", "macos-accessibility", "ocr"]
    state = compile_state(BrowserSnapshot(snapshot_id="s1", url="https://fixture.test", title="Fixture",
                                          text="Continue", elements=fused, source="unified"), epoch=1)
    assert state.elements[0].sources == ("dom-accessibility", "macos-accessibility", "ocr")
    assert state.elements[0].identity.visual == "tile:2:1"


def test_semantics_skip_visual_provider_but_canvas_uses_it_and_reuses_cache():
    async def run():
        calls = []

        async def provider(_image, changed):
            calls.append(changed)
            return [record(0, "button", "Play", 20, 20, identity={"visual": "play"}, confidence=.8)]

        pipeline = PerceptionPipeline(provider)
        semantic = BrowserSnapshot(snapshot_id="s1", url="", title="", text="",
                                   elements=[record(0, "button", "Continue", 10, 10)])
        assert not visual_fallback_decision(semantic.elements).needed
        assert await pipeline.enrich(semantic, b"image") is semantic and calls == []

        canvas = semantic.model_copy(update={"snapshot_id": "s2", "elements": [
            record(0, "canvas", "Game", 0, 0),
        ]})
        enriched = await pipeline.enrich(canvas, b"image", changed_tiles=((0, 0),))
        reused = await pipeline.enrich(canvas, b"same", changed_tiles=())
        assert len(calls) == 1 and pipeline.calls == 1
        assert enriched.source == reused.source == "unified"
        assert any(item["identity"].get("visual") == "play" for item in enriched.elements)

    asyncio.run(run())


def test_tile_cache_reports_exact_reuse_ratio():
    cache = TileChangeCache(tile_size=16)
    first = bytes([0]) * (32 * 16)
    assert cache.update(first, width=32, height=16, channels=1).unchanged_ratio == 0
    second = bytearray(first)
    second[16] = 1  # first pixel of the second tile
    result = cache.update(bytes(second), width=32, height=16, channels=1)
    assert result.total_tiles == 2 and result.changed_tiles == ((1, 0),)
    assert result.unchanged_ratio == .5


def test_visual_only_candidate_uses_fresh_pixels_and_never_replays_stale_geometry():
    class Backend:
        def __init__(self):
            self.images = [b"same", b"same"]

        async def snapshot(self):
            return BrowserSnapshot(snapshot_id="s1", url="", title="Canvas", text="",
                                   elements=[record(0, "canvas", "Board", 0, 0)])

        async def screenshot(self):
            return self.images.pop(0)

    class Actuator:
        def __init__(self):
            self.clicks = []

        def click(self, x, y):
            self.clicks.append((x, y))

    async def run(images):
        async def provider(_image, _changed):
            return [record(0, "button", "Play", 20, 30, identity={"visual": "play"}, confidence=.9)]

        backend, actuator = Backend(), Actuator()
        backend.images = images
        visual = VisualFallbackBackend(backend, PerceptionPipeline(provider),
                                       allow_visual_writes=True, actuator=actuator)
        snap = await visual.snapshot()
        result = await visual.click(snap.snapshot_id, 1)
        return result, actuator

    receipt, actuator = asyncio.run(run([b"same", b"same"]))
    assert receipt.status == "unverified" and receipt.evidence["execution_path"] == "visual_pointer"
    assert actuator.clicks == [(70, 45)]

    stale, actuator = asyncio.run(run([b"before", b"after"]))
    assert stale.status == "stale" and not actuator.clicks
