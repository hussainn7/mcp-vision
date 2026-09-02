"""Click CLI: serve, install, doctor."""

from __future__ import annotations

import sys

import click

from mcp_vision.log import configure


@click.group()
def cli() -> None:
    """mcp-vision — screen perception and actuation over MCP."""
    configure()


@cli.command()
def serve() -> None:
    """Run the MCP server on stdio (stdout is JSON-RPC only)."""
    from mcp_vision.server import main
    main()


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
        click.echo("No DevTools attach — Google will not see webdriver / the automation infobar.")
        return
    cn.get_relay()
    click.echo(cn.install_hint())
    click.echo("Leave this terminal open, then run the agent.")


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
