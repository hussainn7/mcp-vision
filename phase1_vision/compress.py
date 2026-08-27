"""Rolling trajectory compression: shrink old tool outputs, keep visual anchors.

A 15-step web run otherwise accumulates every snapshot dump in the prompt.
We keep the last K tool results verbatim (the model still needs the latest
indices) and collapse older ones to one line, preserving SoM/index anchors
so a later step can still refer to `[7] link "Pricing"`.
"""

from __future__ import annotations

import re

_ANCHOR = re.compile(r"\[\d+\][^\n]{0,80}")
_SNAP_HEAD = "Interactive elements"


def summarize_tool_result(text: str, max_len: int = 180) -> str:
    raw = str(text or "")
    if raw.startswith(_SNAP_HEAD) or "[" in raw[:40]:
        anchors = _ANCHOR.findall(raw)[:8]
        if anchors:
            return "snapshot: " + "; ".join(a.strip() for a in anchors)
    line = " ".join(raw.split())
    if len(line) <= max_len:
        return line
    return line[: max_len - 16] + f"...[+{len(line) - max_len + 16} chars]"


def compress_messages(messages: list[dict], keep_last: int = 4) -> list[dict]:
    """Return a new message list with older tool results compressed.

    System + the most recent `keep_last` tool-role messages stay intact.
    Older tool results become a one-line summary. Assistant/user turns are
    left alone so tool_call_id pairing still matches.
    """
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    keep = set(tool_idxs[-keep_last:]) if keep_last else set()
    out = []
    for i, m in enumerate(messages):
        if m.get("role") != "tool" or i in keep:
            out.append(m)
            continue
        content = summarize_tool_result(m.get("content", ""))
        nm = dict(m)
        nm["content"] = content
        out.append(nm)
    return out


def demo():
    snap = (
        "Interactive elements (use the index with web_click):\n"
        '[0] link "Overview"\n'
        '[7] link "Pricing"\n'
        '[12] textbox "Search"\n'
        + ("x" * 400)
    )
    s = summarize_tool_result(snap)
    assert "snapshot:" in s and '[7] link "Pricing"' in s
    assert len(s) < len(snap)

    long = "ok " + ("word " * 80)
    assert "chars]" in summarize_tool_result(long, max_len=60)

    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": snap},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "Clicked [7]"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c3"}]},
        {"role": "tool", "tool_call_id": "c3", "content": "fresh snapshot [1] link \"Next\""},
    ]
    out = compress_messages(msgs, keep_last=2)
    assert out[0]["content"] == "sys"
    assert out[3]["content"].startswith("snapshot:")
    assert out[3]["tool_call_id"] == "c1"          # pairing preserved
    assert out[5]["content"] == "Clicked [7]"      # kept (last 2)
    assert out[7]["content"].startswith("fresh")
    # keep_last=99 is a no-op
    assert compress_messages(msgs, keep_last=99)[3]["content"] == snap
    print("ok")


if __name__ == "__main__":
    demo()
