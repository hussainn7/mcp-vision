const HOST = "http://127.0.0.1:9230";
const CONTEXT_HOST = "http://127.0.0.1:7331";
const MENU_ID = "ask-mcp-vision";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_ID, title: "Ask MCP-Vision", contexts: ["all"] });
  });
});

async function sendInvocation(tab, info) {
  let context;
  try {
    context = await chrome.tabs.sendMessage(tab.id, "capture-context");
  } catch (_) {
    context = {
      source: "chrome", source_application: "Google Chrome", url: tab.url || "", title: tab.title || "",
      selected_text: info.selectionText || "", cursor_position: null,
      session: { authenticated: null, session_kind: "existing-chrome-profile" }, identity: { status: "unknown" },
    };
  }
  if (info.selectionText && !context.selected_text) context.selected_text = info.selectionText;
  const response = await fetch(CONTEXT_HOST + "/api/context", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-MCP-Vision": "chrome-extension" },
    body: JSON.stringify(context),
  });
  if (!response.ok) throw new Error("local runtime unavailable");
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID || !tab || !tab.id) return;
  sendInvocation(tab, info).then(() => {
    chrome.action.setBadgeText({ text: "✓", tabId: tab.id });
    chrome.action.setBadgeBackgroundColor({ color: "#2D8C74", tabId: tab.id });
    setTimeout(() => chrome.action.setBadgeText({ text: "", tabId: tab.id }), 1400);
  }).catch(() => {
    chrome.action.setBadgeText({ text: "!", tabId: tab.id });
    chrome.action.setBadgeBackgroundColor({ color: "#C65B48", tabId: tab.id });
  });
});

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
