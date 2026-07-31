"""
Semantic page snapshot: what the model is allowed to see and act on.

Three ideas, all deterministic — decided by the browser, never by the model:

1. ROLE + ACCESSIBLE NAME, not DOM.  A single control on a modern site is
   wrapped in layers of div soup that carry no meaning. We compute the ARIA
   role and accessible name (the same thing a screen reader announces) and
   throw the rest away. `[7] link "Pricing"` instead of 100 lines of markup.

2. OCCLUSION PRUNING.  An element can be visible, enabled, on-screen, and still
   unclickable because a sticky header or modal scrim sits on top of it —
   document.elementFromPoint() at the element's own coordinates returns the
   overlay instead. That is knowable without guessing, so we probe several
   points per element, keep the first that actually resolves to the element,
   and drop anything with no reachable point. The model never sees a trap.

3. VERIFIED CLICK POINT.  Each surviving element carries the exact (cx, cy)
   that resolved to it, so the click lands where the browser itself said the
   element is reachable.

Pure JS + pure formatting helpers, so the geometry and the formatting are
testable without a browser.
"""

import json

# Candidate controls. Broad on purpose — role/name scoring below decides what
# actually survives, rather than a hand-maintained tag list.
_CANDIDATE_SELECTOR = (
    "a,button,input,select,textarea,summary,label,"
    "[role],[onclick],[contenteditable=''],[contenteditable='true'],"
    "[tabindex]:not([tabindex='-1'])"
)

# Roles that carry no interaction meaning for an agent.
_SKIP_ROLES = "['presentation','none','separator','img','image']"

_JS_HELPERS = r"""
  const SKIP_ROLES = new Set(%(skip)s);

  const implicitRole = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return el.hasAttribute('href') ? 'link' : '';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'label') return 'label';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'hidden') return '';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'range') return 'slider';
      if (['submit','button','reset','image'].includes(t)) return 'button';
      return 'textbox';
    }
    return '';
  };

  // Accessible name, following the ARIA naming precedence closely enough to
  // match what a screen reader would announce.
  const accName = (el) => {
    const fromIds = (ids) => ids.trim().split(/\s+/)
      .map(id => { const n = document.getElementById(id); return n ? (n.innerText || n.getAttribute('aria-label') || '') : ''; })
      .join(' ');
    let n = '';
    const lb = el.getAttribute('aria-labelledby');
    if (lb) n = fromIds(lb);
    if (!n) n = el.getAttribute('aria-label') || '';
    if (!n && el.labels && el.labels.length) n = Array.from(el.labels).map(l => l.innerText || '').join(' ');
    if (!n) n = el.getAttribute('placeholder') || '';
    if (!n) n = el.getAttribute('title') || '';
    if (!n) n = el.getAttribute('alt') || '';
    if (!n) n = el.innerText || '';
    if (!n && typeof el.value === 'string') n = el.value;
    return n.replace(/\s+/g, ' ').trim().slice(0, 100);
  };

  const isDisabled = (el) =>
    el.disabled === true ||
    el.getAttribute('aria-disabled') === 'true' ||
    el.closest('[inert]') !== null;

  const isVisible = (el) => {
    if (typeof el.checkVisibility === 'function') {
      try { return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}); } catch (e) {}
    }
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };

  // THE OCCLUSION TEST. Probe points on the element; the first one where
  // elementFromPoint resolves back to this element (or a node inside it, or a
  // wrapper around it) is a real, clickable point. No reachable point means
  // something is covering it -> the element is a trap and gets pruned.
  const reachablePoint = (el, r) => {
    const ix = Math.min(12, Math.max(1, r.width  * 0.25));
    const iy = Math.min(10, Math.max(1, r.height * 0.25));
    const pts = [
      [r.left + r.width / 2, r.top + r.height / 2],
      [r.left + ix,          r.top + r.height / 2],
      [r.right - ix,         r.top + r.height / 2],
      [r.left + r.width / 2, r.top + iy],
      [r.left + r.width / 2, r.bottom - iy],
    ];
    for (const [x, y] of pts) {
      if (x < 1 || y < 1 || x > window.innerWidth - 1 || y > window.innerHeight - 1) continue;
      const hit = document.elementFromPoint(x, y);
      if (!hit) continue;
      if (hit === el || el.contains(hit) || hit.contains(el)) return [Math.round(x), Math.round(y)];
    }
    return null;
  };
""" % {"skip": _SKIP_ROLES}


# Snapshot: returns {elements: [...], pruned: {occluded, offscreen, disabled}}
SNAPSHOT_JS = r"""(maxElements) => {
  %(helpers)s

  const out = [];
  const seen = new Set();
  const pruned = {occluded: 0, offscreen: 0, disabled: 0};
  let i = 0;

  for (const el of document.querySelectorAll(%(sel)s)) {
    if (i >= maxElements) break;

    const role = (el.getAttribute('role') || implicitRole(el)).toLowerCase();
    if (!role || SKIP_ROLES.has(role)) continue;
    if (isDisabled(el)) { pruned.disabled++; continue; }
    if (!isVisible(el)) continue;

    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;

    // must intersect the viewport at all
    if (r.bottom <= 0 || r.top >= window.innerHeight ||
        r.right <= 0 || r.left >= window.innerWidth) { pruned.offscreen++; continue; }

    const name = accName(el);
    // an unnamed control is only useful if it takes input
    if (!name && !['textbox', 'combobox', 'checkbox', 'radio', 'slider'].includes(role)) continue;

    const point = reachablePoint(el, r);
    if (!point) { pruned.occluded++; continue; }

    const key = role + '|' + name;
    if (seen.has(key)) continue;
    seen.add(key);

    el.setAttribute('data-agent-index', String(i));
    out.push({
      index: i, role: role, name: name,
      cx: point[0], cy: point[1],
      x: Math.round(r.left), y: Math.round(r.top),
      w: Math.round(r.width), h: Math.round(r.height),
    });
    i++;
  }
  return {elements: out, pruned: pruned};
}""" % {"helpers": _JS_HELPERS, "sel": json.dumps(_CANDIDATE_SELECTOR)}


# Re-test one element's reachability right now (used at click time, after
# scrolling, because a scroll changes what is on top of what).
REACHABLE_JS = r"""(selector) => {
  %(helpers)s
  const el = document.querySelector(selector);
  if (!el) return null;
  if (isDisabled(el) || !isVisible(el)) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return null;
  const p = reachablePoint(el, r);
  return p ? {cx: p[0], cy: p[1], x: Math.round(r.left), y: Math.round(r.top),
              w: Math.round(r.width), h: Math.round(r.height)} : null;
}""" % {"helpers": _JS_HELPERS}


# Scroll an element to the middle of the viewport. This is the general cure for
# sticky headers/footers: they own the edges, so an element parked in the
# centre is out from under them on every site, with no per-site rules.
SCROLL_INTO_CENTER_JS = r"""(selector) => {
  const el = document.querySelector(selector);
  if (!el) return false;
  el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
  return true;
}"""


def format_elements(snapshot):
    """Render a snapshot as the compact menu the model sees.

    Role + name only. This is where the token saving happens: the model reads
    `[3] link "Pricing"`, never the markup that produced it.
    """
    if not snapshot or not snapshot.get("elements"):
        hint = ("No interactive elements are reachable here. The page may still be "
                "loading, or an overlay/cookie banner may be covering it — try "
                "web_scroll, then web_snapshot again.")
        return hint
    lines = [f'[{e["index"]}] {e["role"]} "{e["name"]}"' if e["name"] else f'[{e["index"]}] {e["role"]}'
             for e in snapshot["elements"]]
    note = ""
    pruned = snapshot.get("pruned") or {}
    if pruned.get("occluded"):
        note = (f'\n({pruned["occluded"]} element(s) hidden behind an overlay were omitted '
                f'because they cannot be clicked from here.)')
    return "Interactive elements (use the index with web_click):\n" + "\n".join(lines) + note


def crop_box(el, pad=24, min_size=96, max_size=320, viewport=None):
    """Padded, clamped bounding box around an element, for a micro-vision crop.

    Small on purpose: a tight crop is fast for a local VLM and gives it far
    less to get confused by than a full screenshot.
    """
    w = max(min_size, min(max_size, el["w"] + pad * 2))
    h = max(min_size, min(max_size, el["h"] + pad * 2))
    cx = el["x"] + el["w"] / 2
    cy = el["y"] + el["h"] / 2
    x = cx - w / 2
    y = cy - h / 2
    if viewport:
        vw, vh = viewport
        w, h = min(w, vw), min(h, vh)
        x = max(0, min(x, vw - w))
        y = max(0, min(y, vh - h))
    else:
        x, y = max(0, x), max(0, y)
    return {"x": int(x), "y": int(y), "width": int(w), "height": int(h)}


def demo():
    # formatting: role+name menu, and the occlusion note when things were pruned
    snap = {"elements": [{"index": 0, "role": "link", "name": "Overview", "cx": 5, "cy": 5,
                          "x": 0, "y": 0, "w": 10, "h": 10},
                         {"index": 1, "role": "textbox", "name": "", "cx": 9, "cy": 9,
                          "x": 4, "y": 4, "w": 10, "h": 10}],
            "pruned": {"occluded": 2, "offscreen": 0, "disabled": 1}}
    out = format_elements(snap)
    assert '[0] link "Overview"' in out
    assert "[1] textbox" in out and '[1] textbox ""' not in out  # unnamed input, no empty quotes
    assert "2 element(s) hidden behind an overlay" in out
    assert "No interactive elements are reachable" in format_elements({"elements": [], "pruned": {}})
    assert "hidden behind an overlay" not in format_elements(
        {"elements": snap["elements"], "pruned": {"occluded": 0}})

    # crop stays centred on the element and is clamped to a VLM-friendly size
    box = crop_box({"x": 500, "y": 300, "w": 40, "h": 20})
    assert box["width"] >= 96 and box["height"] >= 96
    assert box["x"] < 500 and box["y"] < 300                      # padded outward
    big = crop_box({"x": 0, "y": 0, "w": 5000, "h": 5000})
    assert big["width"] <= 320 and big["height"] <= 320           # capped for speed

    # never runs off the edge of the viewport
    edge = crop_box({"x": 0, "y": 0, "w": 10, "h": 10}, viewport=(1280, 800))
    assert edge["x"] >= 0 and edge["y"] >= 0
    far = crop_box({"x": 1270, "y": 790, "w": 10, "h": 10}, viewport=(1280, 800))
    assert far["x"] + far["width"] <= 1280 and far["y"] + far["height"] <= 800

    # tiny viewport: crop clamps to it instead of producing a negative origin
    tiny = crop_box({"x": 10, "y": 10, "w": 10, "h": 10}, viewport=(50, 40))
    assert tiny["x"] >= 0 and tiny["y"] >= 0
    assert tiny["x"] + tiny["width"] <= 50 and tiny["y"] + tiny["height"] <= 40

    # the JS carries the occlusion + naming logic the whole design rests on
    for js in (SNAPSHOT_JS, REACHABLE_JS):
        assert "elementFromPoint" in js and "accName" in js

    # The selector must be embedded as a *valid* JS string literal. Building it
    # by hand once produced `[contenteditable=""]`, whose quotes closed the
    # literal early and made the whole snapshot a syntax error — which shows up
    # only as "0 elements found", never as an error. Check the literal instead.
    at = SNAPSHOT_JS.index("querySelectorAll(") + len("querySelectorAll(")
    embedded, _ = json.JSONDecoder().raw_decode(SNAPSHOT_JS[at:])
    assert embedded == _CANDIDATE_SELECTOR
    assert _CANDIDATE_SELECTOR.count("[") == _CANDIDATE_SELECTOR.count("]")
    # balanced braces/parens in the emitted JS, the other way a template breaks
    for js in (SNAPSHOT_JS, REACHABLE_JS, SCROLL_INTO_CENTER_JS):
        assert js.count("{") == js.count("}") and js.count("(") == js.count(")")
    print("ok")


if __name__ == "__main__":
    demo()
