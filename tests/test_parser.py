from __future__ import annotations

from PIL import Image, ImageDraw

from mcp_vision.core.parser import detect_boxes, inspect_image


def _ui() -> Image.Image:
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 90, 55], fill=(30, 30, 30))
    d.rectangle([120, 40, 210, 95], fill=(40, 40, 40))
    return img


def test_detects_numbered_boxes() -> None:
    boxes = detect_boxes(_ui(), min_size=8)
    assert len(boxes) >= 2
    assert boxes[0]["x"] < boxes[1]["x"] or boxes[0]["y"] <= boxes[1]["y"]


def test_inspect_indexes_labels() -> None:
    r = inspect_image(_ui(), display_id=1, scale=2.0, ocr=False)
    assert r.display_id == 1 and r.scale == 2.0
    assert r.element(0) is not None
    ids = [e.id for e in r.elements]
    assert ids == list(range(len(ids)))
    dump = r.model_dump()
    assert "png" not in dump
    assert dump["elements"][0]["bbox"]["w"] > 0
