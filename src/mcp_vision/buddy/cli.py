"""`mcp-vision buddy`: run the on-screen buddy, or ask it one question headlessly."""
from __future__ import annotations

import asyncio
import json
import sys

import click


@click.group(invoke_without_command=True)
@click.pass_context
def buddy(ctx: click.Context) -> None:
    """Your AI buddy next to the cursor: hold Control+Option, talk, and it points at things."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@buddy.command()
def run() -> None:
    """Start the macOS buddy (menu bar + cursor overlay + push-to-talk)."""
    from mcp_vision.buddy.factory import SetupError

    if sys.platform != "darwin":
        raise click.ClickException("The buddy overlay needs macOS. Try: mcp-vision buddy ask --image shot.png \"...\"")
    try:
        from mcp_vision.buddy.app_macos import run_buddy_app
        run_buddy_app()
    except SetupError as exc:
        raise click.ClickException(str(exc)) from exc


@buddy.command()
@click.argument("question")
@click.option("--image", "images", multiple=True, type=click.Path(exists=True, dir_okay=False),
              help="Use these images as the screens instead of capturing (repeatable).")
@click.option("--speak/--no-speak", default=False, help="Also say the answer out loud.")
@click.option("--json", "as_json", is_flag=True, help="Print the full turn result as JSON.")
def ask(question: str, images: tuple[str, ...], speak: bool, as_json: bool) -> None:
    """Ask one question about the screen and print what the buddy says and points at."""
    from mcp_vision.buddy.capture import ScreenCapturer
    from mcp_vision.buddy.factory import SetupError, make_companion
    from mcp_vision.buddy.settings import load_settings
    from mcp_vision.buddy.speech_out import PrintVoice, QueueSpeaker

    settings = load_settings()
    capturer = (ScreenCapturer.from_images(list(images), max_edge=settings.max_image_edge,
                                           quality=settings.jpeg_quality) if images else None)
    speaker = None if speak else QueueSpeaker(PrintVoice(write=lambda text: None))
    try:
        companion = make_companion(settings, capturer=capturer, speaker=speaker)
    except SetupError as exc:
        raise click.ClickException(str(exc)) from exc
    result = asyncio.run(companion.respond(question))
    if as_json:
        click.echo(json.dumps({
            "state": result.state, "error": result.error, "spoken": result.spoken,
            "route": result.route.__dict__, "timings_ms": result.timings,
            "targets": [{**target.__dict__, "element": target.element.__dict__ if target.element else None}
                        for target in result.targets],
        }, indent=2))
    else:
        click.echo(result.spoken or result.error)
        for target in result.targets:
            click.echo(f"  -> {target.label or 'here'}: ({target.x:.0f}, {target.y:.0f}) on screen{target.screen}"
                       f"{' [snapped]' if target.source == 'snapped' else ''}")
        click.echo(f"  route={result.route.provider}/{result.route.intent} "
                   f"screens={'yes' if result.route.needs_screen else 'no'} "
                   f"first_speech={result.timings.get('first_speech', '-')}ms "
                   f"total={result.timings.get('spoken', '-')}ms", err=True)
    sys.exit(0 if result.state == "done" else 1)


@buddy.command()
@click.option("--ping", is_flag=True, help="Make one tiny Jev request to verify the TypeSafe key.")
def doctor(ping: bool) -> None:
    """Check keys, voice, router, and macOS permissions for the buddy."""
    from mcp_vision.buddy.factory import make_jev
    from mcp_vision.buddy.settings import load_settings
    from mcp_vision.buddy.speech_out import default_voice

    settings = load_settings()
    ok = True

    def line(good: bool | None, name: str, detail: str) -> None:
        mark = {True: "ok", False: "FAIL", None: "--"}[good]
        click.echo(f"  [{mark:>4}] {name}: {detail}")

    has_claude = bool(settings.anthropic_api_key)
    ok &= has_claude
    line(has_claude, "brain", f"{settings.model} effort={settings.effort}" if has_claude
         else "ANTHROPIC_API_KEY missing")
    jev = make_jev(settings)
    if jev is None:
        line(None, "jev router", "TYPESAFE_API_KEY not set; using rule routing")
    elif ping:
        from mcp_vision.buddy.router import JevRouter

        route = asyncio.run(JevRouter(jev).route("where is the save button", []))
        good = route.provider == "jev"
        ok &= good
        line(good, "jev router", f"{settings.typesafe_model} answered in {route.latency_ms} ms" if good
             else "request failed; check the key at console.typesafe.ai")
    else:
        line(True, "jev router", f"{settings.typesafe_model} configured (use --ping to verify)")
    voice, fallback = default_voice(settings)
    line(True, "voice", voice.name + (f" (fallback {fallback.name})" if fallback else ""))
    if sys.platform == "darwin":
        from mcp_vision.native_permissions import native_permission_snapshot

        snap = native_permission_snapshot()
        for key, name in (("screenRecording", "screen recording"), ("accessibility", "accessibility"),
                          ("microphone", "microphone"), ("speechRecognition", "speech recognition")):
            value = snap.get(key)
            if key == "screenRecording":
                ok &= value is True
            line(value, name, {True: "granted", False: "denied", None: "not asked yet"}[value])
    else:
        line(None, "overlay", "macOS only; `buddy ask --image` works everywhere")
    sys.exit(0 if ok else 1)
