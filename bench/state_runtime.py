"""Microbenchmark deterministic state compilation and local bounded choice."""
from __future__ import annotations

import asyncio
import statistics
import time

from mcp_vision.browser import BrowserSnapshot
from mcp_vision.fast_policy import RulePolicy
from mcp_vision.state import compile_state


def fixture(size: int = 200) -> BrowserSnapshot:
    elements = []
    for index in range(size):
        role = "button" if index % 2 else "textbox"
        elements.append({
            "index": index, "role": role, "name": f"Control {index}", "value": "",
            "x": (index % 10) * 100, "y": (index // 10) * 35, "w": 90, "h": 30,
            "tag": "button" if role == "button" else "input",
            "input_type": "button" if role == "button" else "text",
            "identity": {"dom": f"{role}:{index}"},
        })
    return BrowserSnapshot(snapshot_id="benchmark", url="https://fixture.test/", title="Fixture",
                           text=" ".join(item["name"] for item in elements), elements=elements)


async def main(iterations: int = 500) -> None:
    sample = fixture()
    compile_ms = []
    choose_ms = []
    for epoch in range(1, iterations + 1):
        started = time.perf_counter()
        state = compile_state(sample, epoch=epoch)
        compile_ms.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        await RulePolicy().choose("press Control 199", state)
        choose_ms.append((time.perf_counter() - started) * 1000)
    def p95(values):
        return statistics.quantiles(values, n=20)[18]

    print(f"elements={len(sample.elements)} iterations={iterations}")
    print(f"compile p50={statistics.median(compile_ms):.3f}ms p95={p95(compile_ms):.3f}ms")
    print(f"rules   p50={statistics.median(choose_ms):.3f}ms p95={p95(choose_ms):.3f}ms")


if __name__ == "__main__":
    asyncio.run(main())
