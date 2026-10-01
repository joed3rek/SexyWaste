// Shared UI shell: icon set, navigation rail and the mobile bottom-sheet handle.
// Load before the page script. Pages opt in with <nav id="appNav"> and <span data-icon="…">.

const ICONS = {
  logo: '<path d="M7 7h10l-1 13H8z"/><path d="M5 7h14M10 4h4"/><path d="M10.5 11.5c1.6-.3 3 .6 3 2.2 0 1.4-1.2 2.3-2.6 2.3"/>',
  home: '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
  survey: '<rect x="5" y="4.5" width="14" height="16" rx="2"/><path d="M9.5 3h5v3h-5z"/><path d="m9 13 2 2 4-4"/>',
  planner: '<path d="m12 4 8.5 4.5L12 13 3.5 8.5z"/><path d="m3.5 12.5 8.5 4.5 8.5-4.5"/><path d="m3.5 16.5 8.5 4.5 8.5-4.5" opacity=".5"/>',
  routes: '<circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8.2 18H16a3 3 0 0 0 0-6H8a3 3 0 0 1 0-6h7.8"/>',
  rules: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5z"/><path d="M5 20.5A2.5 2.5 0 0 0 7.5 21H19v-3M9 7.5h6"/>',
  household: '<path d="M4 11 12 4.5l8 6.5"/><path d="M6 9.5V20h12V9.5"/><path d="M10 20v-5h4v5"/>',
  recycle: '<path d="M19.5 11A7.5 7.5 0 0 0 6 6.6L4.5 8"/><path d="M4.5 4v4h4"/><path d="M4.5 13A7.5 7.5 0 0 0 18 17.4l1.5-1.4"/><path d="M19.5 20v-4h-4"/>',
  ward: '<path d="M4 20V10M10 20V4M16 20v-7M20 20H3"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}

// Where the full app (frontend + Python API) is hosted. GitHub Pages serves only the static
// frontend, so its data pages forward here (see renderShell).
const FULL_APP_URL = "https://swm-urban-waste.onrender.com/";

const NAV = [
  { key: "home", label: "Home", href: "index.html", icon: "home" },
  { key: "surveyor", label: "Survey", href: "map.html?role=surveyor", icon: "survey" },
  { key: "planner", label: "Planner", href: "map.html?role=planner", icon: "planner" },
  { key: "builder", label: "Routes", href: "builder.html", icon: "routes" },
  { key: "rules", label: "Rules", href: "rules.html", icon: "rules" },
];

// JSON request to the app's API with a readable error. Without the Python server (for example on
// GitHub Pages) the host answers with an HTML page instead of JSON.
// ---------- Sign-in (dummy login) ----------
// The chosen role, typed name and sectors live in this browser only. Nothing is verified.

const SESSION_KEY = "swm.session";

function getSession() {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY)) || null;
  } catch {
    return null;
  }
}

function setSession(session) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    /* storage blocked: the session lasts until the page closes */
  }
}

function signOut() {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    /* ignore */
  }
  location.href = "index.html";
}

function sessionHeaders() {
  const s = getSession();
  return s ? { "X-SWM-Role": s.role, "X-SWM-User": encodeURIComponent(s.name || ""),
                "X-SWM-Sectors": (s.sectors || []).map(encodeURIComponent).join(",") } : {};
}

async function apiFetch(path, options = {}) {
  let res;
  try {
    res = await fetch(path, { ...options, headers: { ...sessionHeaders(), ...(options.headers || {}) } });
  } catch {
    throw new Error("Cannot reach the data server. Check your connection and try again.");
  }
  const type = res.headers.get("content-type") || "";
  if (!type.includes("application/json")) {
    throw new Error(FULL_APP_URL
      ? `This copy of the site has no data server. Open the full app: ${FULL_APP_URL}`
      : "This copy of the site has no data server, so maps and data cannot load here. Run the app server (see README).");
  }
  const body = await res.json();
  if (!res.ok) throw new Error(`${res.status}: ${typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body).slice(0, 300)}`);
  return body;
}

// fitBounds padding that keeps features clear of the floating panel
// (left of the map on desktop, a bottom sheet on phones).
function mapPadding(map, extra = 20) {
  const panel = document.getElementById("panel")?.getBoundingClientRect();
  const box = map.getContainer().getBoundingClientRect();
  // MapLibre refuses to fit when padding exceeds the canvas, so leave at least a quarter of it free.
  const e = Math.min(extra, box.width / 8, box.height / 8);
  const pad = { top: e, right: e, bottom: e, left: e };
  if (!panel) return pad;
  if (innerWidth <= 760) pad.bottom = Math.min(e + Math.max(0, box.bottom - panel.top), box.height * 0.75 - e);
  else pad.left = Math.min(e + Math.max(0, panel.right - box.left), box.width * 0.75 - e);
  return pad;
}

function activeNavKey() {
  const page = location.pathname.split("/").pop() || "index.html";
  if (page.startsWith("map")) return new URLSearchParams(location.search).get("role") === "planner" ? "planner" : "surveyor";
  if (page.startsWith("builder")) return "builder";
  if (page.startsWith("rules")) return "rules";
  return "home";
}

(function renderShell() {
  document.querySelectorAll("[data-icon]").forEach((el) => (el.innerHTML = icon(el.dataset.icon)));

  const nav = document.getElementById("appNav");
  if (nav) {
    const active = activeNavKey();
    nav.className = "rail";
    nav.setAttribute("aria-label", "Main");
    nav.innerHTML = `<a class="logo" href="index.html" title="Urban Waste Intelligence">${icon("logo")}</a>` +
      NAV.map((n) => `<a class="item${n.key === active ? " active" : ""}" href="${n.href}"${n.key === active ? ' aria-current="page"' : ""}>${icon(n.icon)}<span>${n.label}</span></a>`).join("");
  }

  if (location.hostname.endsWith("github.io")) {
    // Every page needs the API (sign-in reads the role list), so open the full app instead.
    if (FULL_APP_URL) {
      location.replace(FULL_APP_URL + location.pathname.split("/").pop() + location.search);
      return;
    }
    const note = document.createElement("div");
    note.className = "preview-banner";
    note.innerHTML = FULL_APP_URL
      ? `Design preview. Maps and data run on the <a href="${FULL_APP_URL}">full app</a>.`
      : "Design preview. Maps and data need the app server: run it locally (see README).";
    document.body.append(note);
  }

  // On phones the panel is a bottom sheet; the handle collapses it to show more map.
  const panel = document.getElementById("panel");
  if (panel) {
    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = "sheet-handle";
    handle.setAttribute("aria-label", "Collapse or expand panel");
    handle.addEventListener("click", () => panel.classList.toggle("collapsed"));
    panel.prepend(handle);
  }
})();
