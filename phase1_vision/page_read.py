"""
Read visible page text from screenshots (OCR).

Browsers expose tabs/address bar via AX, not page body. This gives the planner
a general 'read' channel for any task that needs information from the screen.
"""

import re
from typing import Any

import numpy as np
from PIL import Image
from loguru import logger

from phase1_vision.parse_screen import _get_paddle_ocr

# Browser chrome (tabs, toolbar) lives in the top ~90 logical px
BROWSER_CHROME_MAX_Y = 90


def read_visible_text(image: Image.Image, min_y: int = 0) -> str:
    """OCR all readable text on a screenshot. Cheap compared to full OmniParser."""
    ocr = _get_paddle_ocr()
    if ocr is None:
        return ""

    try:
        img_np = np.array(image.convert("RGB"))
        raw = ocr.ocr(img_np)
        if not raw:
            return ""

        r = raw[0]
        texts = r.get("rec_texts", [])
        scores = r.get("rec_scores", [])
        polys = r.get("rec_polys", [])

        lines: list[tuple[int, str]] = []
        for txt, score, poly in zip(texts, scores, polys):
            if score < 0.45 or not txt or not str(txt).strip():
                continue
            ys = poly[:, 1].tolist()
            y_top = int(min(ys))
            if y_top < min_y:
                continue
            lines.append((y_top, str(txt).strip()))

        lines.sort(key=lambda t: t[0])
        return "\n".join(t for _, t in lines)
    except Exception as e:
        logger.debug(f"Page OCR failed: {e}")
        return ""


def is_browser_chrome_only(elements: list[dict], app_info: dict | None) -> bool:
    """True when AX sees mostly browser UI, not page content."""
    if not (app_info or {}).get("is_browser"):
        return False
    if not elements:
        return True
    below = sum(1 for e in elements if e.get("y", 0) > BROWSER_CHROME_MAX_Y)
    return below < 3


def filter_browser_content_elements(elements: list[dict]) -> list[dict]:
    """Drop tab bar / address bar elements from actionable list."""
    out = []
    for e in elements:
        y = e.get("y", 0)
        label = (e.get("label") or "").lower()
        if y <= BROWSER_CHROME_MAX_Y:
            continue
        if "address and search bar" in label:
            continue
        if label.endswith(" - google search") and y < 120:
            continue
        if "inactive tab" in label or label.startswith("new tab"):
            continue
        out.append(e)
    return out


def _task_needs_price(task: str) -> bool:
    t = (task or "").lower()
    return any(w in t for w in ("price", "cost", "how much", "cheap", "$"))


def summarize_readable_facts(page_text: str, task: str = "") -> str | None:
    """
    Pull factual snippets from OCR text — prices, numbers, key lines.
    Works for any lookup task, not hardcoded to one domain.
    """
    if not page_text or len(page_text.strip()) < 20:
        return None

    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    prices = re.findall(r"\$[\d,]+(?:\.\d{2})?", page_text)
    price_lines = [ln for ln in lines if "$" in ln]

    if prices:
        unique = list(dict.fromkeys(prices))[:10]
        if price_lines:
            return "Prices on screen: " + " | ".join(price_lines[:5])
        return "Prices on screen: " + ", ".join(unique)

    # Price questions need a real $ amount — autocomplete suggestions are not answers
    if _task_needs_price(task):
        return None

    task_words = [w for w in re.findall(r"[a-z0-9]+", task.lower()) if len(w) >= 4]
    if task_words:
        hits = []
        for ln in lines:
            ln_l = ln.lower()
            if sum(1 for w in task_words if w in ln_l) >= 2:
                hits.append(ln)
        if hits:
            return "Relevant lines: " + " | ".join(hits[:5])

    return None


_TASK_STOPWORDS = {
    "find", "search", "look", "lookup", "price", "cost", "much", "what",
    "with", "from", "that", "this", "have", "show", "tell", "about", "please",
    "get", "give", "info", "information", "current", "latest", "online",
}


def task_keywords(task: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (task or "").lower())
    return [w for w in words if len(w) >= 4 and w not in _TASK_STOPWORDS]


def page_relevant_to_task(page_text: str, task: str) -> bool:
    """True when visible text looks related to the task (not a blank NTP/home)."""
    keys = task_keywords(task)
    if not keys:
        return True
    page_l = (page_text or "").lower()
    if not page_l.strip():
        return False
    hits = sum(1 for w in keys if w in page_l)
    return hits >= max(1, (len(keys) + 2) // 3)


def search_query_from_task(task: str) -> str:
    t = (task or "").strip()
    t = re.sub(
        r"^(find|search\s+for|search|look\s+up|lookup|get|show\s+me)\s+",
        "",
        t,
        flags=re.IGNORECASE,
    )
    return t.strip() or (task or "").strip()


def has_submitted_search(action_log: list[str]) -> bool:
    """True if the agent already typed and submitted a query."""
    joined = " ".join(action_log).lower()
    return 'press("enter")' in joined or "pressed: enter" in joined


def _log_has(action_log: list[str], needle: str) -> bool:
    return needle.lower() in " ".join(action_log).lower()


def consecutive_scrolls(action_log: list[str]) -> int:
    n = 0
    for a in reversed(action_log):
        if "scroll(" in a.lower():
            n += 1
        else:
            break
    return n


def next_browser_search_action(
    task: str,
    page_text: str,
    action_log: list[str],
    *,
    is_browser: bool,
    needs_web_info: bool,
) -> str | None:
    """
    Deterministic browser search: cmd+l → type → enter.
    Once started, finish the sequence even if autocomplete OCR looks "relevant".
    """
    if not is_browser or not needs_web_info:
        return None
    if has_submitted_search(action_log):
        return None

    focused = _log_has(action_log, 'press("cmd+l")') or _log_has(action_log, "pressed: cmd+l")
    typed = _log_has(action_log, "type_text")
    started = focused or typed

    # Autocomplete after typing looks task-related — ignore until Enter is pressed
    if not started and page_relevant_to_task(page_text, task):
        return None

    if not focused:
        return 'TOOL: press("cmd+l")\nReason: Focus address bar to search — page does not match the task yet.'

    if not typed:
        q = search_query_from_task(task).replace('"', "")
        return f'TOOL: type_text("{q}")\nReason: Type the search query.'

    return 'TOOL: press("enter")\nReason: Submit the search (do not stop on suggestions).'


def try_answer_from_page(
    task: str,
    page_text: str,
    action_log: list[str],
) -> str | None:
    """
    Answer from OCR only after a real search submit (Enter).
    Typing alone leaves autocomplete suggestions — those are not results.
    """
    if not has_submitted_search(action_log):
        return None
    return summarize_readable_facts(page_text, task)
