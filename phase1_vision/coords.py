"""Viewport coordinate transforms: HiDPI, CSS zoom, and scroll offsets.

All click/grounding paths go through here so a 2x Retina grab and a 1x
logical pyautogui click never get mixed up, and a CSS-zoomed page doesn't
send the mouse to a hallucinated pixel.
"""

from dataclasses import dataclass


@dataclass
class Viewport:
    css_w: float
    css_h: float
    screenshot_w: float
    screenshot_h: float
    dpr: float = 1.0
    zoom: float = 1.0
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    origin_x: float = 0.0
    origin_y: float = 0.0

    @property
    def sx(self) -> float:
        """CSS px -> screenshot px, x."""
        if self.css_w <= 0:
            return 1.0
        return self.screenshot_w / self.css_w

    @property
    def sy(self) -> float:
        """CSS px -> screenshot px, y."""
        if self.css_h <= 0:
            return 1.0
        return self.screenshot_h / self.css_h


def from_display(display: dict, screenshot_size: tuple[int, int] | None = None) -> Viewport:
    """Build a Viewport from app_detector.get_display_info() records."""
    w = float(display.get("width") or 1920)
    h = float(display.get("height") or 1080)
    scale = float(display.get("scale_factor") or 1.0)
    sw, sh = screenshot_size if screenshot_size else (int(w), int(h))
    return Viewport(
        css_w=w / scale if scale else w,
        css_h=h / scale if scale else h,
        screenshot_w=float(sw),
        screenshot_h=float(sh),
        dpr=scale,
        origin_x=float(display.get("origin_x") or 0),
        origin_y=float(display.get("origin_y") or 0),
    )


def from_browser(metrics: dict, screenshot_size: tuple[int, int] | None = None) -> Viewport:
    """Build a Viewport from window/visualViewport metrics (page.evaluate)."""
    css_w = float(metrics.get("innerWidth") or metrics.get("css_w") or 0)
    css_h = float(metrics.get("innerHeight") or metrics.get("css_h") or 0)
    dpr = float(metrics.get("devicePixelRatio") or metrics.get("dpr") or 1.0)
    zoom = float(metrics.get("zoom") or 1.0)
    sw, sh = screenshot_size if screenshot_size else (int(css_w * dpr * zoom), int(css_h * dpr * zoom))
    return Viewport(
        css_w=css_w,
        css_h=css_h,
        screenshot_w=float(sw),
        screenshot_h=float(sh),
        dpr=dpr,
        zoom=zoom,
        scroll_x=float(metrics.get("scrollX") or metrics.get("scroll_x") or 0),
        scroll_y=float(metrics.get("scrollY") or metrics.get("scroll_y") or 0),
    )


def css_to_screenshot(x: float, y: float, vp: Viewport) -> tuple[float, float]:
    """Viewport CSS pixels -> pixels in the captured screenshot."""
    return x * vp.sx, y * vp.sy


def screenshot_to_css(x: float, y: float, vp: Viewport) -> tuple[float, float]:
    """Screenshot pixels -> viewport CSS pixels (what Playwright mouse.click wants)."""
    sx = vp.sx or 1.0
    sy = vp.sy or 1.0
    return x / sx, y / sy


def css_to_page(x: float, y: float, vp: Viewport) -> tuple[float, float]:
    """Viewport CSS -> document (page) coordinates, adding scroll."""
    return x + vp.scroll_x, y + vp.scroll_y


def page_to_css(x: float, y: float, vp: Viewport) -> tuple[float, float]:
    """Document coordinates -> viewport CSS, subtracting scroll."""
    return x - vp.scroll_x, y - vp.scroll_y


def css_to_screen(x: float, y: float, vp: Viewport) -> tuple[int, int]:
    """Viewport CSS -> logical screen points for pyautogui (origin = display origin)."""
    return int(round(x + vp.origin_x)), int(round(y + vp.origin_y))


def screenshot_to_click(x: float, y: float, vp: Viewport) -> tuple[int, int]:
    """Screenshot pixel -> pyautogui click point (logical, display-relative)."""
    cx, cy = screenshot_to_css(x, y, vp)
    return css_to_screen(cx, cy, vp)


def normalize_box(box: dict, vp: Viewport, space: str = "css") -> dict:
    """Return a box in CSS viewport pixels regardless of input space.

    `space` is 'css' (already viewport CSS), 'page' (document), or 'screenshot'.
    Box keys: x, y, w, h  (also accepts width/height).
    """
    x = float(box.get("x", 0))
    y = float(box.get("y", 0))
    w = float(box.get("w", box.get("width", 0)))
    h = float(box.get("h", box.get("height", 0)))
    if space == "page":
        x, y = page_to_css(x, y, vp)
    elif space == "screenshot":
        x, y = screenshot_to_css(x, y, vp)
        w, h = w / (vp.sx or 1.0), h / (vp.sy or 1.0)
    return {
        "x": int(round(x)),
        "y": int(round(y)),
        "w": int(round(w)),
        "h": int(round(h)),
        "cx": int(round(x + w / 2)),
        "cy": int(round(y + h / 2)),
    }


def clamp_point(x: float, y: float, vp: Viewport, margin: float = 1.0) -> tuple[float, float]:
    """Keep a click inside the viewport so HiDPI rounding can't miss off-screen."""
    return (
        min(max(x, margin), max(vp.css_w - margin, margin)),
        min(max(y, margin), max(vp.css_h - margin, margin)),
    )


VIEWPORT_METRICS_JS = """() => {
  const vv = window.visualViewport;
  return {
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio || 1,
    scrollX: window.scrollX || 0,
    scrollY: window.scrollY || 0,
    zoom: (vv && window.innerWidth) ? (vv.width / window.innerWidth) : 1,
  };
}"""


def demo():
    # Retina: 3024x1964 grab of a 1512x982 logical display
    vp = Viewport(css_w=1512, css_h=982, screenshot_w=3024, screenshot_h=1964, dpr=2.0)
    assert abs(vp.sx - 2.0) < 1e-9 and abs(vp.sy - 2.0) < 1e-9
    assert css_to_screenshot(100, 50, vp) == (200.0, 100.0)
    assert screenshot_to_css(200, 100, vp) == (100.0, 50.0)
    assert screenshot_to_click(200, 100, vp) == (100, 50)

    # 1:1 non-retina
    vp1 = Viewport(css_w=1280, css_h=800, screenshot_w=1280, screenshot_h=800, dpr=1.0)
    assert screenshot_to_click(100, 50, vp1) == (100, 50)

    # CSS zoom 150%: screenshot is 1.5x CSS
    vpz = Viewport(css_w=1280, css_h=800, screenshot_w=1920, screenshot_h=1200, dpr=1.0, zoom=1.5)
    sx, sy = css_to_screenshot(100, 40, vpz)
    assert abs(sx - 150) < 1e-6 and abs(sy - 60) < 1e-6
    cx, cy = screenshot_to_css(sx, sy, vpz)
    assert abs(cx - 100) < 1e-6 and abs(cy - 40) < 1e-6

    # scroll offsets: page (500, 900) with scrollY=800 is at viewport y=100
    vps = Viewport(css_w=1280, css_h=800, screenshot_w=1280, screenshot_h=800, scroll_y=800)
    assert page_to_css(500, 900, vps) == (500, 100)
    assert css_to_page(500, 100, vps) == (500, 900)

    # display origin (second monitor)
    vpo = Viewport(css_w=1920, css_h=1080, screenshot_w=1920, screenshot_h=1080, origin_x=1920)
    assert css_to_screen(10, 20, vpo) == (1930, 20)

    # box normalization from screenshot space on retina
    box = normalize_box({"x": 200, "y": 100, "w": 80, "h": 40}, vp, space="screenshot")
    assert box == {"x": 100, "y": 50, "w": 40, "h": 20, "cx": 120, "cy": 60}

    box_page = normalize_box({"x": 500, "y": 900, "w": 20, "h": 10}, vps, space="page")
    assert box_page["y"] == 100 and box_page["x"] == 500

    # clamp never goes off-screen, even for a 0-size viewport
    cx, cy = clamp_point(-10, 9999, vp1)
    assert 1.0 <= cx <= 1279 and 1.0 <= cy <= 799
    tiny = Viewport(css_w=0, css_h=0, screenshot_w=0, screenshot_h=0)
    assert clamp_point(5, 5, tiny) == (1.0, 1.0)
    assert tiny.sx == 1.0

    # from_display: physical pixels + scale_factor 2
    vp_d = from_display({"width": 3024, "height": 1964, "scale_factor": 2.0, "origin_x": 0, "origin_y": 0},
                        screenshot_size=(3024, 1964))
    assert abs(vp_d.css_w - 1512) < 1e-6
    assert screenshot_to_click(3024, 0, vp_d)[0] == 1512

    # from_browser: dpr 2, no screenshot size given -> physical guess
    vp_b = from_browser({"innerWidth": 1280, "innerHeight": 800, "devicePixelRatio": 2,
                         "scrollX": 0, "scrollY": 40, "zoom": 1})
    assert vp_b.screenshot_w == 2560 and vp_b.scroll_y == 40

    print("ok")


if __name__ == "__main__":
    demo()
