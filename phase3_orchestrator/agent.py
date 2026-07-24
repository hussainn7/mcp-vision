"""
The main agent orchestrator.

This is the part that ties Phase 1 and Phase 2 together. It runs in a loop:
  1. Take a screenshot with mss
  2. Run OmniParser to get bounding boxes (then drop it from memory)
  3. Send the annotated screenshot + element list to Moondream in Ollama
  4. Parse the model's response to extract the tool call
  5. Execute the tool call
  6. Wait a beat, then repeat

The memory management here is intentional. OmniParser and the LLM don't run
at the same time. The script takes the screenshot, gets the bounding boxes,
explicitly frees OmniParser from memory, then hits the Ollama API. Ollama
handles its own model lifecycle as a separate process.
"""

import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import base64
import gc
import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional, Callable, Any

# For drawing bounding boxes on screenshots
from PIL import ImageDraw, ImageFont

import ollama
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg
from phase1_vision.capture import capture_screen
from phase1_vision.parse_screen import parse_screen, save_results
from phase1_vision.page_read import (
    BROWSER_CHROME_MAX_Y,
    consecutive_scrolls,
    filter_browser_content_elements,
    has_submitted_search,
    is_browser_chrome_only,
    next_browser_search_action,
    page_relevant_to_task,
    read_visible_text,
    search_query_from_task,
    try_answer_from_page,
)
from phase1_vision.perception import get_perception_data, AccessibilityPermissionError, get_perception_method_name
from phase1_vision.accessibility import get_accessibility_elements
from phase2_mcp.tools import (
    click_element,
    get_screen_elements,
    press_key as pyautogui_press_key,
    right_click_element,
    scroll_at_element,
    type_code,
)
from phase2_mcp.playwright_tools import (
    click_element_by_role,
    click_element_by_text,
    press_key as playwright_press_key,
    get_page_text,
    scroll as playwright_scroll,
    type_text as playwright_type_text,
)
from phase3_orchestrator.prompts import (
    SYSTEM_PROMPT_FIGMA_TO_VSCODE,
    SYSTEM_PROMPT_GENERAL,
    VISION_DESCRIBE_PROMPT,
    TOOL_EXTRACTION_SYSTEM_PROMPT,
    build_user_message,
    build_screen_state,
    format_elements_summary,
    task_appears_complete,
    _is_research_task,
)
from phase3_orchestrator.plan import PlanManager, decompose_task_with_llm, StepStatus
from phase3_orchestrator.overlay import Overlay, PlanStep as OverlayPlanStep, StepStatus as OverlayStepStatus

console = Console()

# Global to store the perception method from the last cycle
_last_perception_method = None


def _draw_bounding_boxes_on_screenshot(screenshot_img, elements):
    """Draw numbered boxes on a screenshot from AX or Playwright element data."""
    from PIL import ImageDraw, ImageFont

    annotated = screenshot_img.copy()
    draw = ImageDraw.Draw(annotated)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except IOError:
        font = ImageFont.load_default()

    colors = {
        "button": "red",
        "link": "blue",
        "textbox": "green",
        "textarea": "green",
        "checkbox": "purple",
        "radiobutton": "purple",
        "combobox": "orange",
        "menuitem": "brown",
        "tab": "pink",
        "slider": "yellow",
        "scrollbar": "gray",
    }

    for elem in elements:
        box = elem.get("box", [0, 0, 0, 0])
        if len(box) != 4:
            continue
        x1, y1, x2, y2 = box

        elem_id = elem.get("id", 0)
        role = elem.get("role", "unknown")
        color = colors.get(role, "white")

        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)

        label_text = str(elem_id)
        bbox = draw.textbbox((0, 0), label_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        draw.rectangle([x1, y1, x1 + text_width + 4, y1 + text_height + 4], fill=color)
        draw.text((x1 + 2, y1 + 2), label_text, fill="white", font=font)

    return annotated


# Apps we've already forced to the front this process (avoid doing it every cycle)
_brought_front: set[str] = set()


def image_to_base64(img) -> str:
    """Convert a PIL Image to a base64 string that Ollama's API expects."""
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def call_ollama(
    image_b64: str,
    user_message: str,
    system_prompt: str,
    history: list[dict],
) -> str:
    """
    Send the screenshot and message to Moondream via Ollama.

    Args:
        image_b64: base64-encoded PNG of the annotated screenshot
        user_message: the user turn message (task + element summary)
        system_prompt: the system instructions
        history: previous turns to give the model some context

    Returns:
        the model's raw text response
    """
    messages = [
        {"role": "system", "content": system_prompt},
        *history[-6:],  # keep the last 6 turns for context but don't blow memory
        {
            "role": "user",
            "content": user_message,
            "images": [image_b64],
        },
    ]

    logger.debug(f"Sending request to Ollama ({cfg.ollama_model})...")
    response = ollama.chat(
        model=cfg.ollama_model,
        messages=messages,
        options={
            "temperature": 0.1,  # low temp for more deterministic tool calls
            "num_predict": 128,  # tool calls are short, no need for more tokens
            "keep_alive": cfg.ollama_keep_alive,
        },
    )

    return response["message"]["content"].strip()


def parse_tool_call(response: str) -> tuple[str | None, str | None]:
    """
    Extract the tool call from the model's response.

    The model is instructed to output "TOOL: tool_name(arguments)" or
    "DONE: description". This function parses that out.

    Returns:
        (action_type, content) where action_type is "tool", "done", or None
        content is the tool call string or done description
    """
    response = response.strip()
    lines = [line.strip() for line in response.splitlines()]

    # 1. Look for DONE signal
    for line in lines:
        done_match = re.match(r"^[\*\_]*DONE[\*\_]*:\s*(.+)$", line, re.IGNORECASE)
        if done_match:
            content = done_match.group(1).strip("*_ ")
            return "done", content

    # 2. Look for TOOL signal
    tool_idx = -1
    tool_name = None
    for i, line in enumerate(lines):
        tool_match = re.match(r"^[\*\_]*TOOL[\*\_]*:\s*(.+)$", line, re.IGNORECASE)
        if tool_match:
            tool_name = tool_match.group(1).strip("*_ ")
            if "->" in tool_name:
                tool_name = tool_name.split("->", 1)[0].strip()
            tool_idx = i
            break

    if tool_name is None:
        logger.warning(f"Model output didn't match expected format: {response[:100]}")
        return None, None

    # If it is not a complete call (ends with ')') or we have arguments block, look for arguments in subsequent lines
    normalized_tool = re.sub(r"\(.*\)", "", tool_name).strip()

    # Look for arguments in the lines following the TOOL line
    arg_val = None
    for line in lines[tool_idx + 1:]:
        # Match lines like: **ARGUMENTS:** 2, ARGUMENTS: enter, * key: "cmd+space", key: "cmd+space"
        arg_match = re.match(r"^[\*\_]*(ARGUMENTS?|key|text)[\*\_]*:\s*(.+)$", line, re.IGNORECASE)
        if arg_match:
            arg_val = arg_match.group(2).strip("*_ ")
            break
        # Also match lines starting with bullet points like: * key: "cmd+space"
        bullet_match = re.match(r"^\*\s*(key|text):\s*(.+)$", line, re.IGNORECASE)
        if bullet_match:
            arg_val = bullet_match.group(2).strip("*_ ")
            break

    if arg_val is not None:
        # If the argument is already quoted, leave it, otherwise quote it if it's a string and not a number
        if arg_val.startswith(('"', "'")) and arg_val.endswith(('"', "'")):
            pass
        elif arg_val.isdigit():
            pass
        else:
            arg_val = f'"{arg_val}"'

        return "tool", f"{normalized_tool}({arg_val})"

    # Fallback to returning the tool_name if no arguments block is found
    return "tool", tool_name


def execute_tool_call(tool_call: str) -> str:
    """
    Parse and execute a tool call string like "click(5)" or "type_text('hello')".

    This is a simple eval-based dispatcher. It's intentionally restrictive -
    only the defined tool functions are available in the execution namespace.
    Don't expose anything dangerous here.

    Args:
        tool_call: the tool call string from the model

    Returns:
        the result string from the tool function
    """
    # Choose tool implementation based on the last perception method
    global _last_perception_method
    if _last_perception_method == "playwright":
        # Helper function to click an element by its ID using Playwright
        def _playwright_click_by_element(element_id: int) -> str:
            from phase2_mcp.tools import _find_element
            elem = _find_element(element_id)
            role = elem.get("role", "button")
            name = elem.get("label", "")
            # If name is empty, we might try to use title or something else
            if not name:
                name = elem.get("title", "")
            # Try to click by role and name
            result = click_element_by_role(role, name if name else None)
            if result.startswith("ERROR"):
                # Fallback to click by text if we have a label
                if elem.get("label"):
                    result = click_element_by_text(elem["label"])
            return result

        # Helper function to double-click an element by its ID using Playwright
        def _playwright_double_click_by_element(element_id: int) -> str:
            result1 = _playwright_click_by_element(element_id)
            if result1.startswith("ERROR"):
                return result1
            # Wait a bit between clicks
            time.sleep(0.1)
            result2 = _playwright_click_by_element(element_id)
            return result2

        allowed_tools = {
            "click": _playwright_click_by_element,
            "double_click": _playwright_double_click_by_element,
            "right_click": lambda eid: "ERROR: right-click not implemented for Playwright",
            "type_text": playwright_type_text,
            "press": playwright_press_key,
            "scroll": lambda _eid, direction="down", clicks=3: playwright_scroll(direction, clicks),
            "get_elements": get_screen_elements,  # This still works because it reads the JSON
        }
    else:
        # Use pyautogui tools for accessibility or OmniParser
        allowed_tools = {
            "click": lambda eid: click_element(eid, double=False),
            "double_click": lambda eid: click_element(eid, double=True),
            "right_click": right_click_element,
            "type_text": type_code,
            "press": pyautogui_press_key,
            "scroll": scroll_at_element,
            "get_elements": get_screen_elements,
        }

    try:
        # extract function name and arguments
        # matches things like: click(5), type_text("hello world"), press("cmd+s")
        func_match = re.match(r"(\w+)\((.*)\)$", tool_call.strip(), re.DOTALL)
        if not func_match:
            return f"ERROR: could not parse tool call: {tool_call}"

        func_name = func_match.group(1)
        if func_name == "type":
            func_name = "type_text"
        args_str = func_match.group(2).strip()

        # Clean up keyword arguments if the model outputs them (e.g. click(element_id=6) -> click(6))
        if args_str:
            args_str = re.sub(r"\b\w+=\s*", "", args_str)

        if func_name not in allowed_tools:
            return f"ERROR: unknown tool '{func_name}'. Available: {list(allowed_tools.keys())}"

        # parse arguments safely using json-style parsing
        # wrapping in a list lets json.loads handle the comma-separated args
        if args_str:
            try:
                args = json.loads(f"[{args_str}]")
            except json.JSONDecodeError:
                # fallback: try to handle simple unquoted strings
                # this handles cases like press(enter) -> press("enter")
                args_str_fixed = re.sub(r"(?<!['\"])(\b\w[\w\+\-]*\b)(?!['\"])", r'"\1"', args_str)
                try:
                    args = json.loads(f"[{args_str_fixed}]")
                except json.JSONDecodeError:
                    return f"ERROR: could not parse args: {args_str}"
            result = allowed_tools[func_name](*args)
        else:
            result = allowed_tools[func_name]()

        return str(result) if result is not None else "OK"

    except Exception as e:
        logger.error(f"Tool execution failed: {e}")
        return f"ERROR: {e}"


def _enrich_browser_perception(
    screenshot_img,
    elements: list[dict],
    app_info: dict | None,
    task: str,
) -> tuple[list[dict], str]:
    """When AX only sees browser chrome, OCR the page and fix click targets."""
    page_text = ""
    if screenshot_img is None:
        return elements, page_text

    is_browser = (app_info or {}).get("is_browser")
    if not is_browser:
        return elements, page_text

    chrome_only = is_browser_chrome_only(elements, app_info)
    if _is_research_task(task) or chrome_only:
        console.print("[dim]Reading visible page text...[/dim]")
        page_text = read_visible_text(screenshot_img, min_y=BROWSER_CHROME_MAX_Y)
        if page_text:
            logger.debug(f"OCR captured {len(page_text)} chars of page text")

    filtered = filter_browser_content_elements(elements)
    if filtered:
        elements = filtered
    elif chrome_only:
        w, h = screenshot_img.size
        elements = [{
            "id": 1,
            "label": "page viewport (scroll here)",
            "role": "group",
            "x": w // 2,
            "y": h // 2,
            "interactivity": True,
        }]

    for i, e in enumerate(elements, 1):
        e["id"] = i

    return elements, page_text


def run_agent_cycle(
    task: str,
    history: list[dict],
    system_prompt: str = SYSTEM_PROMPT_GENERAL,
    overlay: Optional[Overlay] = None,
) -> tuple[str, bool, list[dict]]:
    """
    Run one full cycle of the agent loop.

    Takes a screenshot, runs OmniParser, calls Ollama, executes the tool.
    Returns the updated history and whether the task is done.

    Args:
        task: the task description
        history: conversation history so far
        system_prompt: the system prompt text to guide the model

    Returns:
        (result_message, is_done, updated_history)
    """
    console.print("[dim]Getting perception data...[/dim]")
    try:
        perception_data = get_perception_data()
    except AccessibilityPermissionError as e:
        console.print(f"[red]{str(e)}[/red]")
        raise

    method = perception_data["method"]
    elements = perception_data["elements"]
    app_info = perception_data["app_info"]

    global _last_perception_method
    _last_perception_method = method

    console.print(f"[green]Using {get_perception_method_name(method)} perception[/green]")
    if app_info["name"]:
        console.print(f"[dim]Focused app: {app_info['name']} ({app_info['bundle_id']})[/dim]")

    # --- Hard app-switch guard ---
    # If a different app has focus but the task clearly names a target app,
    # switch to it immediately without burning a full LLM cycle.
    KNOWN_APPS = {
        "safari": ("com.apple.Safari", "Safari"),
        "chrome": ("com.google.Chrome", "Google Chrome"),
        "firefox": ("org.mozilla.firefox", "Firefox"),
        "finder": ("com.apple.finder", "Finder"),
        "notes": ("com.apple.Notes", "Notes"),
        "messages": ("com.apple.MobileSMS", "Messages"),
        "mail": ("com.apple.mail", "Mail"),
        "terminal": ("com.apple.Terminal", "Terminal"),
        "calendar": ("com.apple.iCal", "Calendar"),
        "maps": ("com.apple.Maps", "Maps"),
        "music": ("com.apple.Music", "Music"),
        "photos": ("com.apple.Photos", "Photos"),
        "reminders": ("com.apple.reminders", "Reminders"),
        "textedit": ("com.apple.TextEdit", "TextEdit"),
    }
    # Browser / web-research tasks → open Chrome even if task doesn't say "chrome"
    BROWSER_KEYWORDS = {
        "gmail", "google", "browser", "url", "http", "email", "website", "web", "flight",
        "price", "cost", "how much", "lookup", "look up", "search for", "find",
        "tickets", "flights", "cheap", "compare", "buy", "shop",
    }

    focused_bundle = (app_info.get("bundle_id") or "").lower()
    task_lower = task.lower()

    # Find the target app from the task
    target_app = None
    for keyword, (bundle_id, app_name) in KNOWN_APPS.items():
        if keyword in task_lower:
            target_app = (bundle_id, app_name)
            break

    # Fallback: browser/research keywords → Chrome (user's default browser workflow)
    if not target_app and any(kw in task_lower for kw in BROWSER_KEYWORDS):
        target_app = ("com.google.Chrome", "Google Chrome")

    if target_app:
        target_name = target_app[1]
        target_key = target_app[0].lower()
        wrong = focused_bundle != target_key
        # First time this run, always pull it onto the current Space even if AX
        # already says it's focused (common when Notes is on another desktop).
        if wrong or target_key not in _brought_front:
            import subprocess
            import time as _time

            if wrong:
                console.print(f"[yellow]⚠ Wrong app focused — switching to {target_name}...[/yellow]")
            else:
                console.print(f"[dim]Bringing {target_name} to front...[/dim]")

            try:
                subprocess.run(["open", "-a", target_name], check=False)
                script = (
                    f'tell application "{target_name}" to reopen\n'
                    f'tell application "{target_name}" to activate\n'
                    f'delay 0.3\n'
                    f'tell application "System Events" to set frontmost of '
                    f'process "{target_name}" to true'
                )
                subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
            except Exception:
                import pyautogui as _pag
                _pag.hotkey("command", "space")
                _time.sleep(0.4)
                _pag.write(target_name, interval=0.05)
                _time.sleep(0.3)
                _pag.press("enter")
            _time.sleep(1.2)

            from phase1_vision.app_detector import get_frontmost_app
            check = get_frontmost_app()
            if (check.get("bundle_id") or "").lower() != target_key:
                console.print(f"[yellow]Still not frontmost — forcing {target_name} again...[/yellow]")
                subprocess.run(["open", "-a", target_name], check=False)
                _time.sleep(1.0)

            _brought_front.add(target_key)
            console.print(f"[green]{target_name} should be frontmost. Re-scanning...[/green]")
            perception_data = get_perception_data()
            method = perception_data["method"]
            elements = perception_data["elements"]
            app_info = perception_data["app_info"]
            _last_perception_method = method
            console.print(f"[green]Using {get_perception_method_name(method)} perception[/green]")
            if app_info["name"]:
                console.print(f"[dim]Focused app: {app_info['name']} ({app_info['bundle_id']})[/dim]")

    screenshot_img, _ = capture_screen(save=False)
    page_text = ""

    # --- Overlay mask and filter guard ---
    overlay_geom = None
    if overlay:
        overlay_geom = overlay.get_geometry()
        if overlay_geom:
            scale = cfg.display_scale_factor
            ox, oy, ow, oh = overlay_geom
            ox_shot = ox / scale
            oy_shot = oy / scale
            ow_shot = ow / scale
            oh_shot = oh / scale
            
            pad = 10
            ox_pad = max(0, ox_shot - pad)
            oy_pad = max(0, oy_shot - pad)
            ow_pad = ow_shot + 2 * pad
            oh_pad = oh_shot + 2 * pad
            
            logger.debug(f"Overlay mask: tkinter=({ox},{oy},{ow},{oh}) -> screenshot=({ox_pad:.0f},{oy_pad:.0f},{ow_pad:.0f},{oh_pad:.0f}) img={screenshot_img.size}")
            
            # Blank out overlay region on screenshot so OmniParser/VQA doesn't see it
            from PIL import ImageDraw as _ImageDraw
            draw = _ImageDraw.Draw(screenshot_img)
            draw.rectangle([ox_pad, oy_pad, ox_pad + ow_pad, oy_pad + oh_pad], fill="#444444")
            
            # Filter out accessibility elements inside overlay region
            elements = [
                e for e in elements
                if not (ox_pad <= e.get("x", 0) <= ox_pad + ow_pad and oy_pad <= e.get("y", 0) <= oy_pad + oh_pad)
            ]

    if method == "omniparser":
        console.print("[dim]Running OmniParser...[/dim]")
        annotated, elements = parse_screen(screenshot_img)
        # Re-filter elements detected inside overlay region by OmniParser
        if overlay_geom:
            elements = [
                e for e in elements
                if not (ox_pad <= e.get("x", 0) <= ox_pad + ow_pad and oy_pad <= e.get("y", 0) <= oy_pad + oh_pad)
            ]
        del screenshot_img
        screenshot_img = None
    else:
        elements, page_text = _enrich_browser_perception(
            screenshot_img, elements, app_info, task,
        )
        console.print("[dim]Drawing bounding boxes...[/dim]")
        annotated = _draw_bounding_boxes_on_screenshot(screenshot_img, elements)

    # Label-based overlay filtering: remove any elements whose text matches
    # the overlay UI (task title, "Agent Overlay", cycle labels, etc.)
    # This is a safety net in case coordinate masking doesn't fully cover it.
    OVERLAY_POISON_PATTERNS = (
        "agent overlay", "cycle ", "time action", "time ",
    )
    task_lower = task.lower()
    pre_filter_count = len(elements)
    elements = [
        e for e in elements
        if not (
            e.get("label", "").lower().strip() == task_lower.strip()
            or any(e.get("label", "").lower().startswith(p) for p in OVERLAY_POISON_PATTERNS)
        )
    ]
    if len(elements) < pre_filter_count:
        logger.debug(f"Label filter removed {pre_filter_count - len(elements)} overlay elements")

    save_results(annotated, elements)

    elements_summary = format_elements_summary(elements, page_text)
    console.print(f"[green]Found {len(elements)} elements[/green]")

    image_b64 = image_to_base64(annotated)
    del annotated
    if method != "omniparser":
        del screenshot_img
    gc.collect()

    user_message = build_user_message(task, elements_summary)

    console.print("[dim]Running vision pipeline (Moondream -> llama3.1)...[/dim]")
    description, response = call_vision_pipeline(
        image_b64=image_b64,
        task=task,
        elements_summary=elements_summary,
        history=history,
        app_info=app_info,
        elements=elements,
        page_text=page_text,
    )

    console.print(Panel(Text(response, style="cyan"), title="Tool Decision (llama3.1)", border_style="blue"))

    # step 4: parse and execute the tool call
    action_type, content = parse_tool_call(response)

    # Reject / rewrite early DONE on research tasks before search is submitted
    if action_type == "done" and _is_research_task(task):
        prior = []
        for entry in history:
            if entry.get("role") != "assistant":
                continue
            for line in entry.get("content", "").splitlines():
                s = line.strip()
                if s.upper().startswith("TOOL:"):
                    prior.append(s.split("->", 1)[0].replace("TOOL:", "", 1).strip())
        if not prior:
            logger.warning("Rejected premature DONE on research task with no actions yet")
            return "PREMATURE_DONE", False, history
        if not has_submitted_search(prior):
            console.print("[yellow]Rejected early DONE — search not submitted yet, pressing Enter[/yellow]")
            action_type, content = "tool", 'press("enter")'

    if action_type == "done":
        console.print(Panel(f"[green]Task complete: {content}[/green]", border_style="green"))
        updated_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response},
        ]
        return content, True, updated_history

    elif action_type == "tool":
        console.print(f"[yellow]Executing: {content}[/yellow]")
        result = execute_tool_call(content)
        console.print(f"[dim]Result: {result}[/dim]")

        # Give SERP / page time to render after submit
        if "enter" in content.lower() and "press" in content.lower():
            console.print("[dim]Waiting for page load...[/dim]")
            time.sleep(2.0)

        updated_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": f"{response}\nResult: {result}"},
        ]
        return result, False, updated_history

    else:
        # model didn't follow the format - log it and continue
        logger.warning("Model response didn't parse, skipping this cycle")
        return "PARSE_ERROR", False, history


def call_ollama_text(
    user_message: str,
    system_prompt: str,
    history: list[dict],
    model: str | None = None,
) -> str:
    """Call a text-only model for planning/task decomposition."""
    messages = [
        {"role": "system", "content": system_prompt},
        *history[-6:],
        {"role": "user", "content": user_message},
    ]
    model_name = model or cfg.planning_model
    logger.debug(f"Sending request to Ollama ({model_name})...")
    response = ollama.chat(
        model=model_name,
        messages=messages,
        think=False,
        options={
            "temperature": 0.1,
            "num_predict": 512,
            "keep_alive": cfg.ollama_keep_alive,
        },
    )
    return response["message"]["content"].strip()


def call_vision_pipeline(
    image_b64: str,
    task: str,
    elements_summary: str,
    history: list[dict],
    app_info: dict | None = None,
    elements: list[dict] | None = None,
    page_text: str = "",
) -> tuple[str, str]:
    """
    Two-stage vision pipeline.

    Stage 1: Build a structured screen description from AX/DOM elements + app info.
             Falls back to Moondream only when there are no useful labels.
    Stage 2: Ask Llama 3.1 to convert that into a structured tool call.
    """
    focused_app = (app_info or {}).get("name") or "unknown"
    elements = elements or []

    # Collect prior actions for completion checks / action log
    action_log = []
    action_results = []
    for entry in history:
        if entry.get("role") != "assistant":
            continue
        content = entry.get("content", "")
        tool_line = None
        result_line = None
        for line in content.strip().splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("TOOL:"):
                # strip any echoed "-> result" the model may have copied
                raw = stripped.split("->", 1)[0].strip()
                tool_line = raw[5:].strip() if raw.upper().startswith("TOOL:") else raw
            elif stripped.lower().startswith("result:"):
                result_line = stripped[len("Result:"):].strip()
                action_results.append(result_line)
        if tool_line:
            action_log.append(tool_line)
    action_log = action_log[-8:]

    page_answer = try_answer_from_page(task, page_text, action_log)
    if page_answer:
        description = build_screen_state(app_info, elements, task, page_text)
        console.print(Panel(
            Text(description, style="dim cyan"),
            title="Screen State",
            border_style="dim blue",
        ))
        return description, f"DONE: {page_answer}"

    done_reason = task_appears_complete(task, elements, action_results)
    if done_reason:
        description = build_screen_state(app_info, elements, task, page_text)
        console.print(Panel(
            Text(description, style="dim cyan"),
            title="Screen State",
            border_style="dim blue",
        ))
        return description, f"DONE: {done_reason}"

    description = build_screen_state(app_info, elements, task, page_text)

    # Don't trust the planner to search: if page ≠ task and no query submitted, force it.
    forced = next_browser_search_action(
        task,
        page_text,
        action_log,
        is_browser=bool((app_info or {}).get("is_browser")),
        needs_web_info=_is_research_task(task),
    )
    if forced:
        console.print(Panel(
            Text(description, style="dim cyan"),
            title="Screen State",
            border_style="dim blue",
        ))
        console.print("[yellow]Page doesn't match task yet — forcing search step[/yellow]")
        return description, forced
    useful = any(
        line.startswith(("Buttons/actions:", "Inputs:", "Visible content:", "Other labels:"))
        for line in description.splitlines()
    )
    if useful:
        logger.debug("Stage 1: Using structured element screen description.")
    else:
        logger.debug(f"Stage 1: No useful labels, falling back to vision model ({cfg.vision_model})...")
        vision_prompt = VISION_DESCRIBE_PROMPT.format(
            task=task,
            elements_summary=elements_summary,
        )
        vision_response = ollama.chat(
            model=cfg.vision_model,
            messages=[
                *history[-4:],
                {
                    "role": "user",
                    "content": vision_prompt,
                    "images": [image_b64],
                },
            ],
            options={
                "temperature": 0.1,
                "num_predict": 80,
                "keep_alive": cfg.ollama_keep_alive,
            },
        )
        description = (
            f"Focused app: {focused_app}. "
            f"Vision: {vision_response['message']['content'].strip()}"
        )

    console.print(Panel(
        Text(description, style="dim cyan"),
        title="Screen State",
        border_style="dim blue",
    ))

    if action_log:
        actions_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(action_log))
    else:
        actions_text = "  (none yet)"

    stuck = consecutive_scrolls(action_log) >= 2
    stuck_note = ""
    if stuck:
        stuck_note = (
            "\nWARNING: You already scrolled multiple times. Page is unchanged or still "
            "unrelated. Do NOT scroll again — try press(\"cmd+l\") + type_text + enter, "
            "or read Page content and DONE if the answer is there.\n"
        )

    elements_str = (
        f"\nAvailable elements on screen (ONLY use IDs from this list):\n{elements_summary}\n"
        if elements_summary else ""
    )
    extraction_message = f"""Task: {task}
{description}
{elements_str}
Actions completed so far (most recent last):
{actions_text}
{stuck_note}
What is the ONE next tool call? If the task is already complete, output DONE."""

    logger.debug(f"Stage 2: Asking planning model ({cfg.planning_model}) for tool call...")
    tool_raw = ollama.chat(
        model=cfg.planning_model,
        messages=[
            {"role": "system", "content": TOOL_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": extraction_message},
        ],
        think=False,
        options={
            "temperature": 0.1,
            "num_predict": 128,
            "keep_alive": cfg.ollama_keep_alive,
        },
    )
    tool_response = tool_raw["message"]["content"].strip()

    # Hard reject scroll loops when the page still doesn't match the task
    if stuck and "scroll(" in tool_response.lower() and not page_relevant_to_task(page_text, task):
        q = search_query_from_task(task).replace('"', "")
        console.print("[yellow]Rejecting scroll loop — forcing address-bar search[/yellow]")
        tool_response = (
            'TOOL: press("cmd+l")\n'
            f'Reason: Stop scrolling; search for "{q}" instead.'
        )

    return description, tool_response


PLANNING_SYSTEM_PROMPT = """You are a task planner. Decompose the user's high-level task into a sequence of specific, atomic UI actions.

Output a JSON array of steps. Each step:
- description: what this step accomplishes (human-readable)
- expected_element: label of element that should appear/change after this step (for verification)
- expected_role: the AX role (button, menuitem, textbox, link, combobox, etc.)
- expected_state: optional dict of expected state changes, e.g., {"enabled": true, "focused": true}
- action: the tool call to execute (click, type_text, press, scroll, double_click, right_click)

Example:
[
  {"description": "Click File menu", "expected_element": "File", "expected_role": "menuitem", "action": "click(1)"},
  {"description": "Select New from dropdown", "expected_element": "New", "expected_role": "menuitem", "action": "click(2)"}
]

Only output the JSON array. No explanation."""


# Note: The agent runs in plan mode via run_with_plan() defined below, which uses the background thread wrapper _run_agent_plan to keep the Tkinter UI responsive on macOS.


# Agent logic functions (run in background thread) -------------------------------------------------

def _run_agent_simple(overlay: Overlay, task: str, max_cycles: int | None, system_prompt: str):
    """Simple agent loop without plan."""
    max_cycles = max_cycles or cfg.max_cycles
    history: list[dict] = []
    cycle = 0

    overlay.set_status("Starting...")

    try:
        while True:
            if not overlay._running:
                console.print("[yellow]Overlay window closed, stopping simple agent loop.[/yellow]")
                break
            cycle += 1
            if max_cycles and cycle > max_cycles:
                console.print(f"[yellow]Reached max cycles ({max_cycles}), stopping.[/yellow]")
                break

            console.print(f"\n[bold]--- Cycle {cycle} ---[/bold]")
            overlay.set_status(f"Cycle {cycle}")

            result, is_done, history = run_agent_cycle(
                task,
                history,
                system_prompt=system_prompt,
                overlay=overlay,
            )

            if is_done:
                overlay.add_action("DONE", result, success=True)
                overlay.set_answer(result)
            else:
                overlay.add_action(f"Cycle {cycle}", result, success=not result.startswith("ERROR"))

            if is_done:
                overlay.mark_done(result)
                break

            console.print(f"[dim]Waiting {cfg.loop_delay}s...[/dim]")
            time.sleep(cfg.loop_delay)

    except AccessibilityPermissionError as e:
        console.print(f"[red]{str(e)}[/red]")
        console.print("[yellow]Agent terminated due to missing accessibility permissions.[/yellow]")
        overlay.mark_done(f"Error: {e}")
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        overlay.mark_done("Interrupted")
    except Exception as e:
        logger.error(f"Agent error: {e}")
        overlay.mark_done(f"Error: {e}")


def _run_agent_plan(overlay: Overlay, task: str, max_cycles: int | None, system_prompt: str):
    """Agent loop with persistent plan and AX-tree verification."""
    max_cycles = max_cycles or cfg.max_cycles

    plan_manager = PlanManager(ax_verifier_func=get_accessibility_elements)

    console.print(Panel(
        f"[bold green]Starting screen agent (plan mode)[/bold green]\n\nTask: {task}",
        border_style="green",
    ))

    console.print("[dim]Decomposing task with planning model...[/dim]")
    try:
        plan_steps_data = decompose_task_with_llm(task, model=cfg.planning_model)
        if not plan_steps_data:
            raise ValueError("Empty plan")
    except Exception as e:
        logger.warning(f"Planning model failed, using single-step fallback: {e}")
        plan_steps_data = [{"description": task, "expected_element": None, "expected_role": None, "action": None}]

    plan_manager.create_plan(task, plan_steps_data)
    console.print(plan_manager.get_plan_summary())

    # Send plan to overlay
    overlay_steps = [
        OverlayPlanStep(
            description=s.get("description", ""),
            expected_element=s.get("expected_element"),
            expected_role=s.get("expected_role"),
        )
        for s in plan_steps_data
    ]
    overlay.update_plan(overlay_steps)

    history: list[dict] = []
    cycle = 0

    try:
        while True:
            if not overlay._running:
                console.print("[yellow]Overlay window closed, stopping plan agent loop.[/yellow]")
                break
            cycle += 1
            if max_cycles and cycle > max_cycles:
                console.print(f"[yellow]Reached max cycles ({max_cycles}), stopping.[/yellow]")
                break

            console.print(f"\n[bold]--- Cycle {cycle} ---[/bold]")
            console.print(plan_manager.get_plan_summary())
            overlay.set_status(f"Cycle {cycle}")

            if plan_manager.current_plan.is_complete:
                console.print(Panel("[green]All plan steps completed![/green]", border_style="green"))
                break

            step = plan_manager.current_plan.current_step
            step_idx = plan_manager.current_plan.current_step_index
            if not step:
                break

            overlay.update_step(step_idx, OverlayStepStatus.IN_PROGRESS, current=step_idx)

            console.print("[dim]Getting perception data...[/dim]")
            try:
                perception_data = get_perception_data()
            except AccessibilityPermissionError as e:
                console.print(f"[red]{str(e)}[/red]")
                raise

            method = perception_data["method"]
            elements = perception_data["elements"]
            app_info = perception_data["app_info"]

            global _last_perception_method
            _last_perception_method = method

            console.print(f"[green]Using {get_perception_method_name(method)} perception[/green]")
            if app_info["name"]:
                console.print(f"[dim]Focused app: {app_info['name']} ({app_info['bundle_id']})[/dim]")

            screenshot_img, _ = capture_screen(save=False)
            page_text = ""

            # --- Overlay mask and filter guard ---
            overlay_geom = overlay.get_geometry()
            if overlay_geom:
                scale = cfg.display_scale_factor
                ox, oy, ow, oh = overlay_geom
                ox_shot = ox / scale
                oy_shot = oy / scale
                ow_shot = ow / scale
                oh_shot = oh / scale
                
                pad = 10
                ox_pad = max(0, ox_shot - pad)
                oy_pad = max(0, oy_shot - pad)
                ow_pad = ow_shot + 2 * pad
                oh_pad = oh_shot + 2 * pad
                
                logger.debug(f"Overlay mask: tkinter=({ox},{oy},{ow},{oh}) -> screenshot=({ox_pad:.0f},{oy_pad:.0f},{ow_pad:.0f},{oh_pad:.0f}) img={screenshot_img.size}")
                
                # Blank out overlay region on screenshot so OmniParser/VQA doesn't see it
                from PIL import ImageDraw as _ImageDraw
                draw = _ImageDraw.Draw(screenshot_img)
                draw.rectangle([ox_pad, oy_pad, ox_pad + ow_pad, oy_pad + oh_pad], fill="#444444")
                
                # Filter out accessibility elements inside overlay region
                elements = [
                    e for e in elements
                    if not (ox_pad <= e.get("x", 0) <= ox_pad + ow_pad and oy_pad <= e.get("y", 0) <= oy_pad + oh_pad)
                ]

            if method == "omniparser":
                console.print("[dim]Running OmniParser...[/dim]")
                annotated, elements = parse_screen(screenshot_img)
                # Re-filter elements detected inside overlay region by OmniParser
                if overlay_geom:
                    elements = [
                        e for e in elements
                        if not (ox_pad <= e.get("x", 0) <= ox_pad + ow_pad and oy_pad <= e.get("y", 0) <= oy_pad + oh_pad)
                    ]
                del screenshot_img
                screenshot_img = None
            else:
                elements, page_text = _enrich_browser_perception(
                    screenshot_img, elements, app_info, task,
                )
                console.print("[dim]Drawing bounding boxes...[/dim]")
                annotated = _draw_bounding_boxes_on_screenshot(screenshot_img, elements)

            # Label-based overlay filtering (safety net)
            OVERLAY_POISON_PATTERNS = (
                "agent overlay", "cycle ", "time action", "time ",
            )
            task_lower = task.lower()
            pre_filter_count = len(elements)
            elements = [
                e for e in elements
                if not (
                    e.get("label", "").lower().strip() == task_lower.strip()
                    or any(e.get("label", "").lower().startswith(p) for p in OVERLAY_POISON_PATTERNS)
                )
            ]
            if len(elements) < pre_filter_count:
                logger.debug(f"Label filter removed {pre_filter_count - len(elements)} overlay elements")

            save_results(annotated, elements)

            elements_summary = format_elements_summary(elements, page_text)
            console.print(f"[green]Found {len(elements)} elements[/green]")

            step_context = f"Current step: {step.description}\n"
            if step.action:
                step_context += f"Suggested action: {step.action}\n"
            user_message = build_user_message(task, elements_summary) + "\n\n" + step_context

            image_b64 = image_to_base64(annotated)
            del annotated
            if method != "omniparser":
                del screenshot_img
            gc.collect()

            step_task = f"{task}\n\n{step_context}"
            console.print("[dim]Running vision pipeline (Moondream -> llama3.1)...[/dim]")
            description, response = call_vision_pipeline(
                image_b64=image_b64,
                task=step_task,
                elements_summary=elements_summary,
                history=history,
                app_info=app_info,
                elements=elements,
                page_text=page_text,
            )

            console.print(Panel(Text(response, style="cyan"), title="Tool Decision (llama3.1)", border_style="blue"))

            action_type, content = parse_tool_call(response)

            if action_type == "done":
                console.print(Panel(f"[green]Task complete: {content}[/green]", border_style="green"))
                overlay.add_action("DONE", content, success=True)
                overlay.set_answer(content)
                overlay.mark_done(content)
                break

            elif action_type == "tool":
                console.print(f"[yellow]Executing: {content}[/yellow]")
                overlay.add_action(content, "", success=True)

                def execute_action(action_str: str) -> str:
                    return execute_tool_call(action_str)

                result, success, verified = plan_manager.execute_and_verify(content, execute_action)
                console.print(f"[dim]Result: {result}[/dim]")
                if verified:
                    console.print("[green]Step verified via AX-tree[/green]")
                    overlay.update_step(step_idx, OverlayStepStatus.COMPLETED, result=result)
                else:
                    console.print("[yellow]Step verification failed[/yellow]")
                    overlay.update_step(step_idx, OverlayStepStatus.VERIFICATION_FAILED, result=result)

                overlay.add_action(content, result, success=success)

                updated_history = history + [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": f"{response}\nResult: {result}"},
                ]
                history = updated_history

                should_continue, _ = plan_manager.advance_or_retry()
                if not should_continue:
                    if plan_manager.current_plan.is_complete:
                        console.print(Panel("[green]Plan complete![/green]", border_style="green"))
                    else:
                        console.print(Panel("[red]Plan failed - max retries exceeded[/red]", border_style="red"))
                    break

            else:
                logger.warning("Model response didn't parse, skipping this cycle")
                should_continue, _ = plan_manager.advance_or_retry()
                if not should_continue:
                    break

            console.print(f"[dim]Waiting {cfg.loop_delay}s...[/dim]")
            time.sleep(cfg.loop_delay)

    except AccessibilityPermissionError as e:
        console.print(f"[red]{str(e)}[/red]")
        console.print("[yellow]Agent terminated due to missing accessibility permissions.[/yellow]")
        overlay.mark_done(f"Error: {e}")
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        overlay.mark_done("Interrupted")
    except Exception as e:
        logger.error(f"Agent error: {e}")
        overlay.mark_done(f"Error: {e}")


# CLI entry point ------------------------------------------------------------

def run(
    task: str,
    max_cycles: int | None = None,
    system_prompt: str = SYSTEM_PROMPT_GENERAL,
) -> None:
    """Run simple agent with overlay."""
    overlay = Overlay()
    overlay.run(task=task, agent_fn=lambda o: _run_agent_simple(o, task, max_cycles, system_prompt))


def run_with_plan(
    task: str,
    max_cycles: int | None = None,
    system_prompt: str = SYSTEM_PROMPT_GENERAL,
) -> None:
    """Run plan-mode agent with overlay."""
    overlay = Overlay()
    overlay.run(task=task, agent_fn=lambda o: _run_agent_plan(o, task, max_cycles, system_prompt))


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Screen agent - describe what you want it to do",
    )
    parser.add_argument(
        "task",
        nargs="?",
        default="Look at the screen and describe what you see.",
        help="what you want the agent to do",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="stop after this many cycles (default: run until done)",
    )
    parser.add_argument(
        "--workflow",
        choices=["general", "figma"],
        default="general",
        help="which system prompt workflow to use (default: general)",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="use persistent plan mode with AX-tree verification (uses split models)",
    )
    args = parser.parse_args()

    prompt = SYSTEM_PROMPT_GENERAL if args.workflow == "general" else SYSTEM_PROMPT_FIGMA_TO_VSCODE
    if args.plan:
        run_with_plan(task=args.task, max_cycles=args.max_cycles, system_prompt=prompt)
    else:
        run(task=args.task, max_cycles=args.max_cycles, system_prompt=prompt)


if __name__ == "__main__":
    main()