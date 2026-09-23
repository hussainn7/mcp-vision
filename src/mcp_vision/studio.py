"""Loopback-only Mission Control. No model keys, arbitrary URL runner, or telemetry."""
from __future__ import annotations

import asyncio
import json
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from pydantic import ValidationError

from mcp_vision.missions import Mission, RECIPES, brief
from mcp_vision.context import Context
from mcp_vision.redaction import redact
from mcp_vision.utils.config_sync import _entry
from mcp_vision.fast_policy import jev_status


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=7331, *, invocation_handler=None, provider=None,
                 permission_handler=None, permission_request_handler=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.demo_lock = threading.Lock()
        self.context_lock = threading.Lock()
        self.contexts = OrderedDict()
        self.invocation_handler = invocation_handler
        self.provider = provider
        self.permission_handler = permission_handler
        self.permission_request_handler = permission_request_handler

    def permissions(self):
        if self.permission_handler is None:
            return {"scope": "studio-process", "available": False}
        return {"available": True, **self.permission_handler()}

    def accept_context(self, context: Context) -> Context:
        with self.context_lock:
            self.contexts[context.context_id] = context
            while len(self.contexts) > 24:
                self.contexts.popitem(last=False)
        if self.invocation_handler:
            self.invocation_handler(context)
        return context

    def get_context(self, context_id: str = "") -> Context | None:
        with self.context_lock:
            if context_id:
                return self.contexts.get(context_id)
            return next(reversed(self.contexts.values()), None) if self.contexts else None

    def invoke_assistant(self) -> Context:
        """Open the native assistant from a visible, testable UI entry point."""
        if self.invocation_handler is None:
            raise RuntimeError("Launch /Applications/MCP-Vision.app to open the desktop assistant.")
        context = self.get_context() or Context(source="api")
        self.invocation_handler(context)
        return context

    def answer(self, request: str, context_id: str = "") -> dict:
        context = self.get_context(context_id)
        if context is None:
            context = self.accept_context(Context(source="api"))
        context = context.model_copy(update={"user_request": request.strip()})
        # Keep API, popup, and voice requests on one router. Calling
        # answer_context directly here silently reduced this surface to text-only.
        from mcp_vision.tasks import ContextTask
        from mcp_vision.execution import bind_context_backend

        async def execute():
            task = ContextTask(context, provider=self.provider)
            if task.requires_surface_backend:
                task.backend = await bind_context_backend(context, mode=task.mode)
            try:
                return await task.run()
            finally:
                if task.backend and hasattr(task.backend, "close"):
                    await task.backend.close()

        return asyncio.run(execute())


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
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://") and self.path == "/api/context":
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", f"http://{host}")
        extension = (origin.startswith("chrome-extension://") and self.path == "/api/context"
                     and self.headers.get("X-MCP-Vision") == "chrome-extension")
        requested_headers = self.headers.get("Access-Control-Request-Headers", "").lower()
        preflight = (self.command == "OPTIONS" and origin.startswith("chrome-extension://")
                     and self.path == "/api/context" and "x-mcp-vision" in requested_headers)
        if host not in hosts or (origin != f"http://{host}" and not extension and not preflight):
            self._reply(403, {"error": "Open Mission Control from its local URL."})
            return False
        return True

    def do_OPTIONS(self):
        if not self._local():
            return
        if self.path != "/api/context":
            self._reply(404, {"error": "Not found"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MCP-Vision")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        if not self._local():
            return
        if self.path == "/api/info":
            self._reply(200, {"recipes": RECIPES, "server": _entry(browser_mode="live"),
                              "execution": "host", "demo": "isolated-chromium",
                              "contextual": True, "permissions": self.server.permissions(),
                              "providers": {"jev": jev_status()}})
            return
        if self.path == "/api/status":
            latest = self.server.get_context()
            self._reply(200, {"runtime": "ready", "contextual": True,
                              "latestContext": latest.context_id if latest else None,
                              "latestApplication": latest.source_application if latest else None,
                              "latestTitle": latest.title if latest else None,
                              "latestFocused": ({"role": latest.focused_element.role,
                                                 "name": latest.focused_element.name}
                                                if latest and latest.focused_element else None),
                              "permissions": self.server.permissions()})
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
        marker = self.headers.get("X-MCP-Vision")
        if marker not in {"studio", "chrome-extension", "contextual-ui"} or not self.headers.get("Content-Type", "").startswith("application/json"):
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
            if self.path == "/api/context":
                context = self.server.accept_context(Context.model_validate(data))
                self._reply(202, {"ok": True, "contextId": context.context_id})
            elif self.path == "/api/ask":
                request = data.get("request", "")
                if not isinstance(request, str) or not request.strip() or len(request) > 12000:
                    raise ValueError("Give MCP-Vision a request between 1 and 12000 characters.")
                self._reply(200, self.server.answer(request, str(data.get("contextId", ""))))
            elif self.path == "/api/brief":
                self._reply(200, brief(Mission(**data)))
            elif self.path == "/api/request-screen-recording":
                if self.server.permission_request_handler is None:
                    self._reply(409, {"error": "Launch /Applications/MCP-Vision.app to request macOS access."})
                else:
                    self._reply(200, {"permissions": {"available": True,
                                                      **self.server.permission_request_handler()}})
            elif self.path == "/api/invoke":
                context = self.server.invoke_assistant()
                self._reply(202, {"ok": True, "contextId": context.context_id})
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


def serve_studio(port=7331, *, invocation_handler=None, provider=None):
    with StudioServer(port, invocation_handler=invocation_handler, provider=provider) as server:
        print(f"Mission Control → http://127.0.0.1:{server.server_port}", flush=True)
        print("Local workspace. Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
