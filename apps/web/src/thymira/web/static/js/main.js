// Console bootstrap. It owns the shell and nothing else: the theme and the saved shell
// preferences, the sidebar, the command palette, the keyboard bindings, the API health light and
// the offline banner, the connect dialog, and hash routing between the four views.
//
//   #/                          home: start a thread
//   #/new                       alias of #/ (old links keep working)
//   #/runs/<run_id>[/<panel>[/<item>]]
//   #/project                   the workspace this API serves
//   #/settings                  model routing, appearance, connection
//
// The console asks for a token only when the API answers 401, so it never assumes how the API
// authenticates; it only relays what the operator provides. It holds no Run state: every view is a
// fold over what the API answered.

import { api, clearToken, getToken, setToken } from "./api.js";
import { h, replace } from "./dom.js";
import { headline, shortId } from "./format.js";
import { CommandPalette } from "./palette.js";
import { readPref, writePref } from "./prefs.js";
import { navigate, parseRoute } from "./router.js";
import { installShortcuts } from "./shortcuts.js";
import { applyTheme, currentTheme, cycleTheme } from "./theme.js";
import { toast } from "./toast.js";
import { iconButton, kbd } from "./ui.js";
import { HomeView } from "./views/home.js";
import { ProjectView } from "./views/project.js";
import { SettingsView } from "./views/settings.js";
import { Sidebar } from "./views/sidebar.js";
import { ThreadView } from "./views/thread.js";

const HEALTH_MS = 15000;
const SIDEBAR_MIN = 220;
const SIDEBAR_MAX = 420;
const INSPECTOR_MIN = 320;
const OFFLINE_TEXT = "The API is not reachable · retrying";
const PALETTE_THREAD_LIMIT = 40;

const SHORTCUTS = [
  ["Ctrl+K", "Command palette: actions and threads"],
  ["Ctrl+B", "Show or hide the thread list"],
  ["Ctrl+I", "Show or hide the inspector"],
  ["Enter", "Send from the composer"],
  ["Shift+Enter", "New line in the composer"],
  ["Esc", "Close the palette, the drawer or the inspector"],
  ["?", "This sheet"],
];

const shell = document.getElementById("shell");
const mainRoot = document.getElementById("main");
const inspectorRoot = document.getElementById("inspector");
const scrim = document.getElementById("scrim");
const banner = document.getElementById("banner");
const paletteHost = document.getElementById("palette");
const confirmHost = document.getElementById("confirm");
const connect = document.getElementById("connect");
const connectForm = document.getElementById("connect-form");
const tokenInput = document.getElementById("token");
const connectError = document.getElementById("connect-error");
const connectCancel = document.getElementById("connect-cancel");

const narrow = window.matchMedia("(max-width: 899px)");
const overlayInspector = window.matchMedia("(max-width: 1279px)");

let view = null;
let project = null;
let projectLoaded = false;
let dismissed = false;
let online = null;

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), Math.max(low, high));
}

// The two widths arrive as inline custom properties on #shell; layout.css reads them through
// --col-1/--col-3 so an attribute rule (collapsed, closed) still wins over the inline value.
function applyShellPrefs() {
  const collapsed = readPref("sidebar", "open") === "collapsed";
  shell.dataset.sidebar = collapsed ? "collapsed" : "open";
  const sidebarWidth = clamp(Number(readPref("sidebarWidth", 280)) || 280, SIDEBAR_MIN, SIDEBAR_MAX);
  const inspectorWidth = clamp(
    Number(readPref("inspectorWidth", 420)) || 420,
    INSPECTOR_MIN,
    Math.round(window.innerWidth * 0.6),
  );
  shell.style.setProperty("--sidebar-width", `${sidebarWidth}px`);
  shell.style.setProperty("--inspector-width", `${inspectorWidth}px`);
  return collapsed;
}

// ---------------------------------------------------------------- shell chrome
const menuButton = iconButton("sidebar", {
  label: "Show the thread list",
  kind: "ghost",
  onClick: () => toggleSidebar(),
});
menuButton.classList.add("menu-button");
Object.assign(menuButton.style, { alignSelf: "flex-start", margin: "8px 0 0 8px" });
mainRoot.append(menuButton);

function syncMenuButton() {
  menuButton.hidden = !narrow.matches;
}

function closeDrawer() {
  if (shell.dataset.sidebar !== "drawer-open") return;
  shell.dataset.sidebar = readPref("sidebar", "open") === "collapsed" ? "collapsed" : "open";
  scrim.hidden = true;
}

function toggleSidebar() {
  if (narrow.matches) {
    const open = shell.dataset.sidebar === "drawer-open";
    if (open) closeDrawer();
    else {
      shell.dataset.sidebar = "drawer-open";
      scrim.hidden = false;
    }
    return;
  }
  const collapsed = shell.dataset.sidebar !== "collapsed";
  shell.dataset.sidebar = collapsed ? "collapsed" : "open";
  writePref("sidebar", collapsed ? "collapsed" : "open");
  sidebar.setCollapsed(collapsed);
}

// The inspector belongs to the thread view, so the shell asks for it through the route rather than
// reaching into it: the panel in the hash is what opens it.
function toggleInspector() {
  if (!(view instanceof ThreadView)) {
    toast("The inspector opens on a thread.");
    return;
  }
  const current = parseRoute(window.location.hash);
  const opening = shell.dataset.inspector !== "open" || !current.panel;
  const panel = opening ? readPref("inspectorPanel", "details") || "details" : null;
  navigate({ view: "thread", runId: view.runId, panel, item: null });
}

function onEscape() {
  if (palette.isOpen) {
    palette.close();
    return;
  }
  if (shell.dataset.sidebar === "drawer-open") {
    closeDrawer();
    return;
  }
  if (shell.dataset.inspector === "open" && overlayInspector.matches && view instanceof ThreadView) {
    navigate({ view: "thread", runId: view.runId, panel: null, item: null });
  }
}

// A sheet, not a confirmation: it lists the bindings and closes. It borrows the shell's single
// <dialog> the same way confirmDialog() does, and leaves it empty again afterwards.
function openShortcuts() {
  if (!confirmHost || confirmHost.open) return;
  const close = h("button", { class: "btn btn-primary", type: "submit" }, "Close");
  const rows = SHORTCUTS.flatMap(([keys, what]) => [kbd(keys), h("span", null, what)]);
  replace(
    confirmHost,
    h(
      "form",
      { method: "dialog" },
      h("h2", { id: "confirm-title" }, "Keyboard shortcuts"),
      h("div", { class: "shortcut-list" }, rows),
      h("div", { class: "dialog-actions" }, close),
    ),
  );
  confirmHost.addEventListener("close", () => replace(confirmHost), { once: true });
  confirmHost.showModal();
  close.focus();
}

// ---------------------------------------------------------------- views
const sidebar = new Sidebar(document.getElementById("sidebar"), {
  onNewThread: () => navigate({ view: "home" }),
  onToggleCollapse: () => toggleSidebar(),
  onConnect: () => openConnect(),
  onDisconnect: () => disconnect(),
  hasToken: () => Boolean(getToken()),
});

const palette = new CommandPalette(paletteHost, { commands: () => commands() });

function commands() {
  const actions = [
    { id: "new", label: "New thread", hint: "#/", group: "Actions", run: () => navigate({ view: "home" }) },
    { id: "project", label: "Go to project", hint: "#/project", group: "Actions", run: () => navigate({ view: "project" }) },
    { id: "settings", label: "Settings", hint: "#/settings", group: "Actions", run: () => navigate({ view: "settings" }) },
    {
      id: "theme",
      label: "Toggle theme",
      hint: currentTheme(),
      group: "Actions",
      run: () => {
        const next = cycleTheme();
        sidebar.syncTheme();
        toast(`Theme: ${next}`);
      },
    },
    { id: "sidebar", label: "Toggle sidebar", hint: "Ctrl+B", group: "Actions", run: () => toggleSidebar() },
    { id: "inspector", label: "Toggle inspector", hint: "Ctrl+I", group: "Actions", run: () => toggleInspector() },
    { id: "refresh", label: "Refresh threads", group: "Actions", run: () => sidebar.refresh() },
  ];
  const threads = sidebar.runs.slice(0, PALETTE_THREAD_LIMIT).map((run) => ({
    id: run.id,
    label: headline(run.prompt, 90),
    hint: shortId(run.id),
    group: "Threads",
    run: () => navigate({ view: "thread", runId: run.id }),
  }));
  return [...actions, ...threads];
}

// Every view lives in its own .view child of #main, so the rise animation replays on each switch
// and the previous view's nodes are gone before the next one builds.
function mount({ document: isDocument = false } = {}) {
  for (const child of [...mainRoot.children]) {
    if (child !== menuButton) child.remove();
  }
  const node = h("div", { class: ["view", isDocument ? "is-document" : null], dataset: { enter: "true" } });
  mainRoot.append(node);
  return node;
}

function replaceView(options, build) {
  view?.dispose?.();
  view = null;
  view = build(mount(options));
}

function closeInspector() {
  shell.dataset.inspector = "closed";
  inspectorRoot.hidden = true;
}

function openCreatedRun(run) {
  if (!run?.id) return;
  sidebar.upsert(run);
  toast("Thread started", { tone: "ok" });
  navigate({ view: "thread", runId: run.id });
}

const threadHooks = {
  onRunChanged: (run) => sidebar.upsert(run),
  onRunCreated: (run) => openCreatedRun(run),
  onInspectorChange: (panel) => {
    writePref("inspectorPanel", panel ?? undefined);
    const runId = view?.runId;
    if (!runId) return;
    const current = parseRoute(window.location.hash);
    if (current.view === "thread" && current.runId === runId && current.panel === panel) return;
    navigate({ view: "thread", runId, panel, item: null });
  },
};

function route() {
  const target = parseRoute(window.location.hash);
  closeDrawer();
  if (target.view === "thread" && target.runId) {
    sidebar.select(target.runId);
    if (view instanceof ThreadView && view.runId === target.runId) {
      view.setRoute(target.panel, target.item);
      return;
    }
    replaceView({}, (root) => new ThreadView(root, inspectorRoot, target.runId, target.panel, target.item, threadHooks));
    return;
  }
  sidebar.select(null);
  closeInspector();
  if (target.view === "project") {
    replaceView({ document: true }, (root) => new ProjectView(root, { project: () => project }));
    return;
  }
  if (target.view === "settings") {
    replaceView({ document: true }, (root) =>
      new SettingsView(root, {
        onConnect: () => openConnect(),
        onDisconnect: () => disconnect(),
        hasToken: () => Boolean(getToken()),
      }),
    );
    return;
  }
  replaceView({}, (root) =>
    new HomeView(root, {
      onCreated: (run) => openCreatedRun(run),
      recent: () => sidebar.runs,
      project: () => project,
    }),
  );
}

// ---------------------------------------------------------------- connection
function openConnect(message) {
  connectError.textContent = message ?? "";
  connectError.hidden = !message;
  tokenInput.value = "";
  if (!connect.open) connect.showModal();
  tokenInput.focus();
}

function syncConnection() {
  sidebar.syncConnection();
  if (view instanceof SettingsView) view.syncConnection();
}

function disconnect() {
  clearToken();
  project = null;
  projectLoaded = false;
  sidebar.setProject(null);
  syncConnection();
  sidebar.refresh();
  toast("Disconnected. The token is gone from this tab.");
}

connectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = tokenInput.value.trim();
  if (!token) {
    openConnect("Paste the API token first.");
    return;
  }
  setToken(token);
  tokenInput.value = "";
  try {
    await api.listRuns({ limit: 1 });
    dismissed = false;
    connect.close();
    syncConnection();
    view?.dispose?.();
    view = null;
    route();
    sidebar.refresh().then(() => refreshRecent());
    loadProject();
  } catch (error) {
    clearToken();
    syncConnection();
    openConnect(
      error?.status === 401
        ? "The API did not accept that token."
        : (error?.message ?? "The API could not be reached."),
    );
  }
});

connectCancel.addEventListener("click", () => {
  dismissed = true;
  connect.close();
});

window.addEventListener("thymira:unauthorized", () => {
  const hadToken = Boolean(getToken());
  clearToken();
  syncConnection();
  if (connect.open || (dismissed && !hadToken)) return;
  openConnect(hadToken ? "The API rejected the token. Paste the current one." : null);
});

// ---------------------------------------------------------------- health and project
async function pollHealth() {
  const up = await api.health();
  if (up !== online) {
    online = up;
    sidebar.setApi(up);
    banner.hidden = up;
  }
  if (up && !projectLoaded) loadProject();
}

// GET /project is a read-only description of the workspace. An older API without the route answers
// 404: the shell then calls the project "Workspace" and everything else keeps working.
async function loadProject() {
  if (projectLoaded) return;
  try {
    project = await api.project();
    projectLoaded = true;
  } catch (error) {
    if (error?.status === 404) {
      project = null;
      projectLoaded = true;
    } else {
      return;
    }
  }
  sidebar.setProject(project);
  view?.setProject?.(project);
}

function refreshRecent() {
  view?.refreshRecent?.();
}

// ---------------------------------------------------------------- start
applyTheme(currentTheme());
const startedCollapsed = applyShellPrefs();
sidebar.setCollapsed(startedCollapsed);
sidebar.syncTheme();
sidebar.setApi(null);
closeInspector();
banner.textContent = OFFLINE_TEXT;
syncMenuButton();

scrim.addEventListener("click", () => closeDrawer());
narrow.addEventListener("change", () => {
  syncMenuButton();
  closeDrawer();
});

installShortcuts([
  { keys: "ctrl+k", whenTyping: true, run: () => palette.toggle() },
  { keys: "ctrl+b", run: () => toggleSidebar() },
  { keys: "ctrl+i", run: () => toggleInspector() },
  { keys: "?", run: () => openShortcuts() },
  { keys: "escape", whenTyping: true, run: () => onEscape() },
]);

window.addEventListener("hashchange", () => route());
route();
sidebar.refresh().then(() => refreshRecent());
pollHealth();
setInterval(() => {
  if (!document.hidden) pollHealth();
}, HEALTH_MS);
