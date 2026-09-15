let lastPointer = null;

function boundsOf(element) {
  if (!element || !element.getBoundingClientRect) return null;
  const r = element.getBoundingClientRect();
  return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) };
}

function describe(element) {
  if (!element || element.nodeType !== Node.ELEMENT_NODE) return null;
  const safe = {};
  for (const name of ["id", "role", "aria-label", "title", "alt", "placeholder", "type", "href"]) {
    const value = element.getAttribute(name);
    if (value) safe[name] = value.slice(0, 500);
  }
  const labels = element.labels ? Array.from(element.labels).map((label) => label.innerText || "").join(" ") : "";
  const name = (element.getAttribute("aria-label") || labels || element.getAttribute("title") ||
    element.getAttribute("alt") || element.getAttribute("placeholder") || element.innerText || "")
    .replace(/\s+/g, " ").trim().slice(0, 1000);
  return {
    role: element.getAttribute("role") || element.tagName.toLowerCase(),
    name,
    tag: element.tagName.toLowerCase(),
    attributes: safe,
    bounds: boundsOf(element),
  };
}

function nearby(element) {
  if (!element) return {};
  const region = element.closest("article,main,section,form,dialog,li,tr") || element.parentElement || element;
  const text = (region.innerText || "").replace(/\s+/g, " ").trim().slice(0, 5000);
  const controls = Array.from(region.querySelectorAll("a,button,input,textarea,select,[role]"))
    .slice(0, 24).map(describe).filter(Boolean);
  return { text, controls };
}

document.addEventListener("contextmenu", (event) => {
  lastPointer = { target: event.target, x: event.clientX, y: event.clientY };
}, true);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message !== "capture-context") return;
  const clicked = lastPointer && lastPointer.target && document.contains(lastPointer.target) ? lastPointer.target : null;
  const focused = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
  const selection = String(window.getSelection() || "").slice(0, 12000);
  const target = clicked || focused;
  sendResponse({
    source: "chrome",
    source_application: "Google Chrome",
    url: location.href,
    title: document.title,
    selected_text: selection,
    focused_element: describe(focused),
    clicked_element: describe(clicked),
    element_bounds: boundsOf(target),
    dom_context: { ...nearby(target), coordinate_space: "viewport-css" },
    cursor_position: lastPointer ? { x: lastPointer.x, y: lastPointer.y } : null,
    viewport: { x: Math.round(window.scrollX), y: Math.round(window.scrollY), width: window.innerWidth, height: window.innerHeight },
    session: { authenticated: null, session_kind: "existing-chrome-profile" },
    identity: { status: "unknown" },
  });
  lastPointer = null;
});
