// Console entry point: the connect dialog, the API health light and fragment-based routing.
//   #/                          home
//   #/new                       create a Run
//   #/runs/<run_id>[/<tab>[/<item>]]
//   #/settings                  live model routing configuration
//
// The console asks for a token only when the API answers 401, so it never assumes how the API
// authenticates; it only relays what the operator provides.

import { api, clearToken, getToken, setToken } from "./api.js";
import { HomeView } from "./views/home.js";
import { NewRunView } from "./views/new-run.js";
import { RunRail } from "./views/rail.js";
import { RunView } from "./views/run.js";
import { SessionRail } from "./views/sessions.js";
import { SessionView } from "./views/session.js";
import { ProjectExplorerView } from "./views/explorer.js";
import { SettingsView } from "./views/settings.js";
import { h, replace } from "./dom.js";

const HEALTH_MS = 15000;

const main = document.getElementById("main");
const dialog = document.getElementById("connect");
const form = document.getElementById("connect-form");
const tokenInput = document.getElementById("token");
const connectError = document.getElementById("connect-error");
const cancelButton = document.getElementById("connect-cancel");
const lockButton = document.getElementById("lock");
const apiState = document.getElementById("api-state");
const shell = document.getElementById("shell");
const inspector = document.getElementById("inspector");
const sessionRailRoot = document.getElementById("session-rail");
const runRailRoot = document.getElementById("run-rail");

const rail = new RunRail(runRailRoot);
const sessionRail = new SessionRail(sessionRailRoot, {
  onCreated: (session) => {
    window.location.hash = `#/sessions/${encodeURIComponent(session.id)}`;
  },
});
let view = null;
let dismissed = false;

function routeParts() {
  return window.location.hash
    .replace(/^#\/?/, "")
    .split("/")
    .filter(Boolean)
    .map((part) => decodeURIComponent(part));
}

function show(next) {
  view?.dispose?.();
  view = next;
}

function route() {
  const [section, id, subroute, subrouteId] = routeParts();
  const sessionsPage = !section || section === "sessions";
  const wasSessionsPage = !sessionRailRoot.hidden;
  sessionRailRoot.hidden = !sessionsPage;
  runRailRoot.hidden = sessionsPage;
  if (wasSessionsPage !== sessionsPage) refreshVisibleRail();
  inspector.hidden = true;
  shell.dataset.inspector = "closed";

  if (sessionsPage) {
    rail.select(null);
    sessionRail.select(section === "sessions" ? id : null);
    if (!id) {
      show({ dispose() {} });
      replace(
        main,
        h("section", { class: "home sessions-home" },
          h("p", { class: "eyebrow" }, "Thymira workspace"),
          h("h1", null, "Conversations that continue across runs."),
          h("p", { class: "lede" }, "Ask about recorded session evidence at any time, including while a run is active. Auto answers from that evidence when possible and starts governed work when needed; Request work starts governed work directly. Work stays linked to its events, approvals, interview, artifacts and audit."),
          h("div", { class: "home-actions" }, h("a", { class: "btn btn-primary", href: "#/runs" }, "Open runs")),
          h("p", { class: "muted small" }, "Choose a conversation from the sidebar, or create a new session to begin."),
        ),
      );
      return;
    }
    shell.dataset.inspector = "open";
    inspector.hidden = false;
    const panel = ["artifacts", "datasets"].includes(subroute) ? subroute : "files";
    if (view instanceof SessionView && view.sessionId === id) {
      view.updateRoute(panel, panel === "artifacts" ? subrouteId : null);
      return;
    }
    show(new SessionView(main, inspector, id, {
      onSessionChanged: (session, turns) => sessionRail.upsert(session, turns),
    }));
    view.updateRoute(panel, panel === "artifacts" ? subrouteId : null);
    return;
  }

  if (section === "project") {
    sessionRail.select(null);
    rail.select(null);
    show(new ProjectExplorerView(main));
    return;
  }

  if (section === "runs" && id) {
    sessionRail.select(null);
    rail.select(id);
    if (view instanceof RunView && view.runId === id) {
      view.setTab(subroute, subrouteId);
      return;
    }
    show(
      new RunView(main, id, subroute, subrouteId, {
        onRunChanged: (run) => rail.upsert(run),
        onRunCreated: (run) => {
          rail.upsert(run);
          window.location.hash = `#/runs/${encodeURIComponent(run.id)}`;
        },
      }),
    );
    return;
  }
  rail.select(null);
  sessionRail.select(null);
  if (section === "runs") {
    show(new HomeView(main));
    return;
  }
  if (section === "new") {
    show(
      new NewRunView(main, {
        onCreated: (run) => {
          rail.upsert(run);
          window.location.hash = `#/runs/${encodeURIComponent(run.id)}`;
        },
      }),
    );
    return;
  }
  if (section === "settings") {
    show(new SettingsView(main));
    return;
  }
  show(new HomeView(main));
}

function syncLock() {
  lockButton.textContent = getToken() ? "Disconnect" : "Connect";
}

function refreshVisibleRail() {
  if (sessionRailRoot.hidden) rail.refresh();
  else sessionRail.refresh();
}

function openConnect(message) {
  connectError.textContent = message ?? "";
  connectError.hidden = !message;
  tokenInput.value = "";
  if (!dialog.open) dialog.showModal();
  tokenInput.focus();
}

form.addEventListener("submit", async (event) => {
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
    dialog.close();
    syncLock();
    show(null);
    route();
    refreshVisibleRail();
  } catch (error) {
    clearToken();
    syncLock();
    openConnect(error?.status === 401 ? "The API did not accept that token." : (error?.message ?? "The API could not be reached."));
  }
});

cancelButton.addEventListener("click", () => {
  dismissed = true;
  dialog.close();
});

lockButton.addEventListener("click", () => {
  if (getToken()) {
    clearToken();
    syncLock();
    refreshVisibleRail();
  }
  openConnect();
});

window.addEventListener("thymira:unauthorized", () => {
  const hadToken = Boolean(getToken());
  clearToken();
  syncLock();
  if (dialog.open || (dismissed && !hadToken)) return;
  openConnect(hadToken ? "The API rejected the token. Paste the current one." : null);
});

async function pollHealth() {
  const online = await api.health();
  apiState.dataset.state = online ? "online" : "offline";
  apiState.textContent = online ? "API online" : "API offline";
}

window.addEventListener("hashchange", route);
syncLock();
route();
refreshVisibleRail();
pollHealth();
setInterval(() => {
  if (!document.hidden) pollHealth();
}, HEALTH_MS);
