const HOST = "http://127.0.0.1:9230";

async function evalInTab(tabId, js) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: (code) => {
      try { return String(eval(code)); } catch (e) { return "ERROR: " + e.message; }
    },
    args: [js],
  });
  return result;
}

async function handle(cmd) {
  if (!cmd || !cmd.method) return { ok: true, idle: true };
  const tabs = await chrome.tabs.query({});
  if (cmd.method === "tabs") {
    return {
      ok: true,
      tabs: tabs.map((t) => ({ id: t.id, url: t.url || "", title: t.title || "", window: t.windowId, tab: t.index })),
    };
  }
  const active = tabs.find((t) => t.active) || tabs[0];
  if (cmd.method === "activate") {
    const q = String(cmd.params.target || "").toLowerCase();
    const hit = tabs.find((t, i) => String(i) === q || (t.url || "").toLowerCase().includes(q) || (t.title || "").toLowerCase().includes(q));
    if (!hit) return { ok: false, error: "no tab" };
    await chrome.tabs.update(hit.id, { active: true });
    await chrome.windows.update(hit.windowId, { focused: true });
    return { ok: true, url: hit.url, title: hit.title };
  }
  if (cmd.method === "navigate") {
    const url = cmd.params.url;
    const host = (() => { try { return new URL(url).hostname; } catch { return ""; } })();
    const hit = tabs.find((t) => (t.url || "").includes(host));
    if (hit) {
      await chrome.tabs.update(hit.id, { active: true, url: hit.url.includes(url) ? undefined : url });
      await chrome.windows.update(hit.windowId, { focused: true });
    } else if (active) {
      await chrome.tabs.create({ url, windowId: active.windowId });
    }
    return { ok: true };
  }
  if (cmd.method === "eval") {
    const t = tabs.find((x) => x.active) || active;
    if (!t) return { ok: false, error: "no tab" };
    const value = await evalInTab(t.id, cmd.params.js);
    return { ok: true, value };
  }
  return { ok: false, error: "unknown method" };
}

async function tick() {
  try {
    const res = await fetch(HOST + "/poll");
    const cmd = await res.json();
    if (cmd && cmd.method) {
      const out = await handle(cmd);
      await fetch(HOST + "/result", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(out),
      });
    }
  } catch (_) {}
}

setInterval(tick, 400);
tick();
