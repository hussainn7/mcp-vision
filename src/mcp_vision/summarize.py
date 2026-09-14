"""Second-pass cleaner: turn raw page/task evidence into a short answer."""
from __future__ import annotations

import re


_SYSTEM = (
    "You clean up computer-use results for a human. "
    "Given a user query and raw page text or notes, reply with a short, clear answer only. "
    "Use bullets or a small table when comparing options. "
    "No tool talk, no JSON, no speculation beyond the evidence. "
    "If the evidence is incomplete, say what is missing in one line."
)


def _heuristic(query: str, evidence: str) -> str:
    """Works with no model. Pulls prices, times, and titles from the dump."""
    text = re.sub(r"[ \t]+", " ", evidence or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    prices = re.findall(r"(?:US)?\$\d[\d,]*(?:\.\d{2})?", text)
    times = re.findall(r"\b\d{1,2}:\d{2}(?:\+1)?\b", text)
    airlines = [a for a in ("United", "Delta", "American", "Southwest", "Frontier", "JetBlue", "Alaska", "Spirit")
                if a in text]
    title = next((ln for ln in lines if len(ln) > 8 and len(ln) < 100
                  and not ln.lower().startswith("skip to")), "")

    bits = [f"Query: {query.strip()}"]
    if title:
        bits.append(f"Page: {title}")
    if prices:
        uniq = list(dict.fromkeys(prices))[:12]
        bits.append("Prices: " + ", ".join(uniq))
    if times:
        uniq_t = list(dict.fromkeys(times))[:12]
        bits.append("Times: " + ", ".join(uniq_t))
    if airlines:
        bits.append("Airlines: " + ", ".join(dict.fromkeys(airlines)))

    # Keep a few dense lines that look like options
    options = []
    for ln in lines:
        if re.search(r"\$\d|non-?stop|\d stop|hr ", ln, re.I) and len(ln) < 160:
            options.append(ln)
        if len(options) >= 8:
            break
    if options:
        bits.append("Options:")
        bits.extend(f"- {o}" for o in options)

    if len(bits) == 1:
        preview = " ".join(lines[:8])[:600]
        bits.append(preview or "(no usable text)")
    return "\n".join(bits)


def summarize(query: str, evidence: str, *, backend: str | None = "local",
              ok: bool = True) -> str:
    """Return a clean answer. Uses a model when available; else a local heuristic."""
    evidence = (evidence or "")[:12000]
    query = (query or "").strip() or "summarize this"
    if not ok:
        return "Task did not succeed.\n" + _heuristic(query, evidence)

    use_model = backend not in {None, "", "none", "off", "heuristic"}
    if use_model:
        try:
            from backends import get_chat
            chat = get_chat(backend)
            msg = chat([
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"QUERY\n{query}\n\nEVIDENCE\n{evidence}"},
            ], tools=None)
            content = (msg.get("content") or "").strip()
            if content:
                return content
        except Exception:
            pass
    return _heuristic(query, evidence)
