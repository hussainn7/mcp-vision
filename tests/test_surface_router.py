from mcp_vision.surface_router import SurfaceContext, route_surface


def test_web_dom_prefers_browser_with_visual_fallback():
    decision = route_surface(SurfaceContext(url="https://example.com", dom_available=True,
                                            target_role="button"))
    assert decision.primary == "browser" and decision.fallback[-1] == "vision"
    assert decision.observe_only


def test_canvas_uses_hybrid_and_native_app_uses_accessibility_plus_vision():
    canvas = route_surface(SurfaceContext(url="https://maps.example", dom_available=True,
                                          target_role="canvas"))
    native = route_surface(SurfaceContext(application="Preview", accessibility_available=True))
    assert canvas.primary == "hybrid"
    assert native.primary == "hybrid"


def test_unknown_surface_falls_back_to_vision_without_write_authority():
    decision = route_surface(SurfaceContext(application="Unknown"))
    assert decision.primary == "vision" and decision.observe_only
