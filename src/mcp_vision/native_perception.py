"""Window-scoped native capture, local OCR, and AX/OCR fusion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from PIL import Image


ACTIONABLE_ROLES = {
    "button", "link", "checkbox", "radio", "combobox", "textbox", "slider", "spinbutton", "menuitem",
}


@dataclass(frozen=True)
class OCRText:
    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0
    role: str = ""


@dataclass(frozen=True)
class WindowCapture:
    image: Image.Image
    png: bytes
    bounds: dict[str, float]
    window_id: int
    pid: int = 0
    scale: float = 1.0


def _window_info(_pid: int) -> list[dict[str, Any]]:
    import Quartz

    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return list(Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or [])


def _bounds_overlap(first: dict[str, float], second: dict[str, float]) -> float:
    left = max(first.get("X", 0), second.get("X", 0))
    top = max(first.get("Y", 0), second.get("Y", 0))
    right = min(first.get("X", 0) + first.get("Width", 0), second.get("X", 0) + second.get("Width", 0))
    bottom = min(first.get("Y", 0) + first.get("Height", 0), second.get("Y", 0) + second.get("Height", 0))
    return max(0.0, right - left) * max(0.0, bottom - top)


def same_window_bounds(actual: dict[str, float], expected: dict[str, float]) -> bool:
    width = max(float(actual.get("Width", 0)), float(expected.get("Width", 0)), 1.0)
    height = max(float(actual.get("Height", 0)), float(expected.get("Height", 0)), 1.0)
    tolerance = max(3.0, min(width, height) * 0.01)
    return all(abs(float(actual.get(key, 0)) - float(expected.get(key, 0))) <= tolerance
               for key in ("X", "Y", "Width", "Height"))


def _float_bounds(raw: dict[str, Any]) -> dict[str, float]:
    bounds = {key: float(raw[key]) for key in ("X", "Y", "Width", "Height")}
    if bounds["Width"] <= 0 or bounds["Height"] <= 0:
        raise RuntimeError("WindowServer reported empty target-window bounds.")
    return bounds


def capture_window(pid: int, bounds: dict[str, float] | None = None, title: str = "",
                   window_id: int | None = None) -> WindowCapture:
    """Capture pixels only after WindowServer proves the PID, ID, and AX bounds."""
    import Quartz

    if not bounds and not window_id:
        raise RuntimeError("Exact-window capture requires AX bounds or a proven window ID.")
    candidates = [info for info in _window_info(pid)
                  if int(info.get(Quartz.kCGWindowOwnerPID, -1)) == int(pid)
                  and int(info.get(Quartz.kCGWindowLayer, 0)) == 0
                  and info.get(Quartz.kCGWindowBounds)
                  and int(info.get(Quartz.kCGWindowNumber, 0)) > 0]
    if window_id:
        candidates = [info for info in candidates
                      if int(info.get(Quartz.kCGWindowNumber, 0)) == int(window_id)]
    if bounds:
        candidates = [info for info in candidates
                      if same_window_bounds(_float_bounds(info[Quartz.kCGWindowBounds]), bounds)]
    if len(candidates) > 1 and title:
        titled = [info for info in candidates if str(info.get(Quartz.kCGWindowName, "")) == title]
        if titled:
            candidates = titled
    if len(candidates) != 1:
        raise RuntimeError(
            f"WindowServer could not uniquely prove target window for pid {pid} "
            f"(matches={len(candidates)})."
        )

    selected = candidates[0]
    selected_id = int(selected[Quartz.kCGWindowNumber])
    window_bounds = _float_bounds(selected[Quartz.kCGWindowBounds])
    rect = Quartz.CGRectMake(window_bounds["X"], window_bounds["Y"],
                             window_bounds["Width"], window_bounds["Height"])
    image_ref = Quartz.CGWindowListCreateImage(
        rect, Quartz.kCGWindowListOptionIncludingWindow, selected_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image_ref is None:
        raise RuntimeError("macOS returned no pixels for the proven target window.")
    width = int(Quartz.CGImageGetWidth(image_ref))
    height = int(Quartz.CGImageGetHeight(image_ref))
    if width <= 0 or height <= 0:
        raise RuntimeError("macOS returned empty pixels for the proven target window.")
    scale_x = width / window_bounds["Width"]
    scale_y = height / window_bounds["Height"]
    if not (0.5 <= scale_x <= 4.0 and abs(scale_x - scale_y) <= 0.02):
        raise RuntimeError(
            f"Target-window capture scale is inconsistent ({scale_x:.3f}x{scale_y:.3f})."
        )
    row_bytes = Quartz.CGImageGetBytesPerRow(image_ref)
    raw = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image_ref)))
    image = Image.frombuffer("RGBA", (width, height), raw, "raw", "BGRA", row_bytes, 1).convert("RGB")
    return WindowCapture(image=image, png=_png(image), bounds=window_bounds, window_id=selected_id,
                         pid=int(pid), scale=(scale_x + scale_y) / 2)


def _png(image: Image.Image) -> bytes:
    import io

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _recognized_text(capture: WindowCapture, observations: Iterable[Any]) -> list[OCRText]:
    results = []
    for observation in observations:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        text = str(candidates[0].string()).strip()
        if not text:
            continue
        box = observation.boundingBox()
        confidence = float(candidates[0].confidence()) if hasattr(candidates[0], "confidence") else 1.0
        results.append(OCRText(
            text=text,
            x=capture.bounds["X"] + box.origin.x * capture.bounds["Width"],
            y=capture.bounds["Y"] + (1 - box.origin.y - box.size.height) * capture.bounds["Height"],
            width=box.size.width * capture.bounds["Width"],
            height=box.size.height * capture.bounds["Height"],
            confidence=confidence,
        ))
    return results


def recognize_text(capture: WindowCapture) -> list[OCRText]:
    """Recognize visible text with Apple's local Vision framework."""
    try:
        import Vision
    except ImportError:
        raise RuntimeError("Apple Vision OCR is unavailable; install pyobjc-framework-Vision.")

    image_ref = _cg_image(capture.image)
    for _attempt in range(2):
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image_ref, {})
        success, error = handler.performRequests_error_([request], None)
        if success is False or error:
            raise RuntimeError("Apple Vision OCR failed for the target-window capture.")
        results = _recognized_text(capture, request.results() or [])
        if results:
            return results
    return []


def _cg_image(image: Image.Image):
    import Quartz

    rgba = image.convert("RGBA")
    data = rgba.tobytes()
    provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    return Quartz.CGImageCreate(rgba.width, rgba.height, 8, 32, rgba.width * 4, color_space,
                                Quartz.kCGImageAlphaPremultipliedLast, provider, None, False,
                                Quartz.kCGRenderingIntentDefault)


def degradation_reasons(records: list[dict], *, traversal_error: bool = False) -> list[str]:
    actionable = [record for record in records if record.get("role") in ACTIONABLE_ROLES]
    blank = [record for record in actionable if record.get("role") != "textbox"
             and not str(record.get("ax_name") or record.get("ocr_name") or "").strip()]
    reasons = []
    if traversal_error:
        reasons.append("ax_traversal_error")
    if len(records) < 8:
        reasons.append("tiny_ax_tree")
    if len(blank) >= 2 and actionable and len(blank) / len(actionable) >= 0.1:
        reasons.append("blank_actionable_labels")
    if any(record.get("ocr_conflicts") for record in records):
        reasons.append("ocr_conflict")
    return reasons


def _overlapping(record: dict, item: OCRText, threshold: float) -> bool:
    ax_box = {"X": float(record.get("x", 0)), "Y": float(record.get("y", 0)),
              "Width": float(record.get("w", 0)), "Height": float(record.get("h", 0))}
    overlap = _bounds_overlap(ax_box, {
        "X": item.x, "Y": item.y, "Width": item.width, "Height": item.height,
    })
    return overlap / max(1.0, min(ax_box["Width"] * ax_box["Height"], item.width * item.height)) >= threshold


def fuse_ax_ocr(records: list[dict], ocr: list[OCRText], *, overlap_threshold: float = 0.2,
                ocr_only_threshold: float = 0.8, window_id: int | None = None) -> list[dict]:
    """Fuse OCR without replacing AX semantics or inventing ambiguous actions."""
    fused = []
    consumed: set[int] = set()
    for record in records:
        updated = {**record, "sources": list(record.get("sources") or ["ax"]),
                   "confidence": float(record.get("confidence", 1.0))}
        if updated.get("role") not in ACTIONABLE_ROLES or updated.get("offscreen"):
            fused.append(updated)
            continue
        matches = [(index, item) for index, item in enumerate(ocr)
                   if _overlapping(updated, item, overlap_threshold)]
        distinct = {item.text.strip().casefold() for _, item in matches if item.text.strip()}
        ax_name = str(updated.get("ax_name") or "").strip()
        if not ax_name and len(distinct) == 1:
            best_index, best = max(matches, key=lambda pair: pair[1].confidence)
            updated.update(name=best.text.strip(), ocr_name=best.text.strip(), sources=["ax", "ocr"],
                           confidence=min(updated["confidence"], best.confidence))
            consumed.update(index for index, _item in matches)
        elif ax_name:
            agreeing = [(index, item) for index, item in matches
                        if item.text.strip().casefold() == ax_name.casefold()]
            if agreeing:
                best_index, best = max(agreeing, key=lambda pair: pair[1].confidence)
                updated.update(ocr_name=best.text.strip(), sources=["ax", "ocr"],
                               confidence=min(updated["confidence"], best.confidence))
                consumed.add(best_index)
            elif matches and updated.get("role") in {"button", "link", "checkbox", "radio", "menuitem"}:
                updated["ocr_conflicts"] = sorted({item.text.strip() for _, item in matches if item.text.strip()})
        fused.append(updated)

    normalized_counts: dict[tuple[str, str], int] = {}
    for item in ocr:
        key = (item.role, item.text.strip().casefold())
        normalized_counts[key] = normalized_counts.get(key, 0) + 1
    next_index = max((int(record.get("index", -1)) for record in fused), default=-1) + 1
    for index, item in enumerate(ocr):
        key = (item.role, item.text.strip().casefold())
        if (index in consumed or item.role not in ACTIONABLE_ROLES or not item.text.strip()
                or item.confidence < ocr_only_threshold or normalized_counts[key] != 1
                or any(_overlapping(record, item, overlap_threshold)
                       for record in records if record.get("role") in ACTIONABLE_ROLES)):
            continue
        visual = f"window:{window_id or 'unknown'}:{round(item.x)}:{round(item.y)}:{round(item.width)}:{round(item.height)}"
        fused.append({
            "index": next_index, "name": item.text.strip(), "role": item.role, "value": "",
            "x": item.x, "y": item.y, "w": item.width, "h": item.height,
            "sources": ["ocr"], "confidence": item.confidence,
            "identity": {"visual": visual}, "ocr_name": item.text.strip(), "ocr_only": True,
        })
        next_index += 1
    return fused


OCRProvider = Callable[[WindowCapture], list[OCRText]]
