"""Execute from outside the checkout with only the installed wheel available."""
import asyncio
from importlib.resources import files
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    import agent
    import backends
    import tools
    from mcp_vision.tracing import Tracer
    assert agent.load_specialists()
    assert files("mcp_vision").joinpath("browser.py").is_file()
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_vision.cli", "serve"])
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            assert initialized.instructions and "snapshot_id" in initialized.instructions
            result = await session.list_tools()
            names = {t.name for t in result.tools}
            assert {"browser_snapshot", "browser_navigate", "browser_fill", "browser_click",
                    "browser_verify_text", "browser_screenshot", "screen_image"} <= names
            blocked = await session.call_tool("browser_navigate", {"url": "file:///etc/passwd"})
            assert not blocked.is_error
            assert '"executed":false' in ''.join(c.text for c in blocked.content if hasattr(c, "text")).replace(' ', '')
    print("installed wheel imports and MCP handshake passed")


if __name__ == "__main__":
    asyncio.run(main())
