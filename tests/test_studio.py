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
        "screenRecording": False,
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
