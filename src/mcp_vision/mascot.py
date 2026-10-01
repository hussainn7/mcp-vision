"""Mascot banner — pixel octopus, printed on CLI startup."""

from __future__ import annotations

import sys

# ANSI colour helpers — no deps, falls back gracefully on non-TTY
_RED    = "\033[38;5;203m"
_DKRED  = "\033[38;5;160m"
_WHITE  = "\033[97m"
_BLACK  = "\033[30m"
_GRAY   = "\033[90m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"

_OCTO = [
    "         {r}▄▄████▄▄{x}",
    "       {r}██{dk}▀{r}██████{dk}▀{r}██{x}",
    "       {r}██{w}●{r}████{w}●{r}██{x}",
    "       {r}████████████{x}",
    "        {r}▀████████▀{x}",
    "    {r}▄▄{x}  {r}▄██████▄{x}  {r}▄▄{x}",
    "   {r}████▄{x} {r}████████{x} {r}▄████{x}",
    "   {r}█████{x} {r}████████{x} {r}█████{x}",
    "   {r}▀████{x} {r}▀██████▀{x} {r}████▀{x}",
    "    {r}▀▀▀{x}  {r}▀▀▀▀▀▀{x}  {r}▀▀▀{x}",
]


def _supports_color() -> bool:
    return sys.stdout.isatty() and sys.platform != "win32"


def _line(template: str, colored: bool) -> str:
    if colored:
        return template.format(r=_RED, dk=_DKRED, w=_WHITE, x=_RESET)
    # strip all placeholders
    return template.format(r="", dk="", w="", x="")


def _tagline(ax: bool, sc: bool, colored: bool) -> str:
    """Quick health summary next to the mascot."""
    def dot(ok: bool) -> str:
        if not colored:
            return "ok" if ok else "!!"
        return ("\033[32m●\033[0m" if ok else "\033[31m●\033[0m")

    parts = [
        f"  {dot(ax)} accessibility",
        f"  {dot(sc)} screen recording",
    ]
    return "\n".join(parts)


def print_banner(
    *,
    ax_ok: bool = True,
    sc_ok: bool = True,
    version: str = "",
    subtitle: str = "screen perception + actuation over MCP",
) -> None:
    colored = _supports_color()

    lines = [_line(t, colored) for t in _OCTO]

    name = (f"{_BOLD}{_RED}mcp-vision{_RESET}" if colored else "mcp-vision")
    ver  = (f" {_GRAY}v{version}{_RESET}" if version and colored else (f" v{version}" if version else ""))
    sub  = (f"{_GRAY}{subtitle}{_RESET}" if colored else subtitle)

    # Right-hand panel lines
    right = [
        f"  {name}{ver}",
        f"  {sub}",
        "",
        f"  {'accessibility':18s}{'ok' if ax_ok else 'needs grant':>6}",
        f"  {'screen recording':18s}{'ok' if sc_ok else 'needs grant':>6}",
        "",
        f"  {'mcp-vision doctor':18s}{'run to diagnose':>15}" if not (ax_ok and sc_ok) else "",
    ]
    if colored:
        def colour_status(ok: bool, text: str) -> str:
            return ("\033[32m" + text + _RESET) if ok else ("\033[31m" + text + _RESET)
        right[3] = f"  {'accessibility':18s}" + colour_status(ax_ok, "ok" if ax_ok else "needs grant")
        right[4] = f"  {'screen recording':18s}" + colour_status(sc_ok, "ok" if sc_ok else "needs grant")

    for i, art_line in enumerate(lines):
        side = right[i] if i < len(right) else ""
        print(f"{art_line:<35}{side}")

    print()
