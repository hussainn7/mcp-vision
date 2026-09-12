"""Loopback-only Mission Control. No model keys, arbitrary URL runner, or telemetry."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from pydantic import ValidationError

from mcp_vision.missions import Mission, RECIPES, brief
from mcp_vision.redaction import redact
from mcp_vision.utils.config_sync import _entry


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=7331):
        super().__init__(("127.0.0.1", port), Handler)
        self.demo_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Task text does not belong in access logs.

    def _reply(self, status, body, content_type="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        if host not in hosts or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
            self._reply(403, {"error": "Open Mission Control from its local URL."})
            return False
        return True

    def do_GET(self):
        if not self._local():
            return
        if self.path == "/api/info":
            self._reply(200, {"recipes": RECIPES, "server": _entry(), "execution": "host",
                              "demo": "isolated-chromium"})
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/favicon.svg": ("favicon.svg", "image/svg+xml")}
        if self.path not in assets:
            self._reply(404, {"error": "Not found"})
            return
        name, kind = assets[self.path]
        self._reply(200, files("mcp_vision").joinpath("static", name).read_bytes(), kind)

    def do_POST(self):
        if not self._local():
            return
        if self.headers.get("X-MCP-Vision") != "studio" or self.headers.get("Content-Type") != "application/json":
            self._reply(403, {"error": "Use the local Mission Control interface."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 24000:
                self._reply(413, {"error": "Request must be between 1 and 24000 bytes."})
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            if self.path == "/api/brief":
                self._reply(200, brief(Mission(**data)))
            elif self.path == "/api/demo":
                title = data.get("title", "My first mission")
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
                    raise ValueError("Give the demo a title between 1 and 200 characters.")
                if not self.server.demo_lock.acquire(blocking=False):
                    self._reply(409, {"error": "A demo is already running. Wait for its result."})
                    return
                try:
                    from mcp_vision.demo import studio_demo
                    self._reply(200, asyncio.run(studio_demo(title.strip())))
                finally:
                    self.server.demo_lock.release()
            else:
                self._reply(404, {"error": "Not found"})
        except (ValueError, ValidationError) as exc:
            self._reply(400, {"error": str(exc)})
        except Exception as exc:
            self._reply(500, {"error": redact(str(exc)),
                              "help": "Install Chromium with: python -m playwright install chromium"})


def serve_studio(port=7331):
    with StudioServer(port) as server:
        print(f"Mission Control → http://127.0.0.1:{server.server_port}", flush=True)
        print("Local workspace. Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
