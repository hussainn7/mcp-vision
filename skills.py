"""
Skill library: successful runs become reusable playbooks.

After a run passes the judge, its trajectory is distilled into a skill —
the task, the plan, and the exact tool sequence that worked. On the next
similar task, the closest skills are injected into the prompt as a worked
example, so the model starts from a proven path instead of rediscovering it.

This is the step past failure memory: failures teach the agent what NOT to
do; skills teach it what TO do. Both live in plain JSON under memory/,
per specialist, and never leave the machine.

Retrieval is token-overlap (Jaccard) similarity. Deliberately: at <100
skills per specialist, embeddings would add a model call and a vector store
to beat a set intersection. Revisit only if the library outgrows that.
"""

import json
import re
from pathlib import Path

SKILL_DIR = Path(__file__).parent / "memory"
MAX_SKILLS = 100        # per specialist
MIN_SIM = 0.25          # below this a skill is unrelated, don't inject
DEDUP_SIM = 0.8         # above this it's the same task, update in place

_STOP = {"a", "an", "the", "to", "of", "in", "on", "for", "and", "with", "my", "me", "it"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def similarity(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _path(specialist):
    return SKILL_DIR / f"skills_{specialist}.json"


def load_skills(specialist):
    try:
        return json.loads(_path(specialist).read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _save(specialist, skills):
    SKILL_DIR.mkdir(exist_ok=True)
    _path(specialist).write_text(json.dumps(skills[-MAX_SKILLS:], indent=2, ensure_ascii=False))


def distill(events):
    """Compress a successful trajectory into a skill. Returns None when there
    is nothing worth keeping (no clean tool calls)."""
    start = events[0] if events and events[0]["type"] == "run_start" else {}
    end = next((e for e in events if e["type"] == "run_end"), {})
    steps = [
        {"tool": e.get("tool"), "args": e.get("args", {})}
        for e in events
        if e["type"] == "tool_call"
        and e.get("status") != "error"
        and not str(e.get("result", "")).startswith(("error", "ERROR"))
    ]
    if not steps or not start.get("task"):
        return None
    plan = next((e.get("plan", "") for e in events if e["type"] == "plan"), "")
    return {
        "task": str(start["task"])[:300],
        "plan": str(plan)[:500],
        "steps": steps[:12],
        "answer_hint": str(end.get("answer", ""))[:200],
        "uses": 0,
    }


def learn(specialist, events):
    """Distill and store, deduping near-identical tasks in place."""
    skill = distill(events)
    if skill is None:
        return None
    skills = load_skills(specialist)
    for i, s in enumerate(skills):
        if similarity(s["task"], skill["task"]) >= DEDUP_SIM:
            skill["uses"] = s.get("uses", 0)
            skills[i] = skill      # refresh with the latest working sequence
            _save(specialist, skills)
            return skill
    skills.append(skill)
    _save(specialist, skills)
    return skill


def retrieve(specialist, task, k=2):
    """Top-k skills similar to the task, most similar first."""
    scored = [(similarity(s["task"], task), s) for s in load_skills(specialist)]
    scored = [(sim, s) for sim, s in scored if sim >= MIN_SIM]
    scored.sort(key=lambda x: -x[0])
    hits = [s for _, s in scored[:k]]
    if hits:  # count usage so proven skills are visible in the JSON
        skills = load_skills(specialist)
        for s in skills:
            if any(s["task"] == h["task"] for h in hits):
                s["uses"] = s.get("uses", 0) + 1
        _save(specialist, skills)
    return hits


def skills_block(specialist, task, k=2):
    """Prompt block with worked examples, or "" when nothing relevant."""
    hits = retrieve(specialist, task, k)
    if not hits:
        return ""
    parts = []
    for s in hits:
        seq = " -> ".join(f"{st['tool']}({json.dumps(st['args'], ensure_ascii=False)})"
                          for st in s["steps"])
        parts.append(f'- For "{s["task"]}" this exact sequence worked:\n  {seq}')
    return "\n\nProven playbooks from similar past tasks (adapt arguments as needed):\n" + "\n".join(parts)


def demo():
    import shutil
    global SKILL_DIR
    saved, SKILL_DIR = SKILL_DIR, Path(__file__).parent / "memory_demo"
    try:
        shutil.rmtree(SKILL_DIR, ignore_errors=True)
        from trace import Tracer, load_trace
        out = SKILL_DIR / "traces"

        tr = Tracer(task="summarize the top story on hacker news", specialist="web-researcher", out_dir=out)
        tr.event("plan", plan="1. open hn 2. read 3. summarize")
        with tr.span("tool_call", tool="web_navigate", args={"url": "news.ycombinator.com"}) as s:
            s["result"] = "Navigated"
        with tr.span("tool_call", tool="web_read") as s:
            s["result"] = "Page text: ..."
        with tr.span("tool_call", tool="web_click_text", args={"text": "ghost"}) as s:
            s["result"] = "error: not found"          # failed step must be dropped
        tr.end(status="ok", answer="the top story is...")
        events = load_trace(tr.path)

        skill = learn("web-researcher", events)
        assert skill and [s["tool"] for s in skill["steps"]] == ["web_navigate", "web_read"]

        # near-identical task updates in place, not duplicates
        learn("web-researcher", events)
        assert len(load_skills("web-researcher")) == 1

        # retrieval: related task hits, unrelated task doesn't
        block = skills_block("web-researcher", "summarize today's top hacker news story")
        assert "web_navigate" in block and "Proven playbooks" in block
        assert skills_block("web-researcher", "defragment my quantum flux capacitor") == ""
        assert load_skills("web-researcher")[0]["uses"] == 1   # usage counted

        # empty trajectory -> nothing learned
        tr2 = Tracer(task="nothing", specialist="general", out_dir=out)
        tr2.end()
        assert learn("general", load_trace(tr2.path)) is None

        assert 0.99 < similarity("open the notes app", "open notes app") <= 1.0
        print("ok")
    finally:
        shutil.rmtree(SKILL_DIR, ignore_errors=True)
        SKILL_DIR = saved


if __name__ == "__main__":
    demo()
