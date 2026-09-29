"""Tests for FrameCache deduplication and encode improvements."""
from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from mcp_vision.core.capture import (
    Frame,
    FrameCache,
    _encode_with_image,
    capture_display,
    encode,
    encode_async,
)


def _solid(color: tuple[int, int, int] = (100, 150, 200), size: tuple[int, int] = (800, 600)) -> Image.Image:
    img = Image.new("RGB", size, color)
    return img


def _fake_grabber(img: Image.Image):
    def grab(display_id: int) -> Image.Image:
        return img
    return grab


# ---------------------------------------------------------------------------
# FrameCache unit tests
# ---------------------------------------------------------------------------

def test_cache_miss_on_first_get():
    fc = FrameCache()
    img = _solid()
    ph = FrameCache.hash(img)
    assert fc.get(0, ph) is None


def test_cache_hit_after_put():
    fc = FrameCache()
    img = _solid()
    ph = FrameCache.hash(img)
    frame = Frame(image=img, png=b"x", display_id=0,
                  width=img.width, height=img.height, scale=1.0, monitor={})
    fc.put(0, ph, frame)
    assert fc.get(0, ph) is frame


def test_cache_miss_on_different_hash():
    fc = FrameCache()
    img1 = _solid((100, 100, 100))
    img2 = _solid((200, 200, 200))
    ph1 = FrameCache.hash(img1)
    ph2 = FrameCache.hash(img2)
    frame = Frame(image=img1, png=b"x", display_id=0,
                  width=img1.width, height=img1.height, scale=1.0, monitor={})
    fc.put(0, ph1, frame)
    assert fc.get(0, ph2) is None


def test_cache_separate_display_ids():
    fc = FrameCache()
    img = _solid()
    ph = FrameCache.hash(img)
    frame = Frame(image=img, png=b"x", display_id=0,
                  width=img.width, height=img.height, scale=1.0, monitor={})
    fc.put(0, ph, frame)
    assert fc.get(1, ph) is None


def test_invalidate_single_display():
    fc = FrameCache()
    img = _solid()
    ph = FrameCache.hash(img)
    frame = Frame(image=img, png=b"x", display_id=0,
                  width=img.width, height=img.height, scale=1.0, monitor={})
    fc.put(0, ph, frame)
    fc.invalidate(0)
    assert fc.get(0, ph) is None


def test_invalidate_all():
    fc = FrameCache()
    img = _solid()
    ph = FrameCache.hash(img)
    frame = Frame(image=img, png=b"x", display_id=0,
                  width=img.width, height=img.height, scale=1.0, monitor={})
    fc.put(0, ph, frame)
    fc.put(1, ph, frame)
    fc.invalidate()
    assert fc.get(0, ph) is None
    assert fc.get(1, ph) is None


# ---------------------------------------------------------------------------
# encode / _encode_with_image tests
# ---------------------------------------------------------------------------

def test_encode_returns_jpeg_bytes():
    img = _solid()
    data = encode(img)
    assert data[:3] == b"\xff\xd8\xff"


def test_encode_with_image_returns_same_bytes():
    img = _solid()
    data1 = encode(img)
    data2, small = _encode_with_image(img)
    assert data1 == data2
    assert small.width <= 1280


def test_encode_downscales_wide_image():
    img = _solid(size=(3000, 2000))
    _, small = _encode_with_image(img, max_width=1280)
    assert small.width == 1280
    assert small.height == pytest.approx(853, abs=1)


def test_encode_does_not_upscale_small_image():
    img = _solid(size=(640, 480))
    _, small = _encode_with_image(img, max_width=1280)
    assert small.width == 640


def test_encode_png_format():
    img = _solid()
    data = encode(img, fmt="PNG")
    assert data[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# encode_async
# ---------------------------------------------------------------------------

def test_encode_async_produces_same_bytes():
    img = _solid()
    sync_bytes = encode(img)
    async_bytes = asyncio.run(encode_async(img))
    assert sync_bytes == async_bytes


# ---------------------------------------------------------------------------
# capture_display with FrameCache integration
# ---------------------------------------------------------------------------

def test_capture_display_uses_cache_on_unchanged_screen():
    fc = FrameCache()
    img = _solid()
    calls = {"n": 0}

    def grabber(display_id: int) -> Image.Image:
        calls["n"] += 1
        return img

    f1 = capture_display(display_id=0, grabber=grabber, cache=fc)
    f2 = capture_display(display_id=0, grabber=grabber, cache=fc)

    assert f1 is f2
    assert calls["n"] == 2


def test_capture_display_re_encodes_on_changed_screen():
    fc = FrameCache()
    images = [_solid((100, 100, 100)), _solid((200, 200, 200))]
    idx = [0]

    def grabber(display_id: int) -> Image.Image:
        frame = images[idx[0]]
        idx[0] = min(idx[0] + 1, len(images) - 1)
        return frame

    f1 = capture_display(display_id=0, grabber=grabber, cache=fc)
    f2 = capture_display(display_id=0, grabber=grabber, cache=fc)
    assert f1 is not f2


def test_capture_display_scale_correct():
    fc = FrameCache()
    img = _solid(size=(2560, 1600))
    f = capture_display(display_id=0, grabber=_fake_grabber(img), cache=fc)
    assert f.scale == pytest.approx(2560 / f.width, rel=0.01)
    assert f.width == 1280
