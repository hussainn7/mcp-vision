"""Click CLI: serve, install, doctor."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from mcp_vision.log import configure


def agent_cli() -> None:
    """Installed entry point for the optional provider-backed example agent."""
    import agent
    argv = sys.argv[1:]
    if not argv or argv == ["--help"]:
        click.echo('mac-agent [--as general] [--model local|claude|gpt|gemini] "task"')
        return
    spec, backend, tui, task, debug = agent._parse_argv(argv)
    if not task:
        raise click.UsageError("give a task")
    from dashboard import Dashboard
    click.echo(agent.run(task, spec, backend=backend,
                         dashboard=Dashboard(task=task, enabled=tui), debug_state=debug))


@click.group()
def cli() -> None:
    """mcp-vision — screen perception and actuation over MCP."""
    configure()


@cli.command()
@click.option("--allow-browser-writes", is_flag=True, help="Allow routine browser input; risky actions still require local confirmation.")
@click.option("--headed", is_flag=True, help="Show the isolated browser (requires a display on the executor).")
@click.option("--origin", multiple=True, help="Restrict browser requests to these exact HTTP(S) origins. Repeat for dependencies.")
@click.option("--browser", "browser_mode", type=click.Choice(["isolated", "live"]), default="isolated", show_default=True,
              help="Use isolated Chromium or attach to your existing Chrome session.")
@click.option("--driver", "live_driver", type=click.Choice(["native", "cdp"]), default="native", show_default=True,
              help="live mode: native AppleScript (no automation banner) or CDP (shows banner).")
@click.option("--cdp-endpoint", default=None, help="Optional loopback endpoint for --driver cdp.")
def serve(allow_browser_writes: bool, headed: bool, origin: tuple[str, ...], browser_mode: str,
          live_driver: str, cdp_endpoint: str | None) -> None:
    """Run the MCP server on stdio (stdout is JSON-RPC only)."""
    from mcp_vision.server import main
    if cdp_endpoint and browser_mode != "live":
        raise click.UsageError("--cdp-endpoint requires --browser live")
    if cdp_endpoint:
        live_driver = "cdp"
    main(allow_browser_writes=allow_browser_writes, headless=not headed, allowed_origins=origin,
         browser_mode=browser_mode, cdp_endpoint=cdp_endpoint, live_driver=live_driver)


@cli.command()
def demo() -> None:
    """Exercise receipts in disposable Chromium, with no model or real accounts."""
    import asyncio
    from mcp_vision.demo import run_demo
    sys.exit(0 if asyncio.run(run_demo()) else 1)


@cli.command()
@click.argument("goal")
@click.option("--url", default="", help="Starting HTTP(S) page.")
@click.option("--success", default="", help="What an observed successful result looks like.")
@click.option("--mode", type=click.Choice(["observe", "draft"]), default="observe")
def task(goal: str, url: str, success: str, mode: str) -> None:
    """Prepare a portable task prompt for your connected MCP host."""
    from pydantic import ValidationError
    from mcp_vision.missions import Mission, brief
    try:
        click.echo(brief(Mission(goal=goal, url=url, success=success, mode=mode))["prompt"])
    except ValidationError as exc:
        raise click.BadParameter(str(exc)) from exc


@cli.command()
@click.option("--port", type=click.IntRange(0, 65535), default=7331, show_default=True)
def studio(port: int) -> None:
    """Open a local workspace for missions, host setup, and live browser evidence."""
    from mcp_vision.studio import serve_studio
    try:
        serve_studio(port)
    except OSError as exc:
        raise click.ClickException(f"Cannot start Mission Control: {exc}. Try --port 7332.") from exc


@cli.command()
@click.option("--port", type=click.IntRange(1024, 65535), default=7331, show_default=True)
@click.option("--model", "provider", default=None,
              help="Model provider: auto, local, claude, chatgpt, gemini, or nvidia.")
@click.option("--driver", "live_driver", type=click.Choice(["native", "cdp"]), default="native")
@click.option("--cdp-endpoint", default=None, help="Existing Chrome debugging endpoint; enables file attachment.")
def ui(port: int, provider: str | None, live_driver: str, cdp_endpoint: str | None) -> None:
    """Run the macOS contextual popup. Invoke it anywhere with Option-Space."""
    from mcp_vision.macos_ui import run_contextual_ui
    try:
        run_contextual_ui(port=port, provider=provider, live_driver=live_driver, cdp_endpoint=cdp_endpoint)
    except (OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.option("--port", type=click.IntRange(1024, 65535), default=7331, show_default=True)
def status(port: int) -> None:
    """Show contextual runtime and permission status without changing anything."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as response:
            state = json.load(response)
        click.echo(f"  [ok] runtime: {state.get('runtime', 'ready')} at 127.0.0.1:{port}")
    except Exception:
        click.echo(f"  [off] runtime: not listening at 127.0.0.1:{port} (start with: mcp-vision ui)")
    from mcp_vision.utils.doctor import status_checks
    for check in status_checks():
        click.echo(f"  [{'ok' if check.ok else 'needs attention'}] {check.name}: {check.detail}")


@cli.command()
@click.option("--command", default=None, help="Override the server executable written into host configs.")
@click.option("--host", type=click.Choice(["cursor", "claude-desktop", "antigravity"]), default=None,
              help="Update only this host. Omit for the legacy multi-host registration.")
@click.option("--browser", "browser_mode", type=click.Choice(["live", "isolated"]), default="live", show_default=True)
@click.option("--allow-browser-writes", is_flag=True, help="Enable routine input; sensitive actions still require approval.")
@click.option("--config-path", type=click.Path(path_type=Path), default=None,
              help="Use a custom JSON config location for the selected host.")
def install(command: str | None, host: str | None, browser_mode: str, allow_browser_writes: bool, config_path) -> None:
    """Register mcp-vision in Claude Desktop, Cursor, and Codex."""
    from mcp_vision.utils.config_sync import install_hosts, install_host
    if host:
        try:
            path = install_host(host, command, browser_mode=browser_mode, allow_writes=allow_browser_writes,
                                config_path=config_path)
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"updated {path}")
        click.echo("Restart or refresh your host's MCP connection, then ask it to list browser tabs.")
        click.echo("Contextual UI: run 'mcp-vision doctor', then 'mcp-vision ui'.")
        click.echo("Chrome action: load the repository's chrome_relay folder as an unpacked extension.")
        return
    if config_path or allow_browser_writes or browser_mode != "live":
        raise click.UsageError("Select --host when setting browser options or a config path.")
    click.echo("Legacy registration uses isolated Chromium. Use --host to configure existing Chrome.")
    paths = install_hosts(command)
    for p in paths:
        click.echo(f"updated {p}")
    click.echo("Next: run 'mcp-vision doctor'. The contextual UI is optional and independent of MCP hosts.")


@cli.command("config")
@click.option("--browser", "browser_mode", type=click.Choice(["live", "isolated"]), default="live")
@click.option("--allow-browser-writes", is_flag=True)
def host_config(browser_mode: str, allow_browser_writes: bool) -> None:
    """Print a portable MCP entry without changing any host settings."""
    import json
    from mcp_vision.utils.config_sync import _entry
    click.echo(json.dumps({"mcpServers": {"mcp-vision": _entry(browser_mode=browser_mode,
                      allow_writes=allow_browser_writes)}}, indent=2))


@cli.command()
@click.option("--driver", type=click.Choice(["native", "cdp"]), default="native", show_default=True)
@click.option("--wait/--no-wait", default=False, help="For cdp: open remote-debugging and wait for Allow.")
def connect(driver: str, wait: bool) -> None:
    """Check the existing Chrome connection. Native driver avoids the automation banner."""
    import asyncio

    if driver == "cdp":
        from mcp_vision.live_browser import LiveBrowserRuntime
        from phase2_mcp.chrome_bridge import request_live_session, websocket_endpoint
        if wait and not websocket_endpoint():
            click.echo("Opening Chrome's remote debugging page. Enable it and click Allow when prompted.")
            if not request_live_session():
                raise click.ClickException("Timed out waiting for Chrome remote debugging.")

        async def check_cdp():
            runtime = LiveBrowserRuntime()
            try:
                return await runtime.tabs()
            finally:
                await runtime.close()

        result = asyncio.run(check_cdp())
        if not result["connected"]:
            raise click.ClickException(
                f'{result.get("error", "not connected")}\n'
                "Enable Remote debugging at chrome://inspect/#remote-debugging, then retry: "
                "mcp-vision connect --driver cdp --wait"
            )
    else:
        from mcp_vision.native_browser import NativeBrowserRuntime

        async def check_native():
            runtime = NativeBrowserRuntime()
            try:
                return await runtime.tabs()
            finally:
                await runtime.close()

        result = asyncio.run(check_native())
        if not result["connected"]:
            raise click.ClickException(
                f'{result.get("error", "not connected")}\n'
                "Open Google Chrome, then retry. On first use Chrome may ask to allow JavaScript from Apple Events."
            )

    click.echo(f'Connected to existing Chrome ({result.get("driver", driver)}): '
               f'{len(result["tabs"])} accessible tab(s).')
    from mcp_vision.redaction import redact
    for tab in result["tabs"][:12]:
        click.echo(f'  [{tab["tab_id"]}] {redact(tab["title"])[:60]}  {redact(tab["url"])[:80]}')
    if driver == "native":
        click.echo("Native driver: no automation banner. Use: mcp-vision serve --browser live")
    else:
        click.echo("CDP driver shows Chrome's automation banner. Prefer --driver native when possible.")


@cli.command()
@click.option("--host", type=click.Choice(["cursor", "claude-desktop", "antigravity"]), default="cursor",
              show_default=True)
@click.option("--skip-playwright", is_flag=True, help="Skip Chromium download.")
def setup(host: str, skip_playwright: bool) -> None:
    """One-shot setup: wire your MCP host for live Chrome and check the connection."""
    import shutil
    import subprocess
    from mcp_vision.utils.config_sync import install_host

    if not skip_playwright:
        click.echo("installing Chromium for demos…")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

    which = shutil.which("mcp-vision")
    path = install_host(host, which, browser_mode="live", allow_writes=True)
    click.echo(f"wired {host}: {path}")

    try:
        import asyncio
        from mcp_vision.native_browser import NativeBrowserRuntime

        async def check():
            rt = NativeBrowserRuntime()
            try:
                return await rt.tabs()
            finally:
                await rt.close()

        result = asyncio.run(check())
        if result.get("connected"):
            click.echo(f'Chrome ok ({result.get("driver")}): {len(result.get("tabs", []))} tab(s)')
        else:
            click.echo("Open Google Chrome, then run: mcp-vision connect")
    except Exception:
        click.echo("Open Google Chrome, then run: mcp-vision connect")

    click.echo("")
    click.echo('Try:  mcp-vision ask "flights to SFO from ATL next month"')
    click.echo("Or ask Cursor/Claude after refreshing MCP.")


@cli.command()
@click.argument("query")
@click.option("--model", "backend", default="local", show_default=True,
              help="Summarizer backend: local (Ollama), anthropic, openai, gemini, nvidia, or none.")
@click.option("--isolated", is_flag=True, help="Use disposable Chromium instead of your Chrome.")
def ask(query: str, backend: str, isolated: bool) -> None:
    """Run a query in Chrome and print a clean answer (summarizer runs on success)."""
    import asyncio
    from mcp_vision.ask import run_ask
    be = None if backend in {"none", "off", "heuristic"} else backend
    result = asyncio.run(run_ask(query, backend=be, live=not isolated))
    click.echo(result.get("summary") or "(no summary)")
    sys.exit(0 if result.get("ok") else 1)


@cli.command("probe")
@click.option("--live/--isolated", default=False, help="Prefer existing Chrome when available.")
@click.option("--headed/--headless", default=True, show_default=True, help="Show the browser window.")
@click.option("--summarize/--no-summarize", default=True, show_default=True,
              help="On success, run a second AI pass to clean up the report.")
def probe(live: bool, headed: bool, summarize: bool) -> None:
    """Run real public-site tasks and barrier checks. Keeps personal content out of logs."""
    import asyncio
    from pathlib import Path
    from mcp_vision.real_tasks import run_probe
    ok = asyncio.run(run_probe(prefer_live=live, headed=headed))
    if ok and summarize:
        report = Path("outputs/real_tasks/results.json")
        evidence = report.read_text() if report.exists() else ""
        from mcp_vision.summarize import summarize as clean
        click.echo("")
        click.echo(clean("Summarize these mcp-vision probe results for a human.",
                         evidence, backend="local", ok=True))
    sys.exit(0 if ok else 1)


@cli.command()
def doctor() -> None:
    """Check display permissions, accessibility, and local backends."""
    from mcp_vision.utils.doctor import run_doctor
    checks = run_doctor()
    optional = {"ollama", "cli"}
    failed = False
    for c in checks:
        mark = "ok" if c.ok else ("skip" if c.name in optional else "FAIL")
        click.echo(f"  [{mark}] {c.name}: {c.detail}")
        if not c.ok and c.name not in optional:
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    cli()
