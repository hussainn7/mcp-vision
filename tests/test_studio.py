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


def test_assets_and_brief(studio):
    for path in ("/", "/app.js", "/style.css", "/favicon.svg"):
        assert request(studio, path)[0] == 200
    status, body = request(studio, "/api/brief", {"goal": "Inspect the page"},
                           {"Content-Type": "application/json", "X-MCP-Vision": "studio"})
    assert status == 200 and json.loads(body)["executed"] is False


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
