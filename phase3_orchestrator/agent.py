"""
The main agent orchestrator.

This is the part that ties Phase 1 and Phase 2 together. It runs in a loop:
  1. Take a screenshot with mss
  2. Run OmniParser to get bounding boxes (then drop it from memory)
  3. Send the annotated screenshot + element list to Llama 3.2 Vision in Ollama
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

import ollama
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import cfg
from phase1_vision.capture import capture_screen
from phase1_vision.parse_screen import parse_screen, save_results
from phase2_mcp.tools import (
    click_element,
    get_screen_elements,
    press_key,
    right_click_element,
    scroll_at_element,
    type_code,
)
from phase3_orchestrator.prompts import (
    SYSTEM_PROMPT_FIGMA_TO_VSCODE,
    SYSTEM_PROMPT_GENERAL,
    build_user_message,
    format_elements_summary,
)

console = Console()


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
    Send the screenshot and message to Llama 3.2 Vision via Ollama.

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

    # check for completion signal
    done_match = re.match(r"^DONE:\s*(.+)$", response, re.IGNORECASE)
    if done_match:
        return "done", done_match.group(1).strip()

    # check for tool call
    tool_match = re.match(r"^TOOL:\s*(.+)$", response, re.IGNORECASE)
    if tool_match:
        return "tool", tool_match.group(1).strip()

    logger.warning(f"Model output didn't match expected format: {response[:100]}")
    return None, None


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
    # the safe execution namespace - only the tools we defined
    allowed_tools = {
        "click": lambda eid: click_element(eid, double=False),
        "double_click": lambda eid: click_element(eid, double=True),
        "right_click": right_click_element,
        "type_text": type_code,
        "press": press_key,
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
    # step 1: capture the screen
    console.print("[dim]Taking screenshot...[/dim]")
    screenshot, _ = capture_screen(save=False)

    # step 2: run OmniParser and immediately free its memory
    console.print("[dim]Running OmniParser...[/dim]")
    annotated, elements = parse_screen(screenshot)
    del screenshot  # free the raw screenshot, we only need the annotated one now
    gc.collect()

    # save the results so the tools can look up coordinates
    timestamp = None
    img_path, json_path = save_results(annotated, elements)

    elements_summary = format_elements_summary(elements)
    console.print(f"[green]Found {len(elements)} elements[/green]")

    # step 3: encode the annotated image and ask Ollama what to do
    image_b64 = image_to_base64(annotated)
    del annotated  # free the image before loading the LLM
    gc.collect()

    user_message = build_user_message(task, elements_summary)

    console.print("[dim]Asking Llama 3.2 Vision...[/dim]")
    response = call_ollama(
        image_b64=image_b64,
        user_message=user_message,
        system_prompt=system_prompt,
        history=history,
    )

    console.print(Panel(Text(response, style="cyan"), title="Model Response", border_style="blue"))

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


def run(
    task: str,
    max_cycles: int | None = None,
    system_prompt: str = SYSTEM_PROMPT_GENERAL,
) -> None:
    """
    Main entry point. Runs the agent loop until the task is done or we hit max_cycles.

    Args:
        task: what you want the agent to do
        max_cycles: stop after this many cycles. None = run until done or Ctrl+C.
        system_prompt: the system prompt text to guide the model
    """
    max_cycles = max_cycles or cfg.max_cycles
    history: list[dict] = []
    cycle = 0

    console.print(Panel(
        f"[bold green]Starting screen agent[/bold green]\n\nTask: {task}",
        border_style="green",
    ))

    try:
        while True:
            cycle += 1
            if max_cycles and cycle > max_cycles:
                console.print(f"[yellow]Reached max cycles ({max_cycles}), stopping.[/yellow]")
                break

            console.print(f"\n[bold]--- Cycle {cycle} ---[/bold]")

            result, is_done, history = run_agent_cycle(
                task,
                history,
                system_prompt=system_prompt,
            )

            if is_done:
                break

            # wait between cycles - gives UIs time to respond and lets you
            # hit Ctrl+C if something looks wrong
            console.print(f"[dim]Waiting {cfg.loop_delay}s...[/dim]")
            time.sleep(cfg.loop_delay)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")


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
    args = parser.parse_args()

    prompt = SYSTEM_PROMPT_GENERAL if args.workflow == "general" else SYSTEM_PROMPT_FIGMA_TO_VSCODE
    run(task=args.task, max_cycles=args.max_cycles, system_prompt=prompt)


if __name__ == "__main__":
    main()
