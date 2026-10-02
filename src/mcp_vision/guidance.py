"""Semantic guidance; coordinates never stand in for target identity."""
from __future__ import annotations

import json


# Browsers disagree on roles for native inputs: older Chromium exposes a date
# field as "textbox", newer builds as "date"; a file chooser is a "button" in
# the accessibility tree but planners describe it as a field. Match by family.
_ROLE_FAMILIES = {
    'textbox': {'textbox', 'searchbox', 'date', 'datetime', 'time', 'inputtime', 'month', 'week'},
}
_FIELD_INPUT_TYPES = {'file', 'date', 'datetime-local', 'time', 'month', 'week'}


def role_matches(element, role: str) -> bool:
    if not role:
        return True
    wanted = role.casefold()
    have = str(element.get('role', '')).casefold()
    if have == wanted or have in _ROLE_FAMILIES.get(wanted, ()):
        return True
    return wanted == 'textbox' and str(element.get('input_type') or '').casefold() in _FIELD_INPUT_TYPES


def resolve_target(elements, name, role=''):
    role = {'textarea': 'textbox', 'input': 'textbox', 'select': 'combobox',
            'axbutton': 'button', 'axtextfield': 'textbox', 'axtextarea': 'textbox',
            'axcheckbox': 'checkbox', 'axpopupbutton': 'combobox'}.get(role.lower(), role)
    matches = [e for e in elements if e.get('name', '').casefold().strip() == name.casefold().strip()
               and role_matches(e, role)]
    if len(matches) != 1 or not name.strip():
        return None
    target = matches[0]
    if target.get('w', 0) <= 0 or target.get('h', 0) <= 0:
        return None
    return target


def overlay_script(index, label='Next step', duration=8000):
    """Highlight the element a snapshot tagged with ``data-agent-index`` (native driver)."""
    return ('(() => { const el = document.querySelector(\'[data-agent-index="' + str(int(index)) + '"]\');'
            ' if (!el) return false; return (' + overlay_function() + ').call(el, '
            + json.dumps(label) + ', ' + str(int(duration)) + '); })()')


def overlay_function():
    """``function(label, duration)`` run with ``this`` bound to the target element.

    The CDP runtime resolves elements by backend node id and calls this with
    ``Runtime.callFunctionOn``, so it needs no marker attribute in the page.
    """
    return '''function(LABEL_ARG, DURATION_ARG) {
      window.__mcpVisionHighlight?.();
      let target = this;
      if (!target || !target.isConnected) return false;
      const identity = {id: target.id, tag: target.tagName, name: target.getAttribute('aria-label') || target.textContent.trim()};
      const host = document.createElement('div');
      host.style.cssText = 'position:fixed;inset:0;pointer-events:none!important;z-index:2147483647';
      const shadow = host.attachShadow({mode:'closed'});
      const style = document.createElement('style');
      style.textContent = '@keyframes mcpPulse{0%,100%{transform:scale(1);opacity:.95}50%{transform:scale(1.35);opacity:.55}}';
      const ring = document.createElement('div');
      ring.style.cssText = 'position:fixed;border:3px solid #38bda4;border-radius:7px;box-shadow:0 0 0 4px #38bda430;pointer-events:none;box-sizing:border-box';
      const label = document.createElement('span');
      label.textContent = String(LABEL_ARG);
      label.style.cssText = 'position:absolute;bottom:calc(100% + 7px);left:0;background:#143b35;color:white;padding:4px 8px;border-radius:6px;font:12px system-ui;white-space:nowrap';
      const marker = document.createElement('div');
      marker.setAttribute('data-mcp-agent-marker','1');
      marker.style.cssText = 'position:fixed;width:14px;height:14px;margin:-7px 0 0 -7px;border:2px solid #fff;border-radius:50%;background:#38bda4;box-shadow:0 0 0 2px #143b35aa;pointer-events:none;animation:mcpPulse 1.1s ease-in-out infinite';
      ring.append(label); shadow.append(style, ring, marker); document.documentElement.append(host);
      let frame, ended = false;
      const clean = () => { ended = true; cancelAnimationFrame(frame); host.remove(); };
      window.__mcpVisionHighlight = clean;
      const update = () => {
        if (ended) return;
        if (!target.isConnected) {
          const matches = identity.id ? [document.getElementById(identity.id)].filter(Boolean) :
            Array.from(document.getElementsByTagName(identity.tag)).filter(el =>
              (el.getAttribute('aria-label') || el.textContent.trim()) === identity.name && identity.name);
          if (matches.length !== 1 || matches[0].tagName !== identity.tag ||
              (matches[0].getAttribute('aria-label') || matches[0].textContent.trim()) !== identity.name) { clean(); return; }
          target = matches[0];
        }
        const r = target.getBoundingClientRect();
        ring.style.left = (r.x-3)+'px'; ring.style.top = (r.y-3)+'px';
        ring.style.width = (r.width+6)+'px'; ring.style.height = (r.height+6)+'px';
        marker.style.left = (r.x + r.width/2)+'px'; marker.style.top = (r.y + r.height/2)+'px';
        label.style.bottom = r.y < 32 ? 'auto' : 'calc(100% + 7px)';
        frame = requestAnimationFrame(update);
      };
      update(); setTimeout(clean, Number(DURATION_ARG) || 8000); return true;
    }'''
