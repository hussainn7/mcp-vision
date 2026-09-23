import json
import threading
from http.client import HTTPConnection

import pytest

from mcp_vision.studio import StudioServer


@pytest.fixture
def studio():
    server = StudioServer(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, path, data=None, headers=None):
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request("GET" if data is None else "POST", path,
                 body=None if data is None else json.dumps(data), headers=headers or {})
    response = conn.getresponse()
    status, body = response.status, response.read()
    conn.close()
    return status, body


def options(server, path, headers):
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request("OPTIONS", path, headers=headers)
    response = conn.getresponse()
    status, response_headers = response.status, dict(response.getheaders())
    response.read()
    conn.close()
    return status, response_headers


def test_assets_and_brief(studio):
    for path in ("/", "/app.js", "/style.css", "/favicon.svg"):
        assert request(studio, path)[0] == 200
    status, body = request(studio, "/api/brief", {"goal": "Inspect the page"},
                           {"Content-Type": "application/json", "X-MCP-Vision": "studio"})
    assert status == 200 and json.loads(body)["executed"] is False


def test_status_reports_permission_identity_from_native_process():
    server = StudioServer(0, permission_handler=lambda: {
        "scope": "current-process", "bundleId": "org.mcpvision.contextual",
        "bundlePath": "/Applications/MCP-Vision.app", "accessibility": True,
        "screenRecording": False, "microphone": True, "speechRecognition": None,
    })
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = request(server, "/api/status")
        permissions = json.loads(body)["permissions"]
        assert status == 200
        assert permissions["available"] is True
        assert permissions["bundleId"] == "org.mcpvision.contextual"
        assert permissions["accessibility"] is True
        assert permissions["screenRecording"] is False
        assert permissions["microphone"] is True
        assert permissions["speechRecognition"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_screen_recording_request_requires_native_handler():
    headers = {"Content-Type": "application/json", "X-MCP-Vision": "studio"}
    server = StudioServer(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert request(server, "/api/request-screen-recording", {}, headers)[0] == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_visible_assistant_entry_point_invokes_native_handler():
    captured = []
    server = StudioServer(0, invocation_handler=captured.append)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = {"Content-Type": "application/json", "X-MCP-Vision": "studio"}
        status, body = request(server, "/api/invoke", {}, headers)
        assert status == 202 and json.loads(body)["ok"] is True
        assert len(captured) == 1 and captured[0].source == "api"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_rejects_cross_origin_and_untrusted_hosts(studio):
    assert request(studio, "/api/info", headers={"Host": "attacker.test"})[0] == 403
    assert request(studio, "/api/brief", {"goal": "Inspect"},
                   {"Content-Type": "application/json", "X-MCP-Vision": "studio",
                    "Origin": "https://attacker.test"})[0] == 403
    assert request(studio, "/api/demo", {"title": "Test"})[0] == 403
    assert request(studio, "/../pyproject.toml")[0] == 404


def test_rejects_bad_mission_and_demo_input(studio):
    headers = {"Content-Type": "application/json", "X-MCP-Vision": "studio"}
    assert request(studio, "/api/brief", {"goal": "Inspect", "url": "file:///etc/passwd"}, headers)[0] == 400
    assert request(studio, "/api/demo", {"title": " "}, headers)[0] == 400


def test_context_reaches_runtime_and_ask_returns_response(studio):
    headers = {"Content-Type": "application/json", "X-MCP-Vision": "studio"}
    status, body = request(studio, "/api/context", {
        "source": "chrome", "source_application": "Google Chrome",
        "url": "https://example.test", "title": "Example",
        "selected_text": "A useful selected sentence.",
        "cursor_position": {"x": 12, "y": 34},
    }, headers)
    assert status == 202
    context_id = json.loads(body)["contextId"]
    assert studio.get_context(context_id).title == "Example"
    status, body = request(studio, "/api/status")
    runtime = json.loads(body)
    assert status == 200 and runtime["latestApplication"] == "Google Chrome"
    assert runtime["latestTitle"] == "Example"
    assert runtime["latestFocused"] is None
    studio.provider = "definitely-unavailable"
    status, body = request(studio, "/api/ask", {
        "contextId": context_id, "request": "What does this mean?"
    }, headers)
    result = json.loads(body)
    assert status == 200 and result["capability"] == "ask"
    assert "selected sentence" in result["answer"]


def test_ask_endpoint_routes_native_actions_without_a_model(studio, monkeypatch):
    calls = []
    monkeypatch.setattr("mcp_vision.native_apps.perform", lambda action, value, pid=0, bundle_id="":
                        calls.append((action, value)) or
                        {"ok": True, "verified": True, "message": "Opened Notes."})
    headers = {"Content-Type": "application/json", "X-MCP-Vision": "studio"}
    status, body = request(studio, "/api/ask", {"request": "Can you open up Notes for me?"}, headers)
    result = json.loads(body)
    assert status == 200 and result["state"] == "review"
    assert result["answer"] == "Opened Notes."
    assert calls == [("open_app", "Notes")]


def test_ask_endpoint_binds_generic_surface_actions(studio, monkeypatch):
    from mcp_vision.context import Context

    class Backend:
        closed = False
        async def close(self):
            self.closed = True

    backend = Backend()
    bound = []
    async def bind(context, **options):
        bound.append((context.source_application, options["mode"]))
        return backend
    async def run(self):
        assert self.backend is backend
        return self.result("review", "Created through the observed interface.")
    monkeypatch.setattr("mcp_vision.execution.bind_context_backend", bind)
    monkeypatch.setattr("mcp_vision.tasks.ContextTask.run", run)
    context = studio.accept_context(Context(
        source="macos", source_application="Fixture", accessibility_context={"pid": 42}))
    headers = {"Content-Type": "application/json", "X-MCP-Vision": "studio"}
    status, body = request(studio, "/api/ask", {
        "contextId": context.context_id, "request": "Create a new document",
    }, headers)
    result = json.loads(body)
    assert status == 200 and result["state"] == "review"
    assert bound == [("Fixture", "act")] and backend.closed is True


def test_ask_endpoint_binds_native_in_app_actions_to_general_surface(studio, monkeypatch):
    from mcp_vision.context import Context

    class Backend:
        closed = False
        async def close(self):
            self.closed = True

    backend = Backend()
    bound = []
    async def bind(context, **options):
        bound.append((context.source_application, options['mode']))
        return backend
    async def run(self):
        assert self.route.kind == 'surface'
        assert self.backend is backend
        return self.result('review', 'Created through the observed interface.')
    monkeypatch.setattr('mcp_vision.execution.bind_context_backend', bind)
    monkeypatch.setattr('mcp_vision.tasks.ContextTask.run', run)
    context = studio.accept_context(Context(
        source='macos', source_application='Google Chrome', accessibility_context={'pid': 42}))
    headers = {'Content-Type': 'application/json', 'X-MCP-Vision': 'studio'}
    status, body = request(studio, '/api/ask', {
        'contextId': context.context_id, 'request': 'Create a new tab',
    }, headers)
    assert status == 200 and json.loads(body)['state'] == 'review'
    assert bound == [('Google Chrome', 'act')] and backend.closed is True


def test_chrome_extension_origin_is_limited_to_context_endpoint(studio):
    headers = {"Host": f"127.0.0.1:{studio.server_port}",
               "Origin": "chrome-extension://example",
               "Content-Type": "application/json", "X-MCP-Vision": "chrome-extension"}
    assert request(studio, "/api/context", {"source": "chrome"}, headers)[0] == 202
    assert request(studio, "/api/ask", {"request": "hello"}, headers)[0] == 403
    status, response_headers = options(studio, "/api/context", {
        "Host": f"127.0.0.1:{studio.server_port}", "Origin": "chrome-extension://example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, x-mcp-vision",
    })
    assert status == 204
    assert response_headers["Access-Control-Allow-Origin"] == "chrome-extension://example"
