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

import base64
import gc
import io
import json
import re
import sys
import time
from pathlib import Path

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
)
from phase3_orchestrator.prompts import (
    SYSTEM_PROMPT_FIGMA_TO_VSCODE,
    SYSTEM_PROMPT_GENERAL,
    VISION_DESCRIBE_PROMPT,
    TOOL_EXTRACTION_SYSTEM_PROMPT,
    build_user_message,
    format_elements_summary,
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
            "type_text": lambda text: "ERROR: type_text not implemented for Playwright - use fill?",
            "press": playwright_press_key,
            "scroll": lambda eid, direction="down", clicks=3: "ERROR: scroll not implemented for Playwright",
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


def run_agent_cycle(
    task: str,
    history: list[dict],
    system_prompt: str = SYSTEM_PROMPT_GENERAL,
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

    screenshot_img, _ = capture_screen(save=False)

    if method == "omniparser":
        console.print("[dim]Running OmniParser...[/dim]")
        annotated, elements = parse_screen(screenshot_img)
        del screenshot_img
    else:
        console.print("[dim]Drawing bounding boxes...[/dim]")
        annotated = _draw_bounding_boxes_on_screenshot(screenshot_img, elements)

    save_results(annotated, elements)

    elements_summary = format_elements_summary(elements)
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
    )

    console.print(Panel(Text(response, style="cyan"), title="Tool Decision (llama3.1)", border_style="blue"))

    # step 4: parse and execute the tool call
    action_type, content = parse_tool_call(response)

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

        updated_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": response},
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
) -> tuple[str, str]:
    """
    Two-stage vision pipeline for small VQA models like Moondream.

    Stage 1: Ask the vision model to describe the screen relative to the task.
    Stage 2: Ask the planning/text model to convert that into a structured tool call.

    Args:
        image_b64: base64-encoded PNG of the annotated screenshot
        task: the current task description
        elements_summary: text summary of detected elements
        history: previous conversation turns

    Returns:
        (vision_description, tool_call_response)
    """
    # Stage 1: Vision model describes the screen
    vision_prompt = VISION_DESCRIBE_PROMPT.format(
        task=task,
        elements_summary=elements_summary,
    )

    logger.debug(f"Stage 1: Asking vision model ({cfg.ollama_model}) to describe screen...")
    vision_response = ollama.chat(
        model=cfg.ollama_model,
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
            "num_predict": 100,  # brief description only, keeps latency low
            "keep_alive": cfg.ollama_keep_alive,
        },
    )
    description = vision_response["message"]["content"].strip()
    console.print(Panel(
        Text(description, style="dim cyan"),
        title="Vision Description (Moondream)",
        border_style="dim blue",
    ))

    # Extract ALL previous actions from history so Stage 2 can see the full sequence
    action_log = []
    for entry in history:
        if entry.get("role") == "assistant":
            content = entry.get("content", "")
            for line in content.strip().splitlines():
                if line.strip().upper().startswith("TOOL:"):
                    action_log.append(line.strip())
                    break

    if action_log:
        actions_text = "\n".join(f"  {i+1}. {a}" for i, a in enumerate(action_log))
    else:
        actions_text = "  (none yet)"

    # Stage 2: Text model converts description to a structured tool call
    extraction_message = f"""Task: {task}

Actions completed so far:
{actions_text}

What is the next tool call?"""

    logger.debug(f"Stage 2: Asking planning model ({cfg.planning_model}) for tool call...")
    tool_raw = ollama.chat(
        model=cfg.planning_model,
        messages=[
            {"role": "system", "content": TOOL_EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": extraction_message},
        ],
        options={
            "temperature": 0.1,
            "num_predict": 32,  # tool call is one short line
            "keep_alive": cfg.ollama_keep_alive,
        },
    )
    tool_response = tool_raw["message"]["content"].strip()

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


def run_with_plan(
    task: str,
    max_cycles: int | None = None,
    system_prompt: str = SYSTEM_PROMPT_GENERAL,
) -> None:
    """
    Run the agent with persistent plan management and AX-tree verification.

    Uses a fast text model for planning, vision model only for grounding.
    Verifies each step with cheap AX-tree diff instead of full re-perception.
    """
    max_cycles = max_cycles or cfg.max_cycles

    # Initialize PlanManager with AX-tree verifier
    plan_manager = PlanManager(ax_verifier_func=get_accessibility_elements)

    # Start overlay with plan
    overlay = Overlay()
    overlay.start(task=task)

    console.print(Panel(
        f"[bold green]Starting screen agent (plan mode)[/bold green]\n\nTask: {task}",
        border_style="green",
    ))

    # Step 1: Decompose task into plan using fast text model
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
    retry = False

    try:
        while True:
            cycle += 1
            if max_cycles and cycle > max_cycles:
                console.print(f"[yellow]Reached max cycles ({max_cycles}), stopping.[/yellow]")
                break

            console.print(f"\n[bold]--- Cycle {cycle} ---[/bold]")
            console.print(plan_manager.get_plan_summary())
            overlay.set_status(f"Cycle {cycle}")

            # Check if plan is complete
            if plan_manager.current_plan.is_complete:
                console.print(Panel("[green]All plan steps completed![/green]", border_style="green"))
                break

            step = plan_manager.current_plan.current_step
            step_idx = plan_manager.current_plan.current_step_index
            if not step:
                break

            # Update overlay with current step
            overlay.update_step(step_idx, OverlayStepStatus.IN_PROGRESS, current=step_idx)

            # Get perception data (cheap - only when needed)
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

            if method == "omniparser":
                console.print("[dim]Running OmniParser...[/dim]")
                annotated, elements = parse_screen(screenshot_img)
                del screenshot_img
            else:
                console.print("[dim]Drawing bounding boxes...[/dim]")
                annotated = _draw_bounding_boxes_on_screenshot(screenshot_img, elements)

            save_results(annotated, elements)

            elements_summary = format_elements_summary(elements)
            console.print(f"[green]Found {len(elements)} elements[/green]")

            # Build user message with current step context
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
            )

            console.print(Panel(Text(response, style="cyan"), title="Tool Decision (llama3.1)", border_style="blue"))

            # Parse and execute tool call
            action_type, content = parse_tool_call(response)

            if action_type == "done":
                console.print(Panel(f"[green]Task complete: {content}[/green]", border_style="green"))
                overlay.add_action("DONE", content, success=True)
                overlay.mark_done(content)
                break

            elif action_type == "tool":
                console.print(f"[yellow]Executing: {content}[/yellow]")
                overlay.add_action(content, "", success=True)  # result will be filled after

                # Execute with PlanManager verification
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

                # Update action result in overlay
                overlay.add_action(content, result, success=success)

                updated_history = history + [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": response},
                ]
                history = updated_history

                # Advance or retry
                should_continue, is_retry = plan_manager.advance_or_retry()
                if not should_continue:
                    if plan_manager.current_plan.is_complete:
                        console.print(Panel("[green]Plan complete![/green]", border_style="green"))
                    else:
                        console.print(Panel("[red]Plan failed - max retries exceeded[/red]", border_style="red"))
                    break

            else:
                logger.warning("Model response didn't parse, skipping this cycle")
                # Still check if we should retry/advance
                should_continue, _ = plan_manager.advance_or_retry()
                if not should_continue:
                    break

            # wait between cycles
            console.print(f"[dim]Waiting {cfg.loop_delay}s...[/dim]")
            time.sleep(cfg.loop_delay)

    except AccessibilityPermissionError as e:
        console.print(f"[red]{str(e)}[/red]")
        console.print("[yellow]Agent terminated due to missing accessibility permissions.[/yellow]")
        overlay.mark_done(f"Error: {e}")
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        overlay.mark_done("Interrupted")
    finally:
        overlay.stop()


# Agent logic functions (run in background thread) -------------------------------------------------

def _run_agent_simple(overlay: Overlay, task: str, max_cycles: int | None, system_prompt: str):
    """Simple agent loop without plan."""
    max_cycles = max_cycles or cfg.max_cycles
    history: list[dict] = []
    cycle = 0

    overlay.set_status("Starting...")

    try:
        while True:
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
            )

            if is_done:
                overlay.add_action("DONE", result, success=True)
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

            if method == "omniparser":
                console.print("[dim]Running OmniParser...[/dim]")
                annotated, elements = parse_screen(screenshot_img)
                del screenshot_img
            else:
                console.print("[dim]Drawing bounding boxes...[/dim]")
                annotated = _draw_bounding_boxes_on_screenshot(screenshot_img, elements)

            save_results(annotated, elements)

            elements_summary = format_elements_summary(elements)
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
            )

            console.print(Panel(Text(response, style="cyan"), title="Tool Decision (llama3.1)", border_style="blue"))

            action_type, content = parse_tool_call(response)

            if action_type == "done":
                console.print(Panel(f"[green]Task complete: {content}[/green]", border_style="green"))
                overlay.add_action("DONE", content, success=True)
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
                    {"role": "assistant", "content": response},
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