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
def serve(allow_browser_writes: bool, headed: bool, origin: tuple[str, ...]) -> None:
    """Run the MCP server on stdio (stdout is JSON-RPC only)."""
    from mcp_vision.server import main
    main(allow_browser_writes=allow_browser_writes, headless=not headed, allowed_origins=origin)


@cli.command()
def demo() -> None:
    """Exercise receipts in disposable Chromium, with no model or real accounts."""
    import asyncio
    from mcp_vision.demo import run_demo
    sys.exit(0 if asyncio.run(run_demo()) else 1)


@cli.command()
@click.option("--command", default=None, help="Override the server executable written into host configs.")
def install(command: str | None) -> None:
    """Register mcp-vision in Claude Desktop and Cursor."""
    from mcp_vision.utils.config_sync import install_hosts
    paths = install_hosts(command)
    for p in paths:
        click.echo(f"updated {p}")


@cli.command()
def connect() -> None:
    """Use your real Chrome without the automation banner (no CDP)."""
    from phase2_mcp import chrome_native as cn

    cn.ensure_chrome()
    if sys.platform == "darwin":
        click.echo("Using your installed Chrome via AppleScript.")
        click.echo("Experimental personal-profile access. Prefer serve for an isolated browser.")
        return
    raise click.ClickException("The legacy extension relay is experimental. Use mcp-vision serve for the isolated cross-platform browser.")


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
