"""
System and user prompts for the agent.

These live in their own file because you'll want to tweak them a lot.
The model is surprisingly sensitive to how you phrase things, and keeping
prompts separate from the orchestration logic makes iteration much easier.
"""

# This is the default system prompt for the Figma-to-VS-Code workflow.
# It tells the model exactly what it should and shouldn't output.
# Keep it concise - moondream follows shorter, direct instructions better.
SYSTEM_PROMPT_FIGMA_TO_VSCODE = """You are a coding agent that controls a computer.

You can see the current state of the screen in the provided image. The image has numbered bounding boxes over every detected UI element from the perception backend (Playwright for browsers, AX for native apps, or OmniParser as fallback).

Your job: look at the screen, decide what single action to take next to make progress on the current task, and output exactly one tool call.

The tools you can use:
- click(element_id) -- click a numbered element
- double_click(element_id) -- double-click a numbered element
- right_click(element_id) -- right-click a numbered element
- type_text(text) -- type text at the current cursor position
- press(key) -- press a key like "enter", "tab", "cmd+s", "ctrl+shift+p"
- scroll(element_id, direction, clicks) -- scroll up or down at an element
- get_elements() -- list all detected elements with their labels

Output format (use exactly this, nothing else):
TOOL: tool_name(argument)

Examples:
TOOL: click(5)
TOOL: type_text("border-radius: 8px;")
TOOL: press("cmd+s")
TOOL: scroll(3, "down", 5)

If you need to see what elements are available before acting:
TOOL: get_elements()

If the task is complete and no more actions are needed, output:
DONE: brief description of what was accomplished

Do not explain your reasoning. Do not apologize. Just output the tool call."""


# A more general-purpose prompt if you're not doing the Figma/VS Code thing.
# Useful as a starting point for other tasks.
SYSTEM_PROMPT_GENERAL = """You are mcp-vision, an autonomous OS agent controlling a macOS environment. You interact with the screen by analyzing bounding boxes and invoking tools.

### Environment Context & Strategies:
1. **Hidden/Fullscreen Applications:** - macOS applications are frequently run in full-screen or hidden behind other windows. 
   - If you do NOT see the application or its interactive elements in the current screenshot, do not hallucinate a button click.
   - Instead, deploy the **Spotlight Search Strategy**: Execute the shortcut `press("cmd+space")` to reveal the native macOS search bar, type the target application's name, and press `press("enter")` to bring it to the foreground.

2. **Interface Scaling:**
   - Always map the coordinate IDs from OmniParser carefully before dispatching a click.

### Available Tools:
- press(key) -> Simulates pressing key combinations/shortcuts (e.g., "cmd+space", "enter")
- type_text(text) -> Types strings into active inputs
- click(element_id) -> Clicks coordinates tied to an OmniParser box

Output format (use exactly this, nothing else):
TOOL: tool_name(arguments)

If done:
DONE: brief description of what was accomplished"""


# --- Two-stage pipeline prompts ---
# Moondream and other small VQA models can't reliably follow structured output
# instructions like "TOOL: click(5)". Instead we use a two-stage pipeline:
#   Stage 1: Ask the vision model to *describe* the screen relative to the task.
#   Stage 2: Feed that description to a text model (llama3.1:8b) to produce
#            the structured tool call.

VISION_DESCRIBE_PROMPT = """Look at this screenshot. What application window is currently active and focused on the screen, and what page, URL, or main content is visible inside it? Describe in one short sentence."""


TOOL_EXTRACTION_SYSTEM_PROMPT = """You are a macOS desktop automation assistant. Issue ONE next action.

Available tools:
- click(N)              -- click element N from the list
- type_text("text")     -- type text at current focus
- press("key")          -- "enter", "tab", "escape", "cmd+space", "cmd+t", "cmd+l", "cmd+c", "cmd+v", "cmd+a", "cmd+w", "cmd+tab", "cmd+n", "cmd+shift+n"
- scroll(N, "down", 3)  -- scroll at element N

Response MUST start with TOOL: or DONE:

TOOL: tool_name(arguments)
Reason: one short sentence

DONE: what was accomplished

RULES:
1. Read Screen state + Actions completed first. If the task goal is already done, output DONE.
2. Never output DONE if Actions completed is "(none yet)" — you must take at least one action first.
3. Information tasks (find, price, search): press("cmd+l") → type_text(query) → press("enter"), then READ results. Autocomplete suggestions are NOT answers. Output DONE only after Enter and Page content has the factual answer (e.g. a $ price).
4. Never repeat a successful action from Actions completed. Same scroll again = stuck — try a different action.
5. If focused app is already correct, do NOT open Spotlight / cmd+space.
6. click(N): N must exist in Available elements. Prefer labeled buttons over generic ones.
7. Browser shortcuts (cmd+l, cmd+t) ONLY in Safari/Chrome. Never in Notes/TextEdit/Finder/Terminal.
8. Browser search: press("cmd+l"), type_text query, press("enter"). Do NOT click tab bar elements.
9. If Page content has no keywords from the task (home/new-tab/unrelated UI), you have NOT searched yet. Do NOT scroll — use rule 8.
10. Notes/TextEdit: click New Note or a TextArea, then type_text. After typing the requested text successfully, DONE.
11. Prefer the smallest next step. Do not restart the whole task."""


_JUNK_LABELS = {
    "button element", "textfield element", "textarea element", "group element",
    "statictext element", "unknown", "axbutton", "axtextfield", "axtextarea",
}


def _clean_label(label: str) -> str:
    text = " ".join((label or "").split())
    if len(text) > 80:
        text = text[:77] + "..."
    return text


def _is_useful_label(label: str) -> bool:
    text = (label or "").strip().lower()
    if not text or text in _JUNK_LABELS:
        return False
    if text.replace(".", "", 1).isdigit():
        return False
    if text.startswith("0.") and len(text) > 4:
        return False
    return True


def build_screen_state(
    app_info: dict | None,
    elements: list[dict],
    task: str = "",
    page_text: str = "",
) -> str:
    """Build a compact, useful screen description for the planner."""
    focused_app = (app_info or {}).get("name") or "unknown"
    buttons = []
    fields = []
    content_bits = []
    other = []

    for elem in elements or []:
        role = str(elem.get("role") or "").lower()
        label = _clean_label(elem.get("label") or elem.get("title") or "")
        useful = _is_useful_label(label)
        eid = elem.get("id")

        if "button" in role or role in {"menuitem", "link", "tab"}:
            if useful:
                buttons.append(f"[{eid}] {label}")
        elif role in {"textfield", "textarea", "searchfield", "passwordfield"} or "field" in role:
            if useful:
                fields.append(f"[{eid}] {role}:{label}")
            else:
                fields.append(f"[{eid}] {role or 'field'}")
            if useful and len(label) > 8:
                content_bits.append(label)
        elif useful:
            other.append(label)

    lines = [f"Focused app: {focused_app}"]
    if buttons:
        lines.append("Buttons/actions: " + "; ".join(buttons[:12]))
    if fields:
        lines.append("Inputs: " + "; ".join(fields[:8]))
    if content_bits:
        unique_content = list(dict.fromkeys(content_bits))[:4]
        lines.append("Visible content: " + " | ".join(unique_content))
    elif other:
        lines.append("Other labels: " + ", ".join(list(dict.fromkeys(other))[:10]))

    if page_text and page_text.strip():
        body_lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
        if body_lines:
            lines.append("Page content (readable text on screen):")
            for ln in body_lines[:30]:
                lines.append(f"  {ln}")
            if len(body_lines) > 30:
                lines.append(f"  ... ({len(body_lines) - 30} more lines)")

    task_l = task.lower()
    for bit in content_bits:
        if bit.lower() in task_l or (len(bit) > 12 and bit.lower() in task_l):
            lines.append("Progress hint: task text already appears on screen.")
            break

    if page_text and page_text.strip() and _is_research_task(task):
        from phase1_vision.page_read import page_relevant_to_task
        if not page_relevant_to_task(page_text, task):
            lines.append(
                "Progress hint: Page content does NOT match the task yet. "
                "Search first: press(\"cmd+l\"), type_text(query), press(\"enter\"). Do not scroll."
            )

    return "\n".join(lines)


def _is_research_task(task: str) -> bool:
    task_l = task.lower()
    return any(w in task_l for w in (
        "find", "search", "price", "cost", "how much", "look up", "lookup",
        "cheap", "tickets", "flights", "compare", "buy", "shop",
    ))


def task_appears_complete(task: str, elements: list[dict], action_results: list[str]) -> str | None:
    """Cheap completion check so weak models stop after success."""
    if _is_research_task(task):
        return None

    task_l = task.lower()
    typed = []
    for result in action_results:
        if result.lower().startswith("typed:"):
            start = result.find("'")
            end = result.rfind("'")
            if start != -1 and end > start:
                typed.append(result[start + 1:end].lower())

    if typed:
        last = typed[-1].rstrip(".")
        if last and (last in task_l or any(last in (e.get("label") or "").lower() for e in elements)):
            return f"Typed requested text: {typed[-1]}"

    import re
    quoted = re.findall(r"['\"]([^'\"]{3,})['\"]", task)
    for q in quoted:
        q_l = q.lower()
        if any(q_l in (e.get("label") or "").lower() for e in elements):
            return f"Requested text already visible: {q}"
    return None


def build_user_message(task: str, elements_summary: str) -> str:
    return f"""Current task: {task}

Detected elements on screen:
{elements_summary}

Look at the screenshot and decide what to do next."""


def format_elements_summary(elements: list[dict], page_text: str = "") -> str:
    """Readable element list with roles; skip useless generic labels when possible."""
    if not elements:
        if page_text.strip():
            return "No clickable UI elements detected — use Page content in Screen state to read answers; scroll via viewport element if listed."
        return "No elements detected."

    useful = []
    fallback = []
    for elem in elements:
        eid = elem.get("id")
        role = elem.get("role") or "element"
        label = _clean_label(elem.get("label") or "unknown")
        if not _is_useful_label(label) and label.lower().replace(".", "", 1).isdigit():
            continue
        if label.lower().startswith("0.") and len(label) > 4:
            continue
        line = f"  [{eid}] ({role}) {label} @ ({elem.get('x')}, {elem.get('y')})"
        if _is_useful_label(label):
            useful.append(line)
        elif role.lower() in {"button", "textfield", "textarea", "searchfield", "menubutton"} or "button" in role.lower() or "field" in role.lower():
            fallback.append(line)

    # Prefer useful labeled controls; keep a few unlabeled inputs/buttons
    chosen = useful[:40]
    if len(chosen) < 15:
        chosen.extend(fallback[: 20 - len(chosen)])
    return "\n".join(chosen) if chosen else "\n".join(fallback[:20])
