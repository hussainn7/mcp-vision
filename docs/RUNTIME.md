# Runtime contract

The MCP host owns model selection, planning, and task-specific success criteria. MCP-Vision owns the isolated browser, target observations, input policy, and receipts. A server instance serves one trusted host/session. Do not expose it as a shared unauthenticated service.

## Browser action lifecycle

1. Navigate to an HTTP(S) URL. Optional exact-origin request restrictions apply to the owned browser context. Service workers are disabled; this allowlist is not an OS/network sandbox.
2. Read a snapshot. It includes a random ID, URL, visible text, and DOM-derived controls. Controls bind to element handles, rather than a selector that might silently resolve to a replacement.
3. Pass the snapshot ID and index into an action. The target must still be attached, unchanged in markup/geometry, reachable, and within a 30-second observation window.
4. Apply write policy. No routine writes unless the operator enabled them. Recognized sensitive controls and form submissions additionally require a human confirmer. The model has no approval override parameter.
5. Revalidate after approval, dispatch, and consume the observation. Take another snapshot before the next action.
6. Inspect a receipt and check a relevant postcondition. Never blindly retry an action that may already have executed.

| Field | Meaning |
|---|---|
| `action_id` | Unique receipt ID |
| `status` | `verified`, `unverified`, `blocked`, `stale`, `error` |
| `executed` | `true`: dispatched; `false`: not dispatched; `null`: dispatch uncertain |
| `evidence` | Observed URL/value match/text predicate; not the entire task oracle |
| `task_complete` | Always false for individual browser operations |

Click dispatch returns `unverified`. Fill read-back verifies only the field value. Text presence does not prove a backend save, delivery, payment, or persistence. Applications with such requirements need an independent state check appropriate to the task.

Browser operations are serialized per runtime. Desktop calls have a separate lock and invalidate their screen observation after input. Human confirmation is required for all desktop input because pixels cannot establish application semantics. Desktop `ActionResult.ok` means input dispatched, with `verification: unverified`.

## Integrating Python directly

```python
from mcp_vision.browser import BrowserRuntime

async def inspect():
    runtime = BrowserRuntime(allowed_origins=["https://example.com"])
    try:
        receipt = await runtime.navigate("https://example.com")
        if receipt.status != "verified":
            return receipt
        return await runtime.snapshot()
    finally:
        await runtime.close()
```

Direct Python consumers can supply a `Governor(confirmer=...)` implemented by a trusted operator interface. Do not derive that callback's decision from model output or page content. An injected Playwright page is for embedding/testing; its owner is responsible for request isolation and lifecycle.

## Current limits

Snapshot checks reduce stale-target mistakes; they cannot make asynchronous websites atomic or detect changes hidden in JavaScript event handlers. The current browser path does not inspect iframes or shadow roots, and it approximates accessible names. The desktop parser is image segmentation/OCR, not an OS accessibility implementation. Browser snapshots do not execute a model or use vision automatically; the host chooses when to request an image.

Legacy agent traces are separate from MCP browser receipts. Heuristic `pass` means execution-quality feedback, with `task_completion: unknown` unless a trusted completion predicate was recorded. Automatic skill learning requires that explicit verified completion event.
