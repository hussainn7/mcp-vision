import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from mcp_vision.browser import BrowserSnapshot
from mcp_vision.native_perception import (
    OCRText,
    WindowCapture,
    _recognized_text,
    capture_window,
    degradation_reasons,
    fuse_ax_ocr,
)
from mcp_vision.state import compile_state


def button(name="", x=10, y=20, width=40, height=40, index=0):
    return {
        "index": index, "name": name or "Button", "role": "button", "ax_role": "AXButton",
        "ax_name": name, "x": x, "y": y, "w": width, "h": height,
    }


class QuartzFixture:
    kCGWindowOwnerPID = "pid"
    kCGWindowLayer = "layer"
    kCGWindowBounds = "bounds"
    kCGWindowNumber = "number"
    kCGWindowName = "name"
    kCGWindowListOptionIncludingWindow = 1
    kCGWindowImageBoundsIgnoreFraming = 2

    def __init__(self, width=200, height=100, image=True):
        self.width = width
        self.height = height
        self.image = object() if image else None

    @staticmethod
    def CGRectMake(x, y, width, height):
        return x, y, width, height

    def CGWindowListCreateImage(self, *_args):
        return self.image

    def CGImageGetWidth(self, _image):
        return self.width

    def CGImageGetHeight(self, _image):
        return self.height

    def CGImageGetBytesPerRow(self, _image):
        return self.width * 4

    @staticmethod
    def CGImageGetDataProvider(image):
        return image

    def CGDataProviderCopyData(self, _provider):
        return bytes(self.width * self.height * 4)


def window(pid, number, bounds, name=""):
    return {"pid": pid, "layer": 0, "number": number, "bounds": bounds, "name": name}


def test_blank_ax_button_gets_overlapping_ocr_name_sources_and_confidence():
    result = fuse_ax_ocr([button()], [OCRText("7", 17, 28, 8, 15, confidence=.93)])
    assert result[0]["name"] == "7"
    assert result[0]["sources"] == ["ax", "ocr"]
    assert result[0]["ocr_name"] == "7"
    assert result[0]["confidence"] == .93


def test_named_ax_control_is_not_replaced_by_conflicting_ocr():
    result = fuse_ax_ocr([button("Save")], [OCRText("Discard", 17, 28, 20, 15)])
    assert result[0]["name"] == "Save"
    assert result[0]["sources"] == ["ax"]
    assert result[0]["ocr_conflicts"] == ["Discard"]
    assert "ocr_conflict" in degradation_reasons(result)


def test_ocr_outside_bounds_does_not_label_ax_control():
    result = fuse_ax_ocr([button()], [OCRText("7", 100, 100, 8, 15)])
    assert result[0]["name"] == "Button"
    assert result[0]["sources"] == ["ax"]


def test_ocr_only_control_requires_unique_actionable_role_and_confidence():
    items = [
        OCRText("Unique", 100, 100, 40, 20, confidence=.9, role="button"),
        OCRText("Duplicate", 200, 100, 40, 20, confidence=.9, role="button"),
        OCRText("Duplicate", 300, 100, 40, 20, confidence=.9, role="button"),
        OCRText("Unclassified", 400, 100, 40, 20, confidence=.99),
        OCRText("Uncertain", 500, 100, 40, 20, confidence=.5, role="button"),
    ]
    result = fuse_ax_ocr([], items, window_id=44)
    assert [record["name"] for record in result] == ["Unique"]
    assert result[0]["sources"] == ["ocr"] and result[0]["confidence"] == .9
    assert result[0]["identity"]["visual"].startswith("window:44:")


def test_degradation_detects_tiny_tree_and_blank_actionable_labels():
    records = [button(), button(x=60, index=1), {"role": "group", "name": "Panel"}]
    reasons = degradation_reasons(records)
    assert "tiny_ax_tree" in reasons
    assert "blank_actionable_labels" in reasons
    assert "ax_traversal_error" in degradation_reasons([], traversal_error=True)


def test_capture_rejects_wrong_same_pid_window(monkeypatch):
    quartz = QuartzFixture()
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setattr("mcp_vision.native_perception._window_info", lambda _pid: [
        window(42, 8, {"X": 300, "Y": 200, "Width": 100, "Height": 50}),
    ])
    with pytest.raises(RuntimeError, match="matches=0"):
        capture_window(42, {"X": 10, "Y": 20, "Width": 100, "Height": 50})


def test_capture_proves_window_id_bounds_and_retina_scale(monkeypatch):
    quartz = QuartzFixture(width=200, height=100)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    bounds = {"X": 10, "Y": 20, "Width": 100, "Height": 50}
    monkeypatch.setattr("mcp_vision.native_perception._window_info", lambda _pid: [
        window(42, 8, bounds, "Fixture"),
        window(42, 9, {"X": 300, "Y": 200, "Width": 100, "Height": 50}, "Fixture"),
        window(7, 10, bounds, "Fixture"),
    ])
    capture = capture_window(42, bounds, "Fixture")
    assert capture.window_id == 8 and capture.pid == 42 and capture.bounds == bounds
    assert capture.image.size == (200, 100) and capture.scale == 2


def test_capture_failure_is_explicit(monkeypatch):
    quartz = QuartzFixture(image=False)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    bounds = {"X": 10, "Y": 20, "Width": 100, "Height": 50}
    monkeypatch.setattr("mcp_vision.native_perception._window_info", lambda _pid: [window(42, 8, bounds)])
    with pytest.raises(RuntimeError, match="no pixels"):
        capture_window(42, bounds)


def test_synthetic_ocr_coordinates_are_deterministic_at_retina_scale():
    candidate = SimpleNamespace(string=lambda: "7", confidence=lambda: .87)
    box = SimpleNamespace(origin=SimpleNamespace(x=.2, y=.6), size=SimpleNamespace(width=.1, height=.2))
    observation = SimpleNamespace(topCandidates_=lambda _count: [candidate], boundingBox=lambda: box)
    capture = WindowCapture(Image.new("RGB", (1000, 600)), b"", {
        "X": 100, "Y": 200, "Width": 500, "Height": 300,
    }, 8, pid=42, scale=2)
    result = _recognized_text(capture, [observation])
    assert result == [OCRText("7", 200, 260, 50, 60, confidence=.87)]


def test_state_quality_retains_only_unresolved_native_reasons_and_sources():
    records = fuse_ax_ocr([button()], [OCRText("7", 17, 28, 8, 15, confidence=.9)])
    snapshot = BrowserSnapshot(
        snapshot_id="observation", root_id="native-42", url="", title="Fixture", text="7",
        elements=records, source="macos-accessibility",
        identity={"fallback_reasons": ["tiny_ax_tree", "window_capture_unavailable"]},
    )
    state = compile_state(snapshot, epoch=1)
    assert state.quality.degraded
    assert state.quality.reasons == ("tiny_ax_tree", "window_capture_unavailable")
    assert state.quality.sources == ("ax", "ocr")
