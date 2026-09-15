"""Semantic guidance; coordinates never stand in for target identity."""
from __future__ import annotations

import json


def resolve_target(elements, name, role=''):
    matches = [e for e in elements if e.get('name', '').casefold().strip() == name.casefold().strip()
               and (not role or e.get('role', '').casefold() == role.casefold())]
    if len(matches) != 1 or not name.strip():
        return None
    target = matches[0]
    if target.get('w', 0) <= 0 or target.get('h', 0) <= 0:
        return None
    return target


def overlay_script(index, label='Next step', duration=8000):
    return '''(() => {
      window.__mcpVisionHighlight?.();
      const target = document.querySelector('[data-agent-index="INDEX"]');
      if (!target) return false;
      const host = document.createElement('div');
      host.style.cssText = 'position:fixed;inset:0;pointer-events:none!important;z-index:2147483647';
      const shadow = host.attachShadow({mode:'closed'});
      const ring = document.createElement('div');
      ring.style.cssText = 'position:fixed;border:3px solid #38bda4;border-radius:7px;box-shadow:0 0 0 4px #38bda430;pointer-events:none;box-sizing:border-box';
      const label = document.createElement('span');
      label.textContent = LABEL;
      label.style.cssText = 'position:absolute;bottom:calc(100% + 7px);left:0;background:#143b35;color:white;padding:4px 8px;border-radius:6px;font:12px system-ui;white-space:nowrap';
      ring.append(label); shadow.append(ring); document.documentElement.append(host);
      let frame, ended = false;
      const clean = () => { ended = true; cancelAnimationFrame(frame); host.remove(); };
      window.__mcpVisionHighlight = clean;
      const update = () => {
        if (ended || !target.isConnected) { clean(); return; }
        const r = target.getBoundingClientRect();
        ring.style.left = (r.x-3)+'px'; ring.style.top = (r.y-3)+'px';
        ring.style.width = (r.width+6)+'px'; ring.style.height = (r.height+6)+'px';
        label.style.bottom = r.y < 32 ? 'auto' : 'calc(100% + 7px)';
        frame = requestAnimationFrame(update);
      };
      update(); setTimeout(clean, DURATION); return true;
    })()'''.replace('INDEX', str(int(index))).replace('LABEL', json.dumps(label)).replace('DURATION', str(int(duration)))
