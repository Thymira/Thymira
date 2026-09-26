// The sidebar: the project this API serves, and every Run in it as a thread, grouped by the day it
// started. Search and the filter pills are client-side over the list already loaded, so changing
// them costs the API nothing.
//
// The list is fetched on first load, when the operator asks for it, and when a thread the console
// is watching changes (upsert). It is never polled: listing Runs makes the API verify every Run's
// hash-chained event log to report its status, so a timer would keep the API busy for nothing.
//
// Nothing here carries authority: a row is a link, and every value reaches the page as a text node.

import { listAllRuns } from "../api.js";
import { h, replace } from "../dom.js";
import { clock, headline, integer, relative, shortId } from "../format.js";
import { icon } from "../icons.js";
import { href } from "../router.js";
import { TERMINAL_STATUSES, decisionLabel, statusLabel, statusTone } from "../status.js";
import { cycleTheme, currentTheme, themeIcon } from "../theme.js";
import { toast } from "../toast.js";
import { iconButton } from "../ui.js";

// The statuses that mean the runtime still holds the thread: their dot breathes.
const ACTIVE_STATUSES = new Set(["CREATED", "PLANNING", "RUNNING", "EXPERIMENTING", "AUDITING"]);

// "Needs you" is the only filter that maps to a single wire status; the wire names stay in the
// meta line, so nothing here renames RunStatus.
const FILTERS = [
  ["all", "All", () => true],
  ["attention", "Needs you", (run) => run.status === "WAITING_FOR_APPROVAL"],
  ["running", "Running", (run) => ACTIVE_STATUSES.has(run.status)],
  ["done", "Done", (run) => TERMINAL_STATUSES.has(run.status)],
];

const DAY_MS = 86400000;
const GROUP_ORDER = ["Today", "Yesterday", "This week", "Older"];
const LOADING_MESSAGE =
  "Loading runs. The API verifies each run's event log to report its current status, so a long history takes a while.";

function newestFirst(runs) {
  return [...runs].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)));
}

// Day buckets are computed against the operator's local midnight, not UTC, so "Today" means the
// day they are having.
function groupOf(iso) {
  const time = Date.parse(iso ?? "");
  if (Number.isNaN(time)) return "Older";
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (time >= midnight) return "Today";
  if (time >= midnight - DAY_MS) return "Yesterday";
  if (time >= midnight - 6 * DAY_MS) return "This week";
  return "Older";
}

function matches(run, query) {
  if (!query) return true;
  const needle = query.toLowerCase();
  return (
    String(run.prompt ?? "").toLowerCase().includes(needle) || String(run.id ?? "").toLowerCase().includes(needle)
  );
}

export class Sidebar {
  constructor(root, { onNewThread, onToggleCollapse, onConnect, onDisconnect, hasToken = () => false } = {}) {
    this.runs = [];
    this.selected = null;
    this.query = "";
    this.filter = "all";
    this.busy = false;
    this.hasToken = hasToken;
    this.onConnect = onConnect;
    this.onDisconnect = onDisconnect;

    this.projectName = h("span", { class: "project-name" }, "Workspace");
    this.projectMeta = h("span", { class: "project-meta" }, "reading the project…");
    this.projectDot = h("i", { class: "project-dot", dataset: { state: "unknown" }, "aria-hidden": "true" });
    this.message = h("p", { class: "sidebar-message" });
    this.groups = h("div", { class: "thread-groups" });

    this.search = h("input", {
      class: "input",
      type: "search",
      placeholder: "Search threads",
      "aria-label": "Search threads",
      autocomplete: "off",
      spellcheck: "false",
      onInput: (event) => {
        this.query = event.target.value.trim();
        this.render();
      },
    });

    this.pills = FILTERS.map(([id, label]) =>
      h(
        "button",
        {
          class: "pill",
          type: "button",
          "aria-pressed": id === this.filter ? "true" : "false",
          onClick: () => {
            this.filter = id;
            this.render();
          },
        },
        label,
      ),
    );

    this.refreshButton = iconButton("refresh", {
      label: "Reload the thread list",
      kind: "ghost",
      onClick: () => this.refresh(),
    });
    this.connectButton = h("button", {
      class: "btn btn-ghost btn-small",
      type: "button",
      onClick: () => (this.hasToken() ? this.onDisconnect?.() : this.onConnect?.()),
    });
    this.themeButton = iconButton(themeIcon(), {
      label: "Change the theme",
      kind: "ghost",
      onClick: () => {
        const next = cycleTheme();
        this.syncTheme();
        toast(`Theme: ${next}`);
      },
    });
    this.collapseButton = iconButton("sidebar", {
      label: "Collapse the sidebar",
      kind: "ghost",
      onClick: () => onToggleCollapse?.(),
    });
    this.collapseButton.classList.add("sidebar-collapse");

    replace(
      root,
      this.rail({ onNewThread, onToggleCollapse }),
      h(
        "div",
        { class: "sidebar-brand" },
        h(
          "span",
          { class: "wordmark" },
          h("span", { class: "wordmark-glyph", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i")),
          h(
            "span",
            { class: "wordmark-name" },
            h("span", { class: "wordmark-thy" }, "THY"),
            h("span", { class: "wordmark-mira" }, "MIRA"),
          ),
        ),
        this.collapseButton,
      ),
      h(
        "a",
        { class: "project-card", href: href({ view: "project" }), title: "Open the project" },
        this.projectDot,
        this.projectName,
        this.projectMeta,
      ),
      h(
        "div",
        { class: "sidebar-new" },
        h(
          "button",
          { class: "btn btn-primary btn-block", type: "button", onClick: () => onNewThread?.() },
          icon("plus", { size: 15 }),
          "New thread",
        ),
      ),
      h("div", { class: "sidebar-search" }, this.search),
      h("div", { class: "filter-pills", role: "group", "aria-label": "Filter threads" }, this.pills),
      this.message,
      this.groups,
      h(
        "div",
        { class: "sidebar-footer" },
        this.refreshButton,
        h("span", { class: "spacer" }),
        this.connectButton,
        h(
          "a",
          { class: "btn btn-ghost btn-icon", href: href({ view: "settings" }), title: "Settings", "aria-label": "Settings" },
          icon("settings", { size: 16 }),
        ),
        this.themeButton,
      ),
    );
    this.syncConnection();
  }

  // The 56px rail the shell shows instead of the sidebar when it is collapsed. Its first button
  // brings the sidebar back; the rest are the destinations that do not need the list.
  rail({ onNewThread, onToggleCollapse }) {
    const link = (name, label, route) =>
      h(
        "a",
        { class: "btn btn-ghost btn-icon", href: href(route), title: label, "aria-label": label },
        icon(name, { size: 16 }),
      );
    return h(
      "div",
      { class: "icon-rail" },
      iconButton("sidebar", { label: "Expand the sidebar", kind: "ghost", onClick: () => onToggleCollapse?.() }),
      iconButton("plus", { label: "New thread", kind: "ghost", onClick: () => onNewThread?.() }),
      iconButton("thread", { label: "Threads", kind: "ghost", onClick: () => onToggleCollapse?.() }),
      link("database", "Project", { view: "project" }),
      link("settings", "Settings", { view: "settings" }),
    );
  }

  async refresh() {
    if (this.busy) return;
    this.busy = true;
    this.refreshButton.disabled = true;
    this.refreshButton.dataset.busy = "true";
    if (!this.runs.length) this.say(LOADING_MESSAGE);
    try {
      this.runs = newestFirst(await listAllRuns());
      this.refreshButton.title = `Updated ${clock(new Date().toISOString())}`;
      this.render();
    } catch (error) {
      if (error?.status === 401) {
        this.runs = [];
        this.render();
        this.say("Connect to the API to list threads.");
      } else {
        this.say(error?.message ?? "Threads could not be listed.");
      }
    } finally {
      this.busy = false;
      this.refreshButton.disabled = false;
      delete this.refreshButton.dataset.busy;
    }
  }

  upsert(run) {
    if (!run?.id) return;
    this.runs = newestFirst([...this.runs.filter((candidate) => candidate.id !== run.id), run]);
    this.render();
  }

  select(runId) {
    this.selected = runId ?? null;
    for (const link of this.groups.querySelectorAll(".thread-item")) {
      const current = link.dataset.runId === this.selected;
      if (current) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  setProject(summary) {
    this.projectName.textContent = summary?.name ?? "Workspace";
    const datasets = Array.isArray(summary?.datasets) ? summary.datasets.length : 0;
    // The full framework list belongs to the project page; two of them already say what regime the
    // project is under, and the card is 280px wide.
    const frameworks = Array.isArray(summary?.frameworks) ? summary.frameworks : [];
    const parts = [
      summary?.domain ?? null,
      datasets ? `${integer(datasets)} dataset${datasets === 1 ? "" : "s"}` : null,
      ...frameworks.slice(0, 2),
      frameworks.length > 2 ? `+${frameworks.length - 2}` : null,
    ].filter(Boolean);
    replace(
      this.projectMeta,
      parts.length ? parts.map((part, index) => h("span", null, index ? `· ${part}` : part)) : "project",
    );
  }

  setApi(online) {
    this.projectDot.dataset.state = online === null || online === undefined ? "unknown" : online ? "online" : "offline";
    this.projectDot.title = this.projectDot.dataset.state === "online" ? "The API is reachable" : "The API is not reachable";
  }

  setCollapsed(collapsed) {
    this.collapseButton.setAttribute("aria-label", collapsed ? "Expand the sidebar" : "Collapse the sidebar");
    this.collapseButton.title = collapsed ? "Expand the sidebar (Ctrl+B)" : "Collapse the sidebar (Ctrl+B)";
  }

  // The footer button says what it will do next, which is also how the operator reads the state.
  syncConnection() {
    this.connectButton.textContent = this.hasToken() ? "Disconnect" : "Connect";
    this.connectButton.title = this.hasToken()
      ? "Forget the API token kept in this tab"
      : "Paste the API token for this tab";
  }

  syncTheme() {
    replace(this.themeButton, icon(themeIcon(), { size: 16 }));
    this.themeButton.title = `Theme: ${currentTheme()}`;
  }

  say(text) {
    this.message.textContent = text ?? "";
  }

  visible() {
    const keep = FILTERS.find(([id]) => id === this.filter)?.[2] ?? (() => true);
    return this.runs.filter((run) => keep(run) && matches(run, this.query));
  }

  render() {
    this.pills.forEach((pill, index) => {
      pill.setAttribute("aria-pressed", FILTERS[index][0] === this.filter ? "true" : "false");
    });
    const visible = this.visible();
    if (!this.runs.length) this.say(this.busy ? LOADING_MESSAGE : "No thread yet. Start one to see it here.");
    else this.say(visible.length ? "" : "No thread matches this search.");
    const buckets = new Map(GROUP_ORDER.map((name) => [name, []]));
    for (const run of visible) buckets.get(groupOf(run.created_at)).push(run);
    replace(
      this.groups,
      GROUP_ORDER.filter((name) => buckets.get(name).length).map((name) =>
        h(
          "section",
          { class: "thread-group" },
          h("h2", { class: "thread-group-title" }, name),
          h("ol", { class: "thread-list" }, buckets.get(name).map((run) => this.item(run))),
        ),
      ),
    );
  }

  item(run) {
    const live = ACTIVE_STATUSES.has(run.status);
    const waiting = run.status === "WAITING_FOR_APPROVAL";
    return h(
      "li",
      null,
      h(
        "a",
        {
          class: "thread-item",
          href: href({ view: "thread", runId: run.id }),
          dataset: { runId: run.id },
          "aria-current": run.id === this.selected ? "page" : null,
          title: run.prompt,
        },
        h("i", {
          class: `thread-dot tone-${statusTone(run.status)}`,
          dataset: live ? { live: "true" } : null,
          "aria-hidden": "true",
        }),
        h("span", { class: "thread-title" }, headline(run.prompt, 110)),
        h(
          "span",
          { class: "thread-meta" },
          h("span", null, relative(run.created_at)),
          h("span", null, run.final_decision ? decisionLabel(run.final_decision) : statusLabel(run.status)),
          h("span", { class: "mono" }, shortId(run.id)),
        ),
        waiting ? h("span", { class: "thread-attention" }, "Review") : null,
      ),
    );
  }
}
