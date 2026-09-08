"""Read-only smoke test against IANA's stable example domain."""

import asyncio

from mcp_vision.browser import BrowserRuntime


async def main() -> None:
    runtime = BrowserRuntime(allowed_origins=["https://example.com"])
    try:
        navigation = await runtime.navigate("https://example.com/")
        assert navigation.status == "verified", navigation
        snapshot = await runtime.snapshot()
        assert snapshot.url == "https://example.com/"
        assert "Example Domain" in snapshot.text
        assert any(element["role"] == "link" for element in snapshot.elements)
        evidence = await runtime.verify_text("Example Domain")
        assert evidence.status == "verified" and not evidence.task_complete
        assert (await runtime.screenshot()).startswith(b"\x89PNG")
        print("public read-only browser smoke passed")
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
