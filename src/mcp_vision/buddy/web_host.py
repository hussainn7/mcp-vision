"""Host Plip's web UI (``web/index.html``) inside native macOS windows.

Each surface (island, mascot, settings) is a transparent WKWebView loaded from
the single-file bundle. Python -> UI: ``window.__plip([...messages])``.
UI -> Python: ``webkit.messageHandlers.plip.postMessage(JSON string)``.
All methods run on the main thread.
"""
from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from functools import lru_cache
from importlib.resources import files
from typing import Any

_CLASSES: dict[str, type] = {}


@lru_cache(maxsize=1)
def bundle_html() -> str:
    return (files("mcp_vision.buddy") / "web" / "index.html").read_text(encoding="utf-8")


def parse_command(body: Any) -> dict[str, Any] | None:
    """The UI posts JSON strings; tolerate dictionaries from older bundles."""
    try:
        if isinstance(body, dict):
            return dict(body)
        data = json.loads(str(body))
        return data if isinstance(data, dict) and isinstance(data.get("cmd"), str) else None
    except (TypeError, ValueError):
        return None


def script_for(messages: list[dict[str, Any]]) -> str:
    return f"window.__plip && window.__plip({json.dumps(messages, ensure_ascii=False)})"


def _handler_class():
    if "handler" not in _CLASSES:
        import Foundation
        import objc
        import WebKit  # noqa: F401  (registers the protocol)

        class PlipScriptHandler(Foundation.NSObject, protocols=[objc.protocolNamed("WKScriptMessageHandler")]):
            def initWithCallback_(self, callback):
                self = objc.super(PlipScriptHandler, self).init()
                if self is None:
                    return None
                self.callback = callback
                return self

            def userContentController_didReceiveScriptMessage_(self, _controller, message):
                command = parse_command(message.body())
                if command is not None:
                    try:
                        self.callback(command)
                    except Exception:
                        traceback.print_exc()

        _CLASSES["handler"] = PlipScriptHandler
    return _CLASSES["handler"]


class WebSurface:
    """One WKWebView showing one UI surface."""

    def __init__(self, surface: str, frame: Any, on_command: Callable[[dict[str, Any]], None]):
        import AppKit
        import WebKit

        self.surface = surface
        self.on_command = on_command
        self.ready = False
        self._pending: list[dict[str, Any]] = []
        config = WebKit.WKWebViewConfiguration.alloc().init()
        controller = config.userContentController()
        self._handler = _handler_class().alloc().initWithCallback_(self._received)
        controller.addScriptMessageHandler_name_(self._handler, "plip")
        preset = WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            f"window.__PLIP_SURFACE__ = {json.dumps(surface)};",
            WebKit.WKUserScriptInjectionTimeAtDocumentStart, True)
        controller.addUserScript_(preset)
        view = WebKit.WKWebView.alloc().initWithFrame_configuration_(frame, config)
        try:
            view.setValue_forKey_(False, "drawsBackground")       # transparent page background
        except Exception:
            pass
        try:
            view.setUnderPageBackgroundColor_(AppKit.NSColor.clearColor())
        except Exception:
            pass
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        self.view = view
        view.loadHTMLString_baseURL_(bundle_html(), None)

    def _received(self, command: dict[str, Any]) -> None:
        if command.get("cmd") == "ready":
            self.ready = True
            pending, self._pending = self._pending, []
            if pending:
                self._evaluate(pending)
        self.on_command(command)

    def post(self, messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        if not self.ready:
            self._pending.extend(messages)
            return
        self._evaluate(messages)

    def evaluate(self, script: str) -> None:
        self.view.evaluateJavaScript_completionHandler_(script, None)

    def _evaluate(self, messages: list[dict[str, Any]]) -> None:
        self.evaluate(script_for(messages))
