"""Persistent CDP semantic observations.

The capture path is intentionally protocol-only: DOMSnapshot and complete AX
trees are fused by backendNodeId.  No page-authored selector is retained as an
identity and screenshots are never part of a normal observation.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any

from mcp_vision.state import _identity
from phase2_mcp.ax_tree import INTERACTIVE_ROLES

COMPUTED_STYLES = ["display", "visibility", "opacity", "pointer-events"]


def _value(value: Any, default=""):
    if isinstance(value, dict):
        return value.get("value", default)
    return default if value is None else value


def _string(strings: list[str], index: Any, default="") -> str:
    return strings[index] if isinstance(index, int) and 0 <= index < len(strings) else default


def _rare(rare: dict | None) -> dict[int, Any]:
    if not rare:
        return {}
    return dict(zip(rare.get("index", []), rare.get("value", [])))


def _attrs(raw: list[int] | None, strings: list[str]) -> dict[str, str]:
    values = [_string(strings, value) for value in (raw or [])]
    return {values[i].lower(): values[i + 1] for i in range(0, len(values) - 1, 2)}


def _quad_rect(bounds: list[float] | None) -> dict | None:
    if not bounds or len(bounds) < 4:
        return None
    x, y, w, h = (float(v) for v in bounds[:4])
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    return {"x": x, "y": y, "w": w, "h": h, "cx": x + w / 2, "cy": y + h / 2}


def _frame_offsets(documents: list[dict]) -> dict[int, tuple[float, float]]:
    """Compute child-document offsets from iframe contentDocumentIndex links."""
    links: dict[int, tuple[int, int]] = {}
    for parent_i, doc in enumerate(documents):
        nodes, layout = doc.get("nodes", {}), doc.get("layout", {})
        content_docs = _rare(nodes.get("contentDocumentIndex"))
        layout_by_node = {node: pos for pos, node in enumerate(layout.get("nodeIndex", []))}
        for node_i, child_i in content_docs.items():
            if not isinstance(child_i, int):
                continue
            pos = layout_by_node.get(node_i)
            bounds = layout.get("bounds", [])
            rect = _quad_rect(bounds[pos]) if pos is not None and pos < len(bounds) else None
            if rect:
                links[child_i] = (parent_i, node_i)
    offsets: dict[int, tuple[float, float]] = {0: (0.0, 0.0)}
    pending = set(range(1, len(documents)))
    for _ in range(len(documents) + 1):
        progressed = False
        for child_i in list(pending):
            link = links.get(child_i)
            if not link or link[0] not in offsets:
                continue
            parent_i, node_i = link
            layout = documents[parent_i].get("layout", {})
            try:
                pos = layout.get("nodeIndex", []).index(node_i)
                rect = _quad_rect(layout.get("bounds", [])[pos])
            except (ValueError, IndexError):
                rect = None
            px, py = offsets[parent_i]
            offsets[child_i] = (px + (rect or {}).get("x", 0), py + (rect or {}).get("y", 0))
            pending.remove(child_i)
            progressed = True
        if not progressed:
            break
    for child_i in pending:
        offsets[child_i] = (0.0, 0.0)
    return offsets


def _ax_properties(node: dict) -> dict:
    out = {}
    for item in node.get("properties", []) or []:
        name = item.get("name")
        if name:
            out[name] = _value(item.get("value"), None)
    return out


def _covered(candidate: dict, visible: list[dict]) -> bool:
    """Conservative paint-order occlusion at the proposed click point."""
    cx, cy, paint = candidate["cx"], candidate["cy"], candidate.get("paint_order", 0)
    for other in visible:
        if other is candidate or other.get("paint_order", 0) <= paint:
            continue
        if other["x"] <= cx <= other["x"] + other["w"] and other["y"] <= cy <= other["y"] + other["h"]:
            # Ancestors and zero-opacity boxes do not cover their descendants.
            if (candidate.get("backendNodeId") in other.get("ancestors", ())
                    or other.get("backendNodeId") in candidate.get("ancestors", ())):
                continue
            return True
    return False


def layout_scale(documents: list[dict], viewport: dict) -> float:
    """How many DOMSnapshot layout units make one CSS pixel.

    Chromium builds that zoom for device scale report layout bounds in device
    pixels; current builds report CSS pixels. Assuming either breaks clicks
    on Retina displays (every target looks "moved" by a factor of two), so
    measure it: the root document's ``contentWidth`` is in layout units and
    ``documentElement.scrollWidth`` is in CSS pixels.
    """
    dpr = float(viewport.get("deviceScaleFactor") or 1)
    if dpr <= 0:
        dpr = 1.0
    root = documents[0] if documents else {}
    content = float(root.get("contentWidth") or 0)
    css = float(viewport.get("cssContentWidth") or 0)
    if content <= 0 or css <= 0:
        return dpr                      # no calibration data: keep the historical assumption
    ratio = content / css
    return dpr if abs(ratio - dpr) < abs(ratio - 1.0) else 1.0


def fuse_snapshot(dom: dict, ax_trees: list[dict], viewport: dict, *, max_elements=60) -> dict:
    """Fuse a CDP DOMSnapshot capture and per-frame AX trees."""
    strings, documents = dom.get("strings", []), dom.get("documents", [])
    offsets = _frame_offsets(documents)
    device_scale = layout_scale(documents, viewport)
    scroll_x = float(viewport.get("scrollX") or 0)
    scroll_y = float(viewport.get("scrollY") or 0)
    dom_nodes: dict[int, dict] = {}
    all_boxes: list[dict] = []
    detached_frames = 0
    for doc_i, doc in enumerate(documents):
        nodes, layout = doc.get("nodes", {}), doc.get("layout", {})
        frame_id = _string(strings, doc.get("frameId")) or str(doc.get("frameId") or f"document-{doc_i}")
        document_url = _string(strings, doc.get("documentURL")) or str(doc.get("documentURL") or "")
        ox, oy = offsets.get(doc_i, (0.0, 0.0))
        parent = nodes.get("parentIndex", [])
        shadow = _rare(nodes.get("shadowRootType"))
        input_values = _rare(nodes.get("inputValue"))
        input_checked = set((nodes.get("inputChecked") or {}).get("index", []))
        attrs = nodes.get("attributes", [])
        backend_ids = nodes.get("backendNodeId", [])
        layout_nodes = layout.get("nodeIndex", [])
        bounds = layout.get("bounds", [])
        paint = layout.get("paintOrders", [])
        styles = layout.get("styles", [])
        for pos, node_i in enumerate(layout_nodes):
            if node_i >= len(backend_ids) or pos >= len(bounds):
                continue
            rect = _quad_rect(bounds[pos])
            if not rect:
                continue
            rect["x"], rect["y"] = ((rect["x"] + ox) / device_scale - scroll_x,
                                      (rect["y"] + oy) / device_scale - scroll_y)
            rect["w"], rect["h"] = rect["w"] / device_scale, rect["h"] / device_scale
            rect["cx"], rect["cy"] = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
            style_values = styles[pos] if pos < len(styles) else []
            style = {name: _string(strings, value) for name, value in zip(COMPUTED_STYLES, style_values)}
            ancestors = []
            cur = node_i
            while 0 <= cur < len(parent):
                cur = parent[cur]
                if cur < 0:
                    break
                if cur < len(backend_ids):
                    ancestors.append(backend_ids[cur])
            raw_attrs = attrs[node_i] if node_i < len(attrs) else []
            rec = {
                **rect,
                "backendNodeId": backend_ids[node_i], "frameId": frame_id,
                "document_url": document_url,
                "frame_offset_x": ox / device_scale, "frame_offset_y": oy / device_scale,
                "root_scroll_x": scroll_x, "root_scroll_y": scroll_y,
                "root_document": doc_i == 0,
                "node_name": _string(strings, nodes.get("nodeName", [])[node_i])
                    if node_i < len(nodes.get("nodeName", [])) else "",
                "attributes": _attrs(raw_attrs, strings),
                "paint_order": paint[pos] if pos < len(paint) else pos,
                "style": style, "ancestors": tuple(ancestors),
                "shadow": node_i in shadow,
            }
            if node_i in input_values:
                rec["input_value"] = _string(strings, input_values[node_i])
            if rec["node_name"].upper() == "INPUT":
                rec["input_checked"] = node_i in input_checked
            if rec["node_name"].upper() == "SELECT":
                options = []
                node_values = nodes.get("nodeValue", [])
                node_names = nodes.get("nodeName", [])
                for option_i, option_parent in enumerate(parent):
                    if option_parent != node_i or option_i >= len(node_names):
                        continue
                    if _string(strings, node_names[option_i]).upper() != "OPTION":
                        continue
                    option_attrs = _attrs(attrs[option_i] if option_i < len(attrs) else [], strings)
                    label_parts = []
                    for text_i, text_parent in enumerate(parent):
                        if text_parent == option_i and text_i < len(node_values):
                            label_parts.append(_string(strings, node_values[text_i]))
                    options.append({"value": option_attrs.get("value", ""),
                                    "label": " ".join(label_parts).strip(),
                                    "disabled": "disabled" in option_attrs})
                rec["options"] = options
            dom_nodes[rec["backendNodeId"]] = rec
            all_boxes.append(rec)

        # Some native controls expose AX nodes from their user-agent shadow
        # tree while only the host has a layout box.  Inherit the nearest
        # laid-out ancestor's geometry and act on that stable host backend id.
        layout_pos = {node_i: pos for pos, node_i in enumerate(layout_nodes)}
        for node_i, backend in enumerate(backend_ids):
            if backend in dom_nodes:
                continue
            cur = node_i
            while 0 <= cur < len(parent) and cur not in layout_pos:
                cur = parent[cur]
            if cur < 0 or cur >= len(backend_ids):
                continue
            base = dom_nodes.get(backend_ids[cur])
            if not base:
                continue
            inherited = dict(base)
            inherited["backendNodeId"] = base["backendNodeId"]
            own_attrs = _attrs(attrs[node_i] if node_i < len(attrs) else [], strings)
            inherited["attributes"] = {**base.get("attributes", {}), **own_attrs}
            if node_i in input_values:
                inherited["input_value"] = _string(strings, input_values[node_i])
            dom_nodes[backend] = inherited

    vw = float(viewport.get("width") or viewport.get("clientWidth") or 0)
    vh = float(viewport.get("height") or viewport.get("clientHeight") or 0)
    ax_by_backend = {}
    ax_total = 0
    for tree in ax_trees:
        if tree.get("detached"):
            detached_frames += 1
            continue
        for node in tree.get("nodes", []) or []:
            ax_total += 1
            backend = node.get("backendDOMNodeId")
            if backend is not None and not node.get("ignored"):
                ax_by_backend[backend] = node

    pruned = {"occluded": 0, "offscreen": 0, "disabled": 0, "hidden": 0,
              "unnamed": 0, "overflow": 0, "missing_geometry": 0}
    candidates = []
    for backend, ax in ax_by_backend.items():
        role = str(_value(ax.get("role"))).lower()
        if role not in INTERACTIVE_ROLES:
            continue
        dom_rec = dom_nodes.get(backend)
        if not dom_rec:
            pruned["missing_geometry"] += 1
            continue
        if role == "option" and dom_rec.get("node_name", "").upper() == "SELECT":
            # Closed native selects expose AX option descendants at the host's
            # box; they are not independently reachable until the popup opens.
            continue
        props = _ax_properties(ax)
        attrs = dom_rec["attributes"]
        disabled = bool(props.get("disabled")) or attrs.get("disabled") is not None or attrs.get("aria-disabled") == "true"
        if disabled:
            pruned["disabled"] += 1
            continue
        style = dom_rec["style"]
        try:
            opacity = float(style.get("opacity") or 1)
        except ValueError:
            opacity = 1
        if style.get("display") == "none" or style.get("visibility") in {"hidden", "collapse"} or opacity <= .05:
            pruned["hidden"] += 1
            continue
        if vw and vh and (dom_rec["x"] + dom_rec["w"] <= 0 or dom_rec["y"] + dom_rec["h"] <= 0
                          or dom_rec["x"] >= vw or dom_rec["y"] >= vh):
            pruned["offscreen"] += 1
            continue
        name = str(_value(ax.get("name"))).replace("\n", " ").strip()[:160]
        if not name and role not in {"textbox", "searchbox", "combobox", "checkbox", "radio", "slider", "spinbutton"}:
            pruned["unnamed"] += 1
            continue
        rec = {key: value for key, value in dom_rec.items()
               if key not in {"attributes", "style", "ancestors", "input_value", "input_checked"}}
        checked = props.get("checked")
        if isinstance(checked, str):
            checked = checked.lower() == "true"
        input_type = attrs.get("type")
        observed_value = (_value(ax.get("value"), None)
                          if dom_rec["node_name"].upper() == "SELECT"
                          else dom_rec.get("input_value", _value(ax.get("value"), None)))
        if input_type == "password":
            observed_value = None
        rec.update({"role": role, "name": name,
                    "value": observed_value,
                    "checked": dom_rec.get("input_checked", checked), "focused": bool(props.get("focused")),
                    "href": attrs.get("href"), "input_type": input_type,
                    "visible": True, "occluded": False})
        if "options" in dom_rec:
            rec["options"] = dom_rec["options"]
        rec["ancestors"] = dom_rec.get("ancestors", ())
        rec["identity"] = _identity(rec)
        candidates.append(rec)

    visible_boxes = sorted(all_boxes, key=lambda r: r.get("paint_order", 0))
    kept = []
    for rec in sorted(candidates, key=lambda r: (r.get("paint_order", 0), r["identity"])):
        if _covered(rec, visible_boxes):
            rec["occluded"] = True
            pruned["occluded"] += 1
            continue
        rec["index"] = len(kept)
        rec.pop("ancestors", None)
        kept.append(rec)
        if len(kept) >= max_elements:
            pruned["overflow"] = max(0, len(candidates) - len(kept))
            break

    completeness = {
        "dom_documents": len(documents), "ax_frames": len(ax_trees) - detached_frames,
        "detached_frames": detached_frames, "ax_nodes": ax_total,
        "fused_nodes": len(candidates), "complete": bool(documents and ax_trees and not detached_frames),
    }
    return {"elements": kept, "pruned": pruned, "completeness": completeness,
            "viewport": {"width": vw, "height": vh,
                         "scrollX": scroll_x, "scrollY": scroll_y,
                         "deviceScaleFactor": float(viewport.get("deviceScaleFactor") or 1),
                         "layoutScale": device_scale}}


def _walk_frames(frame: dict) -> list[str]:
    out = []
    if frame.get("frame", {}).get("id"):
        out.append(frame["frame"]["id"])
    for child in frame.get("childFrames", []) or []:
        out.extend(_walk_frames(child))
    return out


@dataclass
class ProtocolBudget:
    calls: dict[str, int] = field(default_factory=dict)

    def count(self, method: str):
        self.calls[method] = self.calls.get(method, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.calls.values())


class PersistentCDPSnapshotter:
    def __init__(self, session, *, max_elements=60):
        self.session = session
        self.max_elements = max_elements
        self.budget = ProtocolBudget()

    async def send(self, method: str, params: dict | None = None):
        self.budget.count(method)
        return await self.session.send(method, params or {})

    async def capture(self) -> dict:
        frame_tree = await self.send("Page.getFrameTree")
        frame_ids = _walk_frames(frame_tree.get("frameTree", {}))
        dom_task = self.send("DOMSnapshot.captureSnapshot", {
            "computedStyles": COMPUTED_STYLES, "includePaintOrder": True,
            "includeDOMRects": True, "includeBlendedBackgroundColors": False,
        })
        viewport_task = self.send("Runtime.evaluate", {
            "expression": "({width:innerWidth,height:innerHeight,scrollX:scrollX,scrollY:scrollY,deviceScaleFactor:devicePixelRatio,cssContentWidth:document.documentElement?document.documentElement.scrollWidth:0,url:location.href,title:document.title,focus:document.activeElement?document.activeElement.tagName:''})",
            "returnByValue": True,
        })
        ax_tasks = [self.send("Accessibility.getFullAXTree", {"frameId": fid}) for fid in frame_ids]
        results = await asyncio.gather(dom_task, viewport_task, *ax_tasks, return_exceptions=True)
        dom = results[0] if isinstance(results[0], dict) else {"documents": [], "strings": []}
        viewport_raw = results[1] if isinstance(results[1], dict) else {}
        viewport = viewport_raw.get("result", {}).get("value", {})
        ax_trees = []
        for frame_id, result in zip(frame_ids, results[2:]):
            if isinstance(result, Exception):
                ax_trees.append({"frameId": frame_id, "nodes": [], "detached": True})
            else:
                ax_trees.append({"frameId": frame_id, "nodes": result.get("nodes", [])})
        fused = fuse_snapshot(dom, ax_trees, viewport, max_elements=self.max_elements)
        root_backend = None
        try:
            root_backend = dom["documents"][0]["nodes"]["backendNodeId"][0]
        except (KeyError, IndexError, TypeError):
            pass
        fused.update({"url": viewport.get("url", ""), "title": viewport.get("title", ""),
                      "focus": viewport.get("focus", ""), "protocol_calls": self.budget.total,
                      "root_backend_node_id": root_backend,
                      "protocol_call_detail": dict(self.budget.calls)})
        return fused
