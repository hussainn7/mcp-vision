"""Brains Blip can think with.

Subscription engines drive a CLI the user is already signed in to (Claude
Code with a Claude Pro/Max plan, Codex with ChatGPT Plus/Pro, Cursor's
agent, Gemini CLI with a Google account), so their plan pays for the
reasoning and no API key is needed. API engines use a key instead.

Each CLI runs once per turn in an empty temporary directory with its tools
turned off: Blip only wants words and ``[POINT]`` tags back, never edits.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp_vision.buddy.conversation import Turn
from mcp_vision.buddy.prompt import screen_label

# GUI apps launched from Finder get a bare PATH; look where installers put CLIs.
SEARCH_DIRS = ("~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin",
               "~/.bun/bin", "~/.volta/bin", "~/.cursor/bin", "~/.codex/bin", "~/bin", "/usr/bin")
NVM_GLOB = "~/.nvm/versions/node/*/bin"


class EngineError(RuntimeError):
    """The engine ran but couldn't answer (signed out, rate limited, crashed)."""


@dataclass(frozen=True)
class EngineSpec:
    id: str
    label: str                      # what the user calls it: "Claude", "ChatGPT"
    via: str                        # how Blip reaches it
    kind: str                       # "subscription" | "api"
    vision: bool                    # accepts screenshots
    binaries: tuple[str, ...] = ()
    login: str = ""                 # command to run in Terminal to sign in
    install: str = ""               # command (or URL) to install it
    key_name: str = ""              # API engines: the env var holding the key
    blurb: str = ""


SPECS: tuple[EngineSpec, ...] = (
    EngineSpec("claude-code", "Claude", "Claude Pro / Max via Claude Code", "subscription", True,
               binaries=("claude",), login="claude auth login",
               install="curl -fsSL https://claude.ai/install.sh | bash",
               blurb="Sees your screenshots and streams answers on your Claude plan."),
    EngineSpec("codex", "ChatGPT", "ChatGPT Plus / Pro via Codex CLI", "subscription", True,
               binaries=("codex",), login="codex login", install="npm i -g @openai/codex",
               blurb="Sees your screenshots; answers arrive a sentence at a time."),
    EngineSpec("cursor", "Cursor", "Your Cursor plan via Cursor CLI", "subscription", False,
               binaries=("cursor-agent", "agent"), login="cursor-agent login",
               install="curl https://cursor.com/install -fsS | bash",
               blurb="Text only: Blip reads the screen's controls out to it."),
    EngineSpec("gemini", "Gemini", "Google account via Gemini CLI", "subscription", True,
               binaries=("gemini",), login="gemini", install="npm i -g @google/gemini-cli",
               blurb="Sees your screenshots on your Google account's free or paid tier."),
    EngineSpec("anthropic", "Claude API", "Anthropic API key", "api", True, key_name="ANTHROPIC_API_KEY",
               install="https://console.anthropic.com/settings/keys",
               blurb="Fastest first word: streamed with prompt caching and adaptive effort."),
)
BY_ID = {spec.id: spec for spec in SPECS}
PREFERENCE = ("claude-code", "anthropic", "codex", "gemini", "cursor")


# -- finding and probing ---------------------------------------------------------

def find_binary(names: Iterable[str], path: str | None = None) -> str | None:
    """First executable named ``names`` on PATH or in the usual install dirs."""
    dirs = [d for d in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep) if d]
    dirs += [os.path.expanduser(d) for d in SEARCH_DIRS]
    dirs += sorted(glob.glob(os.path.expanduser(NVM_GLOB)), reverse=True)
    for name in names:
        for directory in dirs:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def child_env(binary: str | None = None) -> dict[str, str]:
    """Environment for a CLI: our PATH plus the install dirs (node CLIs need ``node``)."""
    env = dict(os.environ)
    extra = [os.path.expanduser(d) for d in SEARCH_DIRS]
    if binary:
        extra.insert(0, os.path.dirname(binary))
    extra += sorted(glob.glob(os.path.expanduser(NVM_GLOB)), reverse=True)[:1]
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    env["PATH"] = os.pathsep.join(dict.fromkeys(parts + extra))
    env.setdefault("NO_COLOR", "1")
    return env


@dataclass(frozen=True)
class RunResult:
    code: int
    out: str
    err: str


Runner = Callable[[list[str], float], RunResult]


def run_quick(argv: list[str], timeout: float = 6.0) -> RunResult:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=child_env(argv[0]),
                              stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return RunResult(124, "", "timed out")
    except OSError as exc:
        return RunResult(127, "", str(exc))
    return RunResult(done.returncode, done.stdout, done.stderr)


@dataclass
class EngineStatus:
    spec: EngineSpec
    status: str = "unknown"          # ready | not-installed | logged-out | missing-key | unknown
    path: str | None = None
    detail: str = ""
    version: str = ""

    @property
    def usable(self) -> bool:
        """Worth trying: ready, or installed but we couldn't tell whether it's signed in."""
        return self.status in {"ready", "unknown"}

    def card(self, selected: bool = False) -> dict[str, Any]:
        spec = self.spec
        card: dict[str, Any] = {"id": spec.id, "label": spec.label, "via": spec.via, "kind": spec.kind,
                                "status": self.status, "vision": spec.vision, "selected": selected,
                                "detail": self.detail or spec.blurb}
        if spec.kind == "subscription":
            if self.status == "not-installed":
                card["install"] = spec.install
            if self.status in {"logged-out", "unknown"}:
                card["login"] = self._login_command()
        if spec.key_name:
            card["keyName"] = spec.key_name
        return card

    def _login_command(self) -> str:
        if not self.path:
            return self.spec.login
        binary, _, rest = self.spec.login.partition(" ")
        return " ".join(filter(None, [_shell_quote(self.path), rest]))


def _shell_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)


def probe(spec: EngineSpec, settings: Any, *, runner: Runner = run_quick,
          which: Callable[[Iterable[str]], str | None] = find_binary,
          home: Path | None = None) -> EngineStatus:
    if spec.kind == "api":
        key = getattr(settings, spec.key_name.lower(), None) or os.environ.get(spec.key_name)
        return EngineStatus(spec, "ready" if key else "missing-key",
                            detail="" if key else "Paste a key to use it.")
    path = which(spec.binaries)
    if not path:
        return EngineStatus(spec, "not-installed", detail=f"Install it, then sign in. {spec.blurb}")
    status = EngineStatus(spec, "unknown", path=path)
    checker = _LOGIN_CHECKS.get(spec.id)
    if checker is not None:
        checker(status, runner, home or Path.home())
    if status.status == "ready" and not status.detail:
        status.detail = spec.blurb
    return status


def _check_claude(status: EngineStatus, runner: Runner, home: Path) -> None:
    result = runner([status.path, "auth", "status", "--json"], 6.0)
    if result.code == 0 and result.out.strip().startswith("{"):
        try:
            data = json.loads(result.out)
        except ValueError:
            data = {}
        if data.get("loggedIn") is True:
            who = data.get("email") or data.get("account", {}).get("email") or ""
            plan = data.get("subscriptionType") or data.get("authMethod") or ""
            status.status = "ready"
            status.detail = " · ".join(filter(None, [f"Signed in as {who}" if who else "Signed in", plan]))
            return
        if data.get("loggedIn") is False:
            status.status = "logged-out"
            status.detail = "Run claude auth login once to sign in with your Claude plan."
            return
    if result.code != 0 and "unknown" not in (result.err + result.out).lower() and result.code != 127:
        status.status = "logged-out"
        status.detail = "Run claude auth login once to sign in with your Claude plan."
        return
    # Older Claude Code without `auth status`: credentials live in the keychain or this file.
    if (home / ".claude" / ".credentials.json").exists() or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        status.status = "ready"


def _check_codex(status: EngineStatus, runner: Runner, home: Path) -> None:
    result = runner([status.path, "login", "status"], 6.0)
    text = (result.out + result.err).strip()
    if result.code == 0 and "not logged in" not in text.lower():
        status.status = "ready"
        status.detail = "Signed in with ChatGPT" if "chatgpt" in text.lower() else (text.splitlines() or [""])[0]
    elif result.code in {124, 127}:
        if (home / ".codex" / "auth.json").exists():
            status.status = "ready"
    else:
        status.status = "logged-out"
        status.detail = "Run codex login once and choose Sign in with ChatGPT."


def _check_cursor(status: EngineStatus, runner: Runner, home: Path) -> None:
    result = runner([status.path, "status"], 8.0)
    text = (result.out + result.err).strip()
    lowered = text.lower()
    if "not logged in" in lowered or "not authenticated" in lowered or "login" in lowered and result.code != 0:
        status.status = "logged-out"
        status.detail = "Run cursor-agent login once to sign in with your Cursor account."
    elif result.code == 0 and ("logged in" in lowered or "@" in text):
        status.status = "ready"
        line = next((ln.strip(" ✓") for ln in text.splitlines() if "logged in" in ln.lower()), "")
        status.detail = line or "Signed in"
    elif os.environ.get("CURSOR_API_KEY"):
        status.status = "ready"


def _check_gemini(status: EngineStatus, runner: Runner, home: Path) -> None:
    creds = home / ".gemini" / "oauth_creds.json"
    if creds.exists() or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        status.status = "ready"
        status.detail = "Signed in with Google" if creds.exists() else "Using your Gemini API key"
    else:
        status.status = "logged-out"
        status.detail = "Run gemini once in Terminal and pick Login with Google."


_LOGIN_CHECKS = {"claude-code": _check_claude, "codex": _check_codex, "cursor": _check_cursor,
                 "gemini": _check_gemini}


class EngineRegistry:
    """Probes engines off the main thread and caches the result for the Settings window."""

    def __init__(self, settings: Callable[[], Any], *, runner: Runner = run_quick,
                 which: Callable[[Iterable[str]], str | None] = find_binary, ttl: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.runner = runner
        self.which = which
        self.ttl = ttl
        self.clock = clock
        self._cache: list[EngineStatus] | None = None
        self._stamp = 0.0

    def statuses(self, refresh: bool = False) -> list[EngineStatus]:
        if refresh or self._cache is None or self.clock() - self._stamp > self.ttl:
            settings = self.settings()
            self._cache = [probe(spec, settings, runner=self.runner, which=self.which) for spec in SPECS]
            self._stamp = self.clock()
        return list(self._cache)

    def cached(self) -> list[EngineStatus]:
        return list(self._cache) if self._cache is not None else [EngineStatus(spec) for spec in SPECS]

    def cards(self, selected: str = "", probe: bool = False) -> list[dict[str, Any]]:
        """Engine cards for Settings. Without ``probe`` this never spawns a process."""
        statuses = self.statuses(refresh=True) if probe else self.cached()
        active = choose_engine(self.settings(), statuses, preferred=selected)
        return [status.card(selected=active is not None and status.spec.id == active.spec.id)
                for status in statuses]


def choose_engine(settings: Any, statuses: list[EngineStatus] | None = None,
                  preferred: str | None = None) -> EngineStatus | None:
    """The user's pick if it can run, else the best engine that's ready."""
    statuses = statuses if statuses is not None else [probe(spec, settings) for spec in SPECS]
    by_id = {status.spec.id: status for status in statuses}
    wanted = preferred or getattr(settings, "engine", "")
    if wanted:
        pick = by_id.get(wanted)
        if pick is not None and pick.status not in {"not-installed", "missing-key"}:
            return pick
    for engine_id in PREFERENCE:
        status = by_id.get(engine_id)
        if status is not None and status.status == "ready":
            return status
    for engine_id in PREFERENCE:
        status = by_id.get(engine_id)
        if status is not None and status.status == "unknown":
            return status
    return None


def make_engine_brain(status: EngineStatus, settings: Any):
    spec = status.spec
    if spec.id == "anthropic":
        from mcp_vision.buddy.brain_claude import ClaudeBrain

        return ClaudeBrain(api_key=getattr(settings, "anthropic_api_key", None),
                           model=getattr(settings, "model", "claude-opus-5-5"),
                           effort=getattr(settings, "effort", "low"),
                           max_tokens=getattr(settings, "max_tokens", 16000))
    brain_class = {"claude-code": ClaudeCodeBrain, "codex": CodexBrain, "cursor": CursorBrain,
                   "gemini": GeminiBrain}[spec.id]
    return brain_class(status.path or spec.binaries[0], model=getattr(settings, "cli_model", "") or "",
                       effort=getattr(settings, "effort", "low"))


# -- prompt rendering shared by the CLIs ---------------------------------------------

def transcript_prompt(turns: list[Turn], image_paths: list[str] | None = None,
                      system: str | None = None) -> str:
    """One self-contained prompt: (system), earlier exchanges, then this turn."""
    *history, current = turns
    parts: list[str] = []
    if system:
        parts.append(f"<instructions>\n{system}\n</instructions>")
    if history:
        lines = [f"{'user' if turn.role == 'user' else 'you (blip)'}: {turn.text}" for turn in history]
        parts.append("<earlier_conversation>\n" + "\n".join(lines) + "\n</earlier_conversation>")
    if current.images and image_paths:
        labels = [f"{path}: {screen_label(shot, len(current.images))}"
                  for path, shot in zip(image_paths, current.images, strict=False)]
        parts.append("<screenshots>\n" + "\n".join(labels) + "\n</screenshots>")
    parts.append(current.text)
    return "\n\n".join(parts)


def write_images(turn: Turn, directory: str) -> list[str]:
    paths = []
    for index, shot in enumerate(turn.images, start=1):
        suffix = ".png" if shot.media_type == "image/png" else ".jpg"
        path = os.path.join(directory, f"screen{index}{suffix}")
        with open(path, "wb") as handle:
            handle.write(shot.data)
        paths.append(path)
    return paths


# -- the CLI brains ----------------------------------------------------------------------

@dataclass
class Invocation:
    argv: list[str]
    stdin: bytes | None = None
    env: dict[str, str] = field(default_factory=dict)


class StreamParser:
    """Turns a CLI's JSON lines into text deltas; remembers errors and the final text."""

    def __init__(self):
        self.produced = False
        self.error = ""
        self.final = ""

    def feed(self, line: str) -> list[str]:
        line = line.strip()
        if not line.startswith("{"):
            return []
        try:
            event = json.loads(line)
        except ValueError:
            return []
        texts = [text for text in self.handle(event) if text]
        if texts:
            self.produced = True
        return texts

    def handle(self, event: dict[str, Any]) -> list[str]:
        raise NotImplementedError

    def finish(self) -> list[str]:
        """Anything not streamed yet (CLIs that only report the final answer)."""
        if not self.produced and self.final:
            self.produced = True
            return [self.final]
        return []


class CLIBrain:
    kind = "subscription"
    name = "cli"
    label = "CLI"
    vision = True
    effort_map: dict[str, str] = {}

    def __init__(self, binary: str, *, model: str = "", effort: str = "low", timeout: float = 120.0,
                 spawn: Callable[..., Any] | None = None):
        self.binary = binary
        self.model = model or None
        self.effort = effort
        self.timeout = timeout
        self._spawn = spawn or asyncio.create_subprocess_exec

    # subclasses fill these in
    def invocation(self, *, system: str, turns: list[Turn], workdir: str, detailed: bool) -> Invocation:
        raise NotImplementedError

    def parser(self) -> StreamParser:
        raise NotImplementedError

    def _effort(self, detailed: bool) -> str:
        from mcp_vision.buddy.brain_claude import _EFFORT_STEP

        effort = _EFFORT_STEP.get(self.effort, self.effort) if detailed else self.effort
        return self.effort_map.get(effort, effort)

    async def warm(self) -> str:
        return self.label

    async def stream(self, *, system: str, turns: list[Turn], detailed: bool = False) -> AsyncIterator[str]:
        workdir = tempfile.mkdtemp(prefix="blip-")
        process = None
        try:
            call = self.invocation(system=system, turns=turns, workdir=workdir, detailed=detailed)
            env = child_env(self.binary)
            env.update(call.env)
            try:
                process = await self._spawn(*call.argv, stdin=asyncio.subprocess.PIPE if call.stdin is not None
                                            else asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                            stderr=asyncio.subprocess.PIPE, cwd=workdir, env=env,
                                            limit=8 * 1024 * 1024)
            except FileNotFoundError as exc:
                raise EngineError(f"{self.label} is not installed ({exc.filename}).") from exc
            if call.stdin is not None:
                process.stdin.write(call.stdin)
                await process.stdin.drain()
                process.stdin.close()
            parser = self.parser()
            errors = asyncio.ensure_future(process.stderr.read())
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EngineError(f"{self.label} timed out.")
                try:
                    raw = await asyncio.wait_for(process.stdout.readline(), remaining)
                except asyncio.TimeoutError as exc:
                    raise EngineError(f"{self.label} timed out.") from exc
                if not raw:
                    break
                for text in parser.feed(raw.decode("utf-8", "replace")):
                    yield text
            code = await process.wait()
            stderr = (await errors).decode("utf-8", "replace")
            for text in parser.finish():
                yield text
            if parser.error and not parser.produced:
                raise EngineError(f"{self.label}: {parser.error}")
            if code != 0 and not parser.produced:
                raise EngineError(f"{self.label} failed: {_tail(parser.error or stderr) or f'exit {code}'}")
        finally:
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), 2.0)
            shutil.rmtree(workdir, ignore_errors=True)


def _tail(text: str, limit: int = 240) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " ".join(lines[-3:])[-limit:]


# Claude Code ----------------------------------------------------------------------------

class ClaudeCodeParser(StreamParser):
    def handle(self, event):
        kind = event.get("type")
        if kind == "stream_event":
            inner = event.get("event") or {}
            delta = inner.get("delta") or {}
            if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                return [delta.get("text", "")]
        elif kind == "assistant" and not self.produced:
            # Without partial messages we still get whole assistant messages.
            message = event.get("message") or {}
            self.final = "".join(block.get("text", "") for block in message.get("content", [])
                                 if block.get("type") == "text") or self.final
        elif kind == "result":
            if event.get("is_error") or event.get("subtype", "success") != "success":
                self.error = str(event.get("result") or event.get("subtype") or "error")
            elif not self.final:
                self.final = str(event.get("result") or "")
        return []


class ClaudeCodeBrain(CLIBrain):
    name = "claude-code"
    label = "Claude"
    vision = True

    def invocation(self, *, system, turns, workdir, detailed):
        *history, current = turns
        content: list[dict[str, Any]] = []
        for shot in current.images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": shot.media_type,
                                                        "data": base64.standard_b64encode(shot.data).decode()}})
            content.append({"type": "text", "text": screen_label(shot, len(current.images))})
        content.append({"type": "text", "text": transcript_prompt([*history, Turn("user", current.text)])})
        message = {"type": "user", "message": {"role": "user", "content": content}}
        argv = [self.binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--system-prompt", system,
                "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"]
        if self.model:
            argv += ["--model", self.model]
        # Effort maps onto Claude Code's own setting; Blip keeps it light unless the router asks for depth.
        env = {"CLAUDE_CODE_EFFORT_LEVEL": self._effort(detailed), "DISABLE_AUTOUPDATER": "1",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        return Invocation(argv, stdin=(json.dumps(message) + "\n").encode(), env=env)

    def parser(self):
        return ClaudeCodeParser()


# Codex (ChatGPT) -------------------------------------------------------------------------

class CodexParser(StreamParser):
    def __init__(self):
        super().__init__()
        self._sent: dict[str, str] = {}

    def handle(self, event):
        kind = event.get("type", "")
        if kind in {"item.updated", "item.completed"}:
            item = event.get("item") or {}
            if item.get("type") in {"agent_message", "assistant_message"}:
                text = item.get("text") or ""
                key = str(item.get("id", ""))
                previous = self._sent.get(key, "")
                if text.startswith(previous) and len(text) > len(previous):
                    self._sent[key] = text
                    return [text[len(previous):]]
        elif kind in {"turn.failed", "error"}:
            error = event.get("error") or {}
            self.error = str(error.get("message") if isinstance(error, dict) else error or event.get("message")
                             or "failed")
        return []


class CodexBrain(CLIBrain):
    name = "codex"
    label = "ChatGPT"
    vision = True
    effort_map = {"xhigh": "high", "max": "high"}

    def invocation(self, *, system, turns, workdir, detailed):
        images = write_images(turns[-1], workdir)
        prompt = transcript_prompt(turns, images, system=system)
        argv = [self.binary, "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only",
                "--color", "never", "-c", f"model_reasoning_effort={json.dumps(self._effort(detailed))}"]
        if self.model:
            argv += ["--model", self.model]
        for path in images:
            argv += ["--image", path]
        argv.append("-")          # prompt on stdin: no argv length limits
        return Invocation(argv, stdin=prompt.encode())

    def parser(self):
        return CodexParser()


# Cursor -----------------------------------------------------------------------------------

class CursorParser(StreamParser):
    def __init__(self):
        super().__init__()
        self._seen = ""

    def handle(self, event):
        kind = event.get("type")
        if kind == "assistant":
            message = event.get("message") or {}
            text = "".join(block.get("text", "") for block in message.get("content", [])
                           if block.get("type") == "text")
            if not text:
                return []
            # --stream-partial-output sends deltas; a final full copy may follow, so skip repeats.
            if self._seen and text == self._seen:
                return []
            if self._seen and text.startswith(self._seen):
                delta = text[len(self._seen):]
                self._seen = text
                return [delta]
            self._seen += text
            return [text]
        if kind == "result":
            if event.get("is_error") or event.get("subtype", "success") != "success":
                self.error = str(event.get("result") or event.get("error") or "error")
            elif not self.final:
                self.final = str(event.get("result") or "")
        return []


class CursorBrain(CLIBrain):
    name = "cursor"
    label = "Cursor"
    vision = False

    def invocation(self, *, system, turns, workdir, detailed):
        prompt = transcript_prompt(turns, system=system)
        argv = [self.binary, "-p", "--output-format", "stream-json", "--stream-partial-output",
                "--mode", "ask", "--trust"]
        if self.model:
            argv += ["--model", self.model]
        argv.append(prompt)
        return Invocation(argv)

    def parser(self):
        return CursorParser()


# Gemini ----------------------------------------------------------------------------------

class GeminiParser(StreamParser):
    def handle(self, event):
        kind = event.get("type")
        if kind == "message" and event.get("role") == "assistant":
            return [str(event.get("content") or "")]
        if kind == "error":
            self.error = str(event.get("message") or event.get("error") or "error")
        if kind == "result" and event.get("status") not in {None, "success"}:
            error = event.get("error") or {}
            self.error = str(error.get("message") if isinstance(error, dict) else error or "failed")
        return []


class GeminiBrain(CLIBrain):
    name = "gemini"
    label = "Gemini"
    vision = True

    def invocation(self, *, system, turns, workdir, detailed):
        images = write_images(turns[-1], workdir)
        prompt = transcript_prompt(turns, system=system)
        refs = " ".join(f"@{os.path.basename(path)}" for path in images)
        argv = [self.binary, "--output-format", "stream-json", "--approval-mode", "default"]
        if self.model:
            argv += ["--model", self.model]
        argv += ["--prompt", (refs + "\n\n" + prompt) if refs else prompt]
        return Invocation(argv)

    def parser(self):
        return GeminiParser()


__all__ = ["BY_ID", "SPECS", "ClaudeCodeBrain", "CodexBrain", "CursorBrain", "EngineError", "EngineRegistry",
           "EngineSpec", "EngineStatus", "GeminiBrain", "choose_engine", "find_binary", "make_engine_brain", "probe",
           "transcript_prompt"]
