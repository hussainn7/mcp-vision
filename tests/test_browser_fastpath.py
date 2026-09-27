import asyncio

from mcp_vision.browser import BrowserRuntime
from mcp_vision.state import _identity
from phase2_mcp.cdp_snapshot import PersistentCDPSnapshotter, fuse_snapshot


def _strings(*values):
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _document(strings, *, frame="main", backend=(1, 2), bounds=((0, 0, 800, 600), (10, 20, 120, 30)),
              paint=(0, 1), names=("HTML", "BUTTON"), parents=(-1, 0), attrs=([], []), content=None,
              shadow=None, url="https://fixture.test/"):
    nodes = {
        "parentIndex": list(parents), "nodeName": [strings.index(v) for v in names],
        "backendNodeId": list(backend),
        "attributes": [[strings.index(v) for v in row] for row in attrs],
    }
    if content:
        nodes["contentDocumentIndex"] = {"index": [content[0]], "value": [content[1]]}
    if shadow is not None:
        nodes["shadowRootType"] = {"index": [shadow], "value": [strings.index("open")]}
    return {
        "frameId": frame, "documentURL": url, "nodes": nodes,
        "layout": {"nodeIndex": list(range(len(backend))), "bounds": [list(v) for v in bounds],
                   "paintOrders": list(paint),
                   "styles": [[strings.index("block"), strings.index("visible"), strings.index("1"), strings.index("auto")]
                              for _ in backend]},
    }


def _ax(backend, role="button", name="Go", **properties):
    return {"backendDOMNodeId": backend, "role": {"value": role}, "name": {"value": name},
            "properties": [{"name": key, "value": {"value": value}} for key, value in properties.items()]}


def test_backend_identity_iframe_shadow_transform_and_detached_tolerance():
    strings = _strings("HTML", "IFRAME", "BUTTON", "open", "block", "visible", "1", "auto")
    parent = _document(strings, backend=(1, 2), names=("HTML", "IFRAME"),
                       bounds=((0, 0, 800, 600), (100, 50, 300, 200)), content=(1, 1))
    child = _document(strings, frame="child", backend=(3, 4), names=("HTML", "BUTTON"),
                      bounds=((0, 0, 300, 200), (10, 20, 90, 30)), shadow=1,
                      url="https://fixture.test/frame")
    fused = fuse_snapshot({"strings": strings, "documents": [parent, child]},
                          [{"nodes": [_ax(4)]}, {"nodes": [], "detached": True}],
                          {"width": 800, "height": 600, "deviceScaleFactor": 2})
    element = fused["elements"][0]
    assert element["backendNodeId"] == 4
    assert element["identity"] == "cdp:child:4"
    assert (element["x"], element["y"]) == (55, 35)
    assert element["shadow"] is True
    assert fused["completeness"]["detached_frames"] == 1
    assert fused["completeness"]["complete"] is False


def test_paint_order_occlusion_and_visibility_pruning():
    strings = _strings("HTML", "BUTTON", "DIV", "block", "visible", "1", "auto")
    doc = _document(strings, backend=(1, 2, 3), names=("HTML", "BUTTON", "DIV"), parents=(-1, 0, 0),
                    bounds=((0, 0, 800, 600), (10, 20, 120, 30), (0, 0, 200, 100)), paint=(0, 1, 9),
                    attrs=([], [], []))
    fused = fuse_snapshot({"strings": strings, "documents": [doc]}, [{"nodes": [_ax(2)]}],
                          {"width": 800, "height": 600})
    assert fused["elements"] == []
    assert fused["pruned"]["occluded"] == 1


class _Session:
    def __init__(self, dom):
        self.dom = dom
        self.calls = []
        self.detached = False

    async def send(self, method, params):
        self.calls.append(method)
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "main"}}}
        if method == "DOMSnapshot.captureSnapshot":
            return self.dom
        if method == "Runtime.evaluate":
            return {"result": {"value": {"width": 800, "height": 600, "deviceScaleFactor": 1,
                                           "url": "https://fixture.test/", "title": "Fixture", "focus": "BODY"}}}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": [_ax(2)]}
        raise AssertionError(method)

    async def detach(self):
        self.detached = True


def test_protocol_budget_no_screenshot_and_deterministic_capture():
    strings = _strings("HTML", "BUTTON", "block", "visible", "1", "auto")
    dom = {"strings": strings, "documents": [_document(strings)]}
    session = _Session(dom)
    snapper = PersistentCDPSnapshotter(session)
    first = asyncio.run(snapper.capture())
    second = asyncio.run(snapper.capture())
    assert first["elements"] == second["elements"]
    assert first["protocol_calls"] == 4
    assert len(session.calls) == 8
    assert all("screenshot" not in call.lower() for call in session.calls)


def test_runtime_reuses_connection_and_detaches_without_closing_browser():
    class Context:
        def __init__(self):
            self.created = 0
        async def new_cdp_session(self, page):
            self.created += 1
            return _Session({"strings": [], "documents": []})
    class Page:
        def __init__(self):
            self.context = Context()
    async def case():
        page = Page()
        runtime = BrowserRuntime(page=page)
        one = await runtime._ensure_cdp()
        two = await runtime._ensure_cdp()
        assert one is two and page.context.created == 1
        session = runtime._cdp_session
        await runtime._detach_cdp()
        assert session.detached
    asyncio.run(case())


def test_fallback_identity_is_scoped_and_deterministic():
    rec = {"frameId": "f", "role": "button", "name": "Save", "x": 1, "y": 2, "w": 3, "h": 4}
    assert _identity(rec) == _identity(dict(rec))
    assert _identity(rec).startswith("observed:f:")
