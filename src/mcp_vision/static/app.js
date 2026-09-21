"use strict";
const $ = (id) => document.getElementById(id);
const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  plug: '<path d="m7 3 2 4m8-4-2 4M6 7h12v3a6 6 0 0 1-12 0V7Zm6 9v5"/>',
  shield: '<path d="m12 3 8 3v5c0 5-8 10-8 10S4 16 4 11V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',
  spark: '<path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5L12 2Z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  search: '<circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6"/>',
  compare: '<rect x="3" y="5" width="7" height="15" rx="2"/><rect x="14" y="3" width="7" height="17" rx="2"/><path d="M6 9h1m10-2h1"/>',
  scan: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 12l3 3 7-7"/>',
  edit: '<path d="m14 4 6 6M4 20l5-1L21 7a2 2 0 0 0-4-4L5 15l-1 5Z"/>',
  play: '<path d="m8 4 12 8-12 8V4Z"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v12h5"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
};
function icon(name) {
  const span = document.createElement("span");
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.spark}</svg>`;
  return span;
}
document.querySelectorAll("[data-icon]").forEach(el => el.replaceWith(icon(el.dataset.icon)));
function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
let toastTimer;
function toast(message) {
  (document.querySelector("dialog[open]") || document.body).append($("toast"));
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5000);
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied. Ready for your agent."); }
  catch { toast("Clipboard access is unavailable. Select the text to copy it manually."); }
}
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const anchor = element("a"); anchor.href = url; anchor.download = name;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-MCP-Vision": "studio"}, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error([result.error, result.help].filter(Boolean).join(" "));
  return result;
}
const views = {workspace: "Mission control", recipes: "Recipe library", activity: "Session activity", connections: "Connections"};
function navigate() {
  const view = location.hash.slice(1) in views ? location.hash.slice(1) : "workspace";
  for (const name of Object.keys(views)) $("view-" + name).hidden = name !== view;
  document.querySelectorAll("[data-view]").forEach(a => {
    const active = a.dataset.view === view;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  $("breadcrumb").textContent = views[view];
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", navigate); navigate();
let recipes = [], serverEntry, currentBrief, selectedHost = "cursor";
const activities = [];
function recipeCard(recipe) {
  const card = element("button", "recipe-card"); card.type = "button";
  const tile = element("span", "recipe-icon"); tile.append(icon(recipe.icon));
  card.append(tile, element("span", "recipe-arrow", "↗"), element("span", "recipe-category", recipe.category), element("h3", "", recipe.name), element("p", "", recipe.description));
  card.addEventListener("click", () => {
    $("goal").value = recipe.goal; $("success").value = recipe.success; $("mode").value = recipe.mode;
    location.hash = "workspace"; navigate(); $("goal").focus();
    toast("Recipe loaded. Add your starting page and make it yours.");
  });
  return card;
}
function showRecipes() {
  const query = $("recipe-search").value.trim().toLowerCase();
  const found = recipes.filter(r => `${r.name} ${r.category} ${r.description}`.toLowerCase().includes(query));
  $("all-recipes").replaceChildren(...found.map(recipeCard));
  $("no-recipes").hidden = found.length !== 0;
}
$("recipe-search").addEventListener("input", showRecipes);
function showBrief(result) {
  currentBrief = result;
  $("brief-boundary").textContent = result.boundary;
  $("brief-steps").replaceChildren(...result.steps.map(s => element("li", "", s)));
  $("brief-prompt").textContent = result.prompt;
  $("brief-missing").textContent = [result.needs_destination ? "No starting page yet: your agent will ask for a destination." : "", result.needs_success_criteria ? "No success criteria yet: agree on the expected result with your agent." : ""].filter(Boolean).join(" ");
  $("brief-dialog").showModal();
}
$("mission-form").addEventListener("submit", async event => {
  event.preventDefault(); $("prepare").disabled = true;
  try {
    const result = await api("/api/brief", {goal: $("goal").value, url: $("url").value, success: $("success").value, mode: $("mode").value});
    addActivity("brief", result); showBrief(result);
  } catch (error) { toast(error.message); }
  finally { $("prepare").disabled = false; }
});
$("copy-brief").addEventListener("click", () => copy(currentBrief.prompt));
$("export-brief").addEventListener("click", () => download("mcp-vision-mission.md", currentBrief.prompt, "text/markdown"));
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => $(button.dataset.close).close()));
function addActivity(type, result) {
  activities.unshift({type, result, at: new Date()});
  activities.splice(20); renderActivity();
}
function renderActivity() {
  $("activity-count").textContent = activities.length;
  if (!activities.length) {
    const empty = element("div", "empty-state"); empty.append(icon("activity"), element("h2", "", "Room for your first mission."), element("p", "", "Prepare a task brief or run the browser demo. The evidence will appear here."));
    const link = element("a", "primary", "Create a mission ↗"); link.href = "#workspace"; empty.append(link);
    $("activity-list").replaceChildren(empty); return;
  }
  $("activity-list").replaceChildren(...activities.map(item => {
    const row = element("article", "activity-row"), content = element("div"), badge = element("span", "activity-dot");
    badge.append(icon(item.type === "brief" ? "edit" : "scan"));
    const status = item.type === "brief" ? "Brief prepared · Not executed" : item.result.demo_passed ? "Sandbox checks passed" : "Sandbox check failed";
    content.append(element("h3", "", item.type === "brief" ? item.result.mission.goal : item.result.title), element("p", "", `${status} · ${item.at.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}`));
    const button = element("button", "secondary", item.type === "brief" ? "View brief ↗" : "View receipts ↗");
    button.addEventListener("click", () => { if (item.type === "brief") showBrief(item.result); else { renderDemo(item.result); $("demo-dialog").showModal(); } });
    row.append(badge, content, button); return row;
  }));
}
renderActivity();
function connectionConfig() {
  if (!serverEntry) return;
  const entry = {...serverEntry, args: [...serverEntry.args]};
  entry.args.push("--browser", $("browser-session").value);
  if ($("writes").checked) entry.args.push("--allow-browser-writes");
  const hosts = {
    cursor: ["Connect to Cursor", "Add this server entry to ~/.cursor/mcp.json, keeping your other servers. Then enable it in Cursor's MCP settings."],
    claude: ["Connect to Claude Desktop", "Open Settings → Developer → Edit Config. Merge this server entry with your existing mcpServers, then restart Claude Desktop."],
    "claude-code": ["Connect to Claude Code", "Run this command in a macOS or Linux terminal, then check the connection with /mcp in Claude Code."],
    antigravity: ["Connect to Antigravity", "Open MCP Servers → Manage MCP Servers → View raw config. Merge this entry into mcpServers, then refresh. The current global location is ~/.gemini/config/mcp_config.json; use the location shown by your installed version."],
    other: ["Connect any local MCP host", "Use this stdio configuration in your host's MCP settings. Your host must run locally on the computer that will perform the actions."]
  };
  $("host-title").textContent = hosts[selectedHost][0]; $("host-help").textContent = hosts[selectedHost][1];
  const quote = s => "'" + s.replaceAll("'", "'\\''") + "'";
  $("host-config").textContent = selectedHost === "claude-code" ?
    `claude mcp add --transport stdio --scope user mcp-vision -- ${[entry.command, ...entry.args].map(quote).join(" ")}` : JSON.stringify({mcpServers: {"mcp-vision": entry}}, null, 2);
  document.querySelectorAll("[data-host]").forEach(b => b.setAttribute("aria-selected", b.dataset.host === selectedHost));
}
document.querySelectorAll("[data-host]").forEach(button => button.addEventListener("click", () => { selectedHost = button.dataset.host; connectionConfig(); }));
$("writes").addEventListener("change", connectionConfig);
$("browser-session").addEventListener("change", connectionConfig);
$("copy-config").addEventListener("click", () => copy($("host-config").textContent));
$("open-demo").addEventListener("click", () => $("demo-dialog").showModal());
$("demo-form").addEventListener("submit", async event => {
  event.preventDefault(); $("run-demo").disabled = true; $("demo-progress").hidden = false; $("demo-result").replaceChildren();
  try {
    const result = await api("/api/demo", {title: $("demo-title").value});
    renderDemo(result); addActivity("demo", result);
  } catch (error) { $("demo-result").append(element("p", "error-message", error.message)); }
  finally { $("run-demo").disabled = false; $("demo-progress").hidden = true; }
});
function renderDemo(result) {
  const session = result.session;
  const summary = element("div", "result-summary" + (result.demo_passed ? "" : " failed"));
  summary.append(element("strong", "", result.demo_passed ? "✓ Subgoal verified; boundary held" : "The session needs inspection"), element("span", "mono", `${(result.duration_ms / 1000).toFixed(1)}s · ${session.metrics.actions} actions · ${session.metrics.observations} observations`));
  const mission = element("section", "session-mission"), missionCopy = element("div");
  missionCopy.append(element("span", "session-kicker", "CURRENT GOAL"), element("h3", "", session.goal), element("p", "", session.subgoal));
  const status = element("div", `session-status ${session.status}`);
  status.append(element("span", "local-dot"), element("strong", "", session.status), element("small", "", session.reason));
  mission.append(missionCopy, status);

  const workspace = element("div", "session-workspace"), computer = element("section", "computer-pane");
  const computerBar = element("div", "computer-bar");
  computerBar.append(element("span", "computer-dots", "● ● ●"), element("span", "mono", "demo.mcp-vision.invalid"));
  const viewport = element("div", "computer-viewport"), screenshot = element("img", "demo-screenshot"), highlight = element("span", "target-highlight");
  screenshot.src = "data:image/png;base64," + result.screenshot; screenshot.alt = "Chromium after FastPath completed the draft preview."; highlight.hidden = true;
  viewport.append(screenshot, highlight); computer.append(computerBar, viewport);

  const inspector = element("aside", "step-inspector"), timeline = element("div", "session-timeline"), details = element("div", "step-detail");
  const showStep = (step, button) => {
    timeline.querySelectorAll("button").forEach(item => item.classList.toggle("active", item === button));
    details.replaceChildren(
      element("span", "session-kicker", `STEP ${String(step.number).padStart(2, "0")} · ${step.policy.toUpperCase()}`),
      element("h3", "", `${(step.operation || "decision").replaceAll("_", " ")} ${step.target?.name || ""}`.trim()),
      element("p", "", step.reason || "Bounded action selected from the current state's candidates."),
      metricRow("Confidence", `${Math.round(step.confidence * 100)}%`),
      metricRow("Execution", step.execution_path || "not dispatched"),
      metricRow("Focus", step.background === true ? "Stayed in background" : step.background === false ? "Used foreground" : "No focus claim"),
      metricRow("Observed change", step.diff?.changed ? summarizeDiff(step.diff) : "No semantic change")
    );
    const raw = element("details", "step-raw");
    raw.append(element("summary", "", "Inspect step metadata"), element("pre", "", JSON.stringify(step, null, 2))); details.append(raw);
    if (step.target?.bounds) {
      const box = step.target.bounds;
      Object.assign(highlight.style, {left: `${box.x / 10}%`, top: `${box.y / 7.2}%`, width: `${box.w / 10}%`, height: `${box.h / 7.2}%`});
      highlight.hidden = false;
    } else highlight.hidden = true;
  };
  session.steps.forEach((step, i) => {
    const button = element("button", "timeline-step"); button.type = "button";
    button.append(element("span", "timeline-index", String(step.number).padStart(2, "0")), element("span", "", (step.operation || "decision").replaceAll("_", " ")), element("b", `receipt-status ${step.status}`, step.status));
    button.addEventListener("click", () => showStep(step, button)); timeline.append(button);
    if (i === 0) queueMicrotask(() => showStep(step, button));
  });
  inspector.append(element("span", "session-kicker", "FASTPATH TIMELINE"), timeline, details); workspace.append(computer, inspector);

  const evidence = element("section", "session-evidence");
  evidence.append(
    evidenceCard("Verification", session.verification?.passed ? "Passed" : "Not passed", session.verification?.passed ? `Observed “${session.verification.expected}” in successor state ${session.verification.state_id.slice(0, 8)}.` : session.reason, session.verification?.passed),
    evidenceCard("Execution", `${session.metrics.background_actions} background`, `${session.metrics.policy_ms.toFixed(1)} ms policy time · ${session.metrics.stale_rejections} stale retries`, true),
    evidenceCard("Guardrails", `${session.guardrails.filter(item => item.passed).length}/${session.guardrails.length} held`, session.guardrails.map(item => `${item.passed ? "✓" : "×"} ${item.name}`).join(" · "), session.guardrails.every(item => item.passed))
  );
  const footer = element("div", "session-footer"), compareButton = element("button", "secondary", "Show before");
  let showingBefore = false;
  compareButton.addEventListener("click", () => { showingBefore = !showingBefore; screenshot.src = "data:image/png;base64," + (showingBefore ? result.before_screenshot : result.screenshot); compareButton.textContent = showingBefore ? "Show verified result" : "Show before"; highlight.hidden = showingBefore; });
  const exportButton = element("button", "secondary", "Export session evidence ↓");
  exportButton.addEventListener("click", () => { const {screenshot: _after, before_screenshot: _before, ...report} = result; download("mcp-vision-session.json", JSON.stringify(report, null, 2), "application/json"); });
  footer.append(compareButton, exportButton); $("demo-result").replaceChildren(summary, mission, workspace, evidence, footer);
}
function metricRow(label, value) { const row = element("div", "metric-row"); row.append(element("span", "", label), element("strong", "", value)); return row; }
function summarizeDiff(diff) { const parts = []; if (diff.updated?.length) parts.push(`${diff.updated.length} updated`); if (diff.added?.length) parts.push(`${diff.added.length} added`); if (diff.removed?.length) parts.push(`${diff.removed.length} removed`); if (diff.text_changed) parts.push("page text changed"); return parts.join(" · ") || "State changed"; }
function evidenceCard(label, value, description, passed) { const card = element("article", `evidence-card ${passed ? "passed" : "failed"}`); card.append(element("span", "session-kicker", label.toUpperCase()), element("strong", "", value), element("p", "", description || "No additional evidence.")); return card; }
api("/api/info").then(info => {
  recipes = info.recipes; serverEntry = info.server;
  $("home-recipes").replaceChildren(...recipes.map(recipeCard)); showRecipes(); connectionConfig();
}).catch(error => {
  toast("Could not reach the local runtime. " + error.message);
  $("home-recipes").append(element("p", "error-message", "Start mcp-vision studio, then reload this page."));
});
