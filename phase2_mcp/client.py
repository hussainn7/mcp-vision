"""MCP client pool: stdio + SSE, tool discovery, reconnect.

The agent talks to MCP servers through one shape:

    pool.call("server.tool", **args) -> str
    pool.schemas() -> ollama-style function schemas (namespaced)

Transports:
    inproc  — a dict of callables (tests + local perception, no subprocess)
    stdio   — spawn `command args` (sandbox: the server is another process)
    sse     — HTTP+SSE url

The mcp SDK is optional. inproc works with the stdlib so CI can exercise
discovery, namespace, reconnect, and schema parsing without a server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


class MCPError(Exception):
    pass


def schema_from_mcp(name: str, description: str, input_schema: dict | None) -> dict:
    """MCP tool record -> Ollama/OpenAI function schema."""
    params = input_schema or {"type": "object", "properties": {}}
    if not isinstance(params, dict):
        params = {"type": "object", "properties": {}}
    params.setdefault("type", "object")
    params.setdefault("properties", {})
    return {"type": "function", "function": {
        "name": name, "description": description or "", "parameters": params}}


@dataclass
class ToolSpec:
    name: str
    description: str = ""
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    def as_schema(self, qualified: str) -> dict:
        return schema_from_mcp(qualified, self.description, self.input_schema)


class Session:
    """Minimal session protocol. Real stdio/SSE adapters wrap the MCP SDK."""

    def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError

    def call_tool(self, name: str, arguments: dict) -> str:
        raise NotImplementedError

    def close(self):
        pass


class InprocSession(Session):
    """In-process tools. `fns` maps name -> callable; `specs` optional schemas."""

    def __init__(self, fns: dict, specs: list[ToolSpec] | None = None):
        self.fns = fns
        self.specs = specs or [ToolSpec(name=n, description=getattr(fn, "__doc__", "") or "")
                               for n, fn in fns.items()]

    def list_tools(self) -> list[ToolSpec]:
        return list(self.specs)

    def call_tool(self, name: str, arguments: dict) -> str:
        if name not in self.fns:
            raise MCPError(f"unknown tool {name}")
        try:
            result = self.fns[name](**(arguments or {}))
        except TypeError as e:
            raise MCPError(f"bad args for {name}: {e}") from e
        except Exception as e:
            raise MCPError(f"{name} failed: {e}") from e
        if isinstance(result, (dict, list)):
            return json.dumps(result)
        return str(result)


@dataclass
class Client:
    name: str
    transport: str
    session: Session | None = None
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    factory: object = None  # callable () -> Session
    attempts: int = 0
    max_reconnect: int = 3

    def connect(self):
        if self.session is not None:
            return
        if self.factory is None:
            raise MCPError(f"{self.name}: no session factory")
        self.attempts += 1
        self.session = self.factory()
        self.tools = {t.name: t for t in self.session.list_tools()}

    def reconnect(self):
        self.close()
        if self.attempts >= self.max_reconnect:
            raise MCPError(f"{self.name}: reconnect budget exhausted")
        self.connect()

    def close(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None

    def call(self, tool: str, arguments: dict | None = None) -> str:
        if self.session is None:
            self.connect()
        try:
            return self.session.call_tool(tool, arguments or {})
        except MCPError:
            raise
        except Exception:
            self.reconnect()
            return self.session.call_tool(tool, arguments or {})


class MCPPool:
    def __init__(self):
        self.clients: dict[str, Client] = {}

    def add_inproc(self, name: str, fns: dict, specs: list[ToolSpec] | None = None):
        c = Client(name=name, transport="inproc",
                   factory=lambda: InprocSession(fns, specs))
        c.connect()
        self.clients[name] = c
        return c

    def add(self, name: str, transport: str, **kw):
        """Register a stdio or sse server. Connects lazily on first call."""
        if transport == "inproc":
            return self.add_inproc(name, kw["fns"], kw.get("specs"))
        factory = lambda: _connect_remote(transport, **kw)
        c = Client(name=name, transport=transport, factory=factory)
        self.clients[name] = c
        return c

    def schemas(self) -> list[dict]:
        out = []
        for c in self.clients.values():
            if c.session is None:
                try:
                    c.connect()
                except MCPError:
                    continue
            for t in c.tools.values():
                out.append(t.as_schema(f"{c.name}.{t.name}"))
        return out

    def call(self, qualified: str, **arguments) -> str:
        if "." not in qualified:
            raise MCPError(f"tool name must be server.tool, got {qualified!r}")
        server, tool = qualified.split(".", 1)
        if server not in self.clients:
            raise MCPError(f"unknown server {server}")
        return self.clients[server].call(tool, arguments)

    def close(self):
        for c in self.clients.values():
            c.close()


def _connect_remote(transport: str, **kw) -> Session:
    """Lazy MCP SDK adapter. Imported only when a real server is requested."""
    if transport == "stdio":
        return StdioSession(kw["command"], kw.get("args") or [])
    if transport in ("sse", "http"):
        return SSESession(kw["url"])
    raise MCPError(f"unknown transport {transport}")


class StdioSession(Session):
    def __init__(self, command: str, args: list[str]):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        import anyio

        self._params = StdioServerParameters(command=command, args=args)
        self._stdio_client = stdio_client
        self._ClientSession = ClientSession
        self._anyio = anyio
        # sync wrapper around the async SDK: one call at a time
        self._tools_cache = None

    def list_tools(self) -> list[ToolSpec]:
        raw = self._run("list")
        return [ToolSpec(name=t["name"], description=t.get("description") or "",
                         input_schema=t.get("inputSchema") or {}) for t in raw]

    def call_tool(self, name: str, arguments: dict) -> str:
        return self._run("call", name=name, arguments=arguments)

    def _run(self, op, **kw):
        async def _go():
            async with self._stdio_client(self._params) as (read, write):
                async with self._ClientSession(read, write) as session:
                    await session.initialize()
                    if op == "list":
                        res = await session.list_tools()
                        return [{"name": t.name, "description": t.description,
                                 "inputSchema": t.inputSchema} for t in res.tools]
                    res = await session.call_tool(kw["name"], kw.get("arguments") or {})
                    bits = []
                    for c in res.content or []:
                        bits.append(getattr(c, "text", None) or str(c))
                    return "\n".join(bits) or str(res)
        return self._anyio.run(_go)


class SSESession(Session):
    def __init__(self, url: str):
        self.url = url

    def list_tools(self) -> list[ToolSpec]:
        return self._run("list")

    def call_tool(self, name: str, arguments: dict) -> str:
        return self._run("call", name=name, arguments=arguments)

    def _run(self, op, **kw):
        import anyio
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        async def _go():
            async with sse_client(self.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    if op == "list":
                        res = await session.list_tools()
                        return [ToolSpec(name=t.name, description=t.description or "",
                                         input_schema=t.inputSchema or {}) for t in res.tools]
                    res = await session.call_tool(kw["name"], kw.get("arguments") or {})
                    bits = [getattr(c, "text", None) or str(c) for c in (res.content or [])]
                    return "\n".join(bits) or str(res)
        out = anyio.run(_go)
        return out


def perception_fns():
    """Local perception primitives, exposed as inproc MCP tools."""
    def som_parse():
        from phase1_vision.capture import capture_screen
        from phase1_vision.som import detect_ui_boxes, to_elements
        img, _ = capture_screen(save=False)
        return to_elements(detect_ui_boxes(img))

    def ground(target: str):
        from phase1_vision.coords import Viewport
        from phase1_vision.grounding import resolve
        return "use web_snapshot indices when a page is attached; " \
               f"parsed id={target}"

    return {
        "som_parse": som_parse,
        "ground": ground,
    }


def demo():
    # schema parsing: MCP inputSchema -> ollama function wrapper
    sch = schema_from_mcp("click", "Click a mark", {
        "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]})
    assert sch["function"]["name"] == "click"
    assert sch["function"]["parameters"]["required"] == ["id"]

    calls = {"n": 0}

    def ping(x=0):
        calls["n"] += 1
        return f"pong {x}"

    pool = MCPPool()
    pool.add_inproc("vision", {"ping": ping},
                    [ToolSpec("ping", "health", {"type": "object",
                                                 "properties": {"x": {"type": "integer"}}})])
    names = [s["function"]["name"] for s in pool.schemas()]
    assert names == ["vision.ping"]
    assert pool.call("vision.ping", x=1) == "pong 1"

    # reconnect: a dropped call rebuilds the session (inproc factory is cheap)
    c = pool.clients["vision"]
    c.session.call_tool = lambda name, arguments: (_ for _ in ()).throw(RuntimeError("gone"))
    # Client.call catches Exception, reconnects, retries
    assert pool.call("vision.ping", x=3).startswith("pong")

    # reconnect budget
    c2 = Client(name="dead", transport="inproc", factory=lambda: (_ for _ in ()).throw(MCPError("no")),
                max_reconnect=2)
    try:
        c2.connect()
        assert False, "should have raised"
    except MCPError:
        pass
    c2.attempts = 2
    try:
        c2.reconnect()
        assert False
    except MCPError as e:
        assert "budget" in str(e)

    try:
        pool.call("nope.x")
        assert False
    except MCPError:
        pass
    try:
        pool.call("ping")
        assert False
    except MCPError:
        pass

    pool.close()
    print("ok")


if __name__ == "__main__":
    demo()
