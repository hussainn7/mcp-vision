# Quick Guide

**[Overview (README)](README.md) | 🎯 [Quick Guide](QUICK_GUIDE.md) | 🤖 [Agent Instructions](AGENT_INSTRUCTIONS.md)**

---

Welcome to the **mac-agent** Quick Guide. This document explains what features work, how the system is organized, and how to get it running fully on your macOS machine.

---

## 🛠️ What Works

The agent runs a **Plan → Act → Reflect** loop locally by default. Here is a breakdown of what you can do:

1. **macOS AppleScript Automation**:
   - **Notes**: Create/verify notes in Apple Notes (`create_note`).
   - **Reminders**: Create reminders with optional due dates (`add_reminder`).
   - **Calendar**: Create events in the Calendar app (`create_event`).
2. **System & Shell Command Execution**:
   - Focus and launch applications (`open_app`).
   - List files/directories and read file contents (`list_dir`, `read_file`).
3. **Browser CDP Automation (Chrome/Chromium)**:
   - Navigate URLs, read main article contents, scroll, and press keys (`web_navigate`, `web_read`, `web_scroll`, `web_press`).
   - Generate accessible interactive trees with occlusion pruning (`web_snapshot`). This filters out unclickable elements and gives precise click targets.
   - Click and type into elements by accessibility indices (`web_click`, `web_type_into`).
   - **Safety Boundary**: Automatic blocking of dangerous actions (e.g., checkout/buy/pay buttons) and a prompt for manual approval (`y/n`) for any outbound clicks.
4. **Hybrid Micro-Vision**:
   - If an element is occluded/blocked, the agent crops a tiny thumbnail around the element and asks a local VLM (like `qwen2.5vl`) to find the click coordinate inside it.
5. **Observability & Self-Improvement**:
   - **Trace Waterfall**: Renders every run into a visual HTML waterfall (`trace_viewer.py`).
   - **Automatic Grading**: Judges each trajectory and stores failures in a regression test suite (`judge.py`).
   - **Skill Distillation**: Passing trajectories are saved as skills to guide the agent in future tasks (`skills.py`).

---

## 🚀 Setup Guide

Follow these steps to set up `mac-agent` on your machine:

### 1. Set Up Python Environment
Make sure you have **Python 3.12+** installed:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Install Playwright Browsers
To run the browser specialists, you need the Chromium browser binaries installed:
```bash
playwright install chromium
```

### 3. Setup Ollama (Local LLMs)
Install [Ollama](https://ollama.com) and pull the default models:
```bash
# Pull the function-calling/planning model (default: qwen3:8b)
ollama pull qwen3:8b

# (Optional) Pull the vision model if you want simple_agent or guide_user (default: qwen2.5vl:7b)
ollama pull qwen2.5vl:7b
```

### 4. Run the Pre-flight Check
Ensure your setup is valid by running:
```bash
python scripts/check_setup.py
```

### 5. Grant macOS System Permissions
Upon running a command, macOS will prompt you to allow access. Make sure to enable:
- **Automation** permissions (System Settings → Privacy & Security → Automation) to let AppleScript control Notes, Reminders, and Calendar.
- **Screen Recording** permissions (System Settings → Privacy & Security → Screen Recording) if using `simple_agent.py` or the `guide_user` vision-based tool.

### 6. Setup Environment Variables
Copy the template `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, etc.) only if you wish to run cloud-based backends instead of local Ollama models.

---

## 🌐 Running Browser Automation (Chrome CDP)

The web automation tools connect to Google Chrome via the Chrome DevTools Protocol (CDP) on port `9222`.

- **To use your own logged-in Chrome session** (cookies, history, logins):
  Close all active Google Chrome instances, then launch Chrome from your terminal with remote debugging enabled:
  ```bash
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
  ```
- **If Chrome is not running on port 9222**, the agent will automatically launch a clean, temporary debug profile.

---

## 🏃 Running the Agent

Start the agent with a specialist name and a task.

```bash
# General OS tasks (Notes, Calendar, shell)
python agent.py --as general "make a note called Ideas with a haiku about the sea"

# Web Research tasks (attaches to Chrome)
python agent.py --as web-researcher "summarize the top story on news.ycombinator.com"

# Swapping backend to a cloud model for complex tasks
python agent.py --as general --model claude "write a script that calculates primes and save it"
```

To list all available specialists:
```bash
python agent.py
```

To view the trajectory of a completed run:
```bash
python trace_viewer.py traces/run_<timestamp>.jsonl
# Then open the generated HTML file in your browser to inspect the timeline!
```
