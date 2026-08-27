from __future__ import annotations

from PIL import Image

from mcp_vision.core.capture import capture_display, encode, list_displays


def test_encode_downscales_and_compresses() -> None:
    img = Image.new("RGB", (2000, 1000), (10, 20, 30))
    blob = encode(img, max_width=1280, quality=60)
    assert 500 < len(blob) < 80_000
    small = capture_display(0, max_width=1280, grabber=lambda _i: img)
    assert small.width == 1280
    assert small.height == 640
    assert abs(small.scale - (2000 / 1280)) < 1e-6


def test_encode_keeps_small_frames() -> None:
    img = Image.new("RGB", (320, 200), (0, 0, 0))
    frame = capture_display(3, max_width=1280, grabber=lambda i: img)
    assert frame.display_id == 3
    assert frame.width == 320
    assert frame.png


def test_list_displays_never_crashes() -> None:
    mons = list_displays()
    assert isinstance(mons, list)
    assert mons
