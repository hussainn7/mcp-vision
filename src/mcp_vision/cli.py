"""Click CLI: serve, install, doctor."""

from __future__ import annotations

import sys

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
              help="Use isolated Chromium or attach to your existing, operator-approved Chrome session.")
@click.option("--cdp-endpoint", default=None, help="Optional loopback endpoint for live Chrome; otherwise discover Chrome's local endpoint.")
def serve(allow_browser_writes: bool, headed: bool, origin: tuple[str, ...], browser_mode: str, cdp_endpoint: str | None) -> None:
    """Run the MCP server on stdio (stdout is JSON-RPC only)."""
    from mcp_vision.server import main
    if cdp_endpoint and browser_mode != "live":
        raise click.UsageError("--cdp-endpoint requires --browser live")
    main(allow_browser_writes=allow_browser_writes, headless=not headed, allowed_origins=origin,
         browser_mode=browser_mode, cdp_endpoint=cdp_endpoint)


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
@click.option("--command", default=None, help="Override the server executable written into host configs.")
def install(command: str | None) -> None:
    """Register mcp-vision in Claude Desktop, Cursor, and Codex."""
    from mcp_vision.utils.config_sync import install_hosts
    paths = install_hosts(command)
    for p in paths:
        click.echo(f"updated {p}")


@cli.command()
def connect() -> None:
    """Check the existing Chrome connection without changing browser settings."""
    import asyncio
    from mcp_vision.live_browser import LiveBrowserRuntime

    async def check():
        runtime = LiveBrowserRuntime()
        try:
            return await runtime.tabs()
        finally:
            await runtime.close()

    result = asyncio.run(check())
    if not result["connected"]:
        raise click.ClickException(result["error"])
    click.echo(f'Connected to existing Chrome: {len(result["tabs"])} accessible tab(s).')
    click.echo("Use mcp-vision serve --browser live in your MCP host. Chrome stays open.")


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
