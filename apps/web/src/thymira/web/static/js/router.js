// Hash routing for the console shell. Pure except for `navigate`, which is the only place that
// writes `window.location.hash`; nothing here reads Run state or renders anything.

export const PANELS = [
  "details",
  "plan",
  "agents",
  "reviews",
  "tools",
  "artifacts",
  "audit",
  "events",
  "usage",
  "inputs",
  "interview",
];

// The console's old tab names keep working: a bookmarked #/runs/<id>/overview still opens.
export const PANEL_ALIASES = { overview: "details", approvals: "reviews" };

const VIEWS = { project: "project", settings: "settings" };
const HOME = () => ({ view: "home", runId: null, panel: null, item: null });

// A malformed percent sequence is shown as it was typed rather than throwing the whole route away.
function decode(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

function panelId(segment) {
  if (!segment) return null;
  const name = PANEL_ALIASES[segment] ?? segment;
  return PANELS.includes(name) ? name : PANELS[0];
}

export function parseRoute(hash) {
  const path = String(hash ?? "").replace(/^#/, "").replace(/^\/+/, "");
  const segments = path.split("/").filter((segment) => segment !== "").map(decode);
  if (!segments.length || segments[0] === "new") return HOME();
  const [head, ...rest] = segments;
  if (head === "runs" && rest.length) {
    return {
      view: "thread",
      runId: rest[0],
      panel: panelId(rest[1] ?? null),
      item: rest[2] ?? null,
    };
  }
  if (VIEWS[head]) return { view: VIEWS[head], runId: null, panel: null, item: null };
  return HOME();
}

export function href(route) {
  if (typeof route === "string") return route.startsWith("#") ? route : `#${route}`;
  const encode = encodeURIComponent;
  if (route?.view === "thread" && route.runId) {
    const parts = ["runs", encode(route.runId)];
    if (route.panel) parts.push(encode(route.panel));
    if (route.panel && route.item) parts.push(encode(route.item));
    return `#/${parts.join("/")}`;
  }
  if (route?.view === "project" || route?.view === "settings") return `#/${route.view}`;
  return "#/";
}

export function navigate(route) {
  const target = href(route);
  if (window.location.hash === target) return;
  window.location.hash = target;
}
