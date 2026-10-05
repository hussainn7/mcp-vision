"""`python -m mcp_vision.cli buddy run` is what Plip.app starts; `plip` is the same commands."""

from __future__ import annotations

import click

from mcp_vision.buddy.cli import buddy as _buddy
from mcp_vision.log import configure


@click.group()
def cli() -> None:
    """Plip: hold Control+Option and ask."""
    configure()
    from mcp_vision.analytics import ping
    ping("plip")


cli.add_command(_buddy)


if __name__ == "__main__":
    cli()
