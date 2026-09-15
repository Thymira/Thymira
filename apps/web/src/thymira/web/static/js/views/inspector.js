// The inspector: the evidence beside the thread. It hosts the panel renderers unchanged
// (views/tabs/*.js, same `ctx` contract), so no evidence view was lost when the console became
// thread-first; the transcript tells the story, this panel holds the record.
//
// It owns no Run state: every panel is a fold over the events and API resources the thread view
// already loaded, and the "live" panels redraw on the same 400 ms batch.

import { h, replace } from "../dom.js";
import { icon } from "../icons.js";
import { PANELS } from "../router.js";
import { iconButton, skeleton } from "../ui.js";
import { renderAgents } from "./tabs/agents.js";
import { renderApprovals } from "./tabs/approvals.js";
import { renderArtifacts } from "./tabs/artifacts.js";
import { renderAudit } from "./tabs/audit.js";
import { renderEvents } from "./tabs/events.js";
import { renderInputs } from "./tabs/inputs.js";
import { renderInterview } from "./tabs/interview.js";
import { renderOverview } from "./tabs/overview.js";
import { renderPlan } from "./tabs/plan.js";
import { renderTools } from "./tabs/tools.js";
import { renderUsage } from "./tabs/usage.js";

// "live" panels are folds over the event log and redraw as events arrive; the others read an API
// resource once and refresh when the operator comes back to them.
const PANEL_TABLE = {
  details: { label: "Details", icon: "info", render: renderOverview, live: true },
  plan: { label: "Plan", icon: "file", render: renderPlan, live: false },
  agents: { label: "Agents", icon: "agent", render: renderAgents, live: true },
  reviews: { label: "Reviews", icon: "check", render: renderApprovals, live: true },
  tools: { label: "Tools", icon: "tool", render: renderTools, live: true },
  artifacts: { label: "Artifacts", icon: "image", render: renderArtifacts, live: false },
  audit: { label: "Audit", icon: "shield", render: renderAudit, live: false },
  events: { label: "Events", icon: "clock", render: renderEvents, live: true },
  usage: { label: "Usage", icon: "table", render: renderUsage, live: true },
  inputs: { label: "Inputs", icon: "database", render: renderInputs, live: false },
  interview: { label: "Interview", icon: "thread", render: renderInterview, live: true },
};

const MIN_WIDTH = 320;
const KEY_STEP = 24;
const DEFAULT_PANEL = "details";

function maxWidth() {
  return Math.round(window.innerWidth * 0.6);
}

// A panel the operator is typing into is never redrawn under their hands: the stale button offers
// the refresh instead.
function isEditing(container) {
  const active = document.activeElement;
  if (active && container.contains(active) && active.matches("input, textarea, select")) return true;
  return [...container.querySelectorAll("textarea")].some((field) => field.value.trim());
}

export class Inspector {
  constructor(root, options = {}) {
    this.root = root;
    this.options = options;
    this.shell = document.getElementById("shell");
    this.ctx = null;
    this.current = null;
    this.item = null;
    this._panel = DEFAULT_PANEL;
    this._open = false;
    this.tabs = new Map();
    this.badges = new Map();

    this.handle = h("button", {
      class: "inspector-resize",
      type: "button",
      "aria-label": "Resize the inspector",
      onKeyDown: (event) => this.onHandleKey(event),
    });
    this.handle.addEventListener("pointerdown", (event) => this.onHandleDown(event));
    this.stale = h("button", {
      class: "inspector-stale",
      type: "button",
      hidden: true,
      onClick: () => this.renderPanel(),
    }, "New events · refresh this panel");
    this.tabStrip = h("nav", { class: "inspector-tabs", "aria-label": "Evidence panels" }, this.buildTabs());
    this.body = h("div", { class: "inspector-body" });
    replace(
      root,
      this.handle,
      h(
        "header",
        { class: "inspector-head" },
        h("h2", null, "Inspector"),
        iconButton("x", {
          label: "Close the inspector",
          kind: "ghost",
          onClick: () => {
            this.close();
            this.options.onClose?.();
          },
        }),
      ),
      this.tabStrip,
      this.body,
    );
    this.root.hidden = true;
  }

  buildTabs() {
    return PANELS.map((panel) => {
      const entry = PANEL_TABLE[panel];
      const badge = h("span", { class: "inspector-badge" });
      const tab = h(
        "button",
        {
          class: "inspector-tab",
          type: "button",
          onClick: () => this.options.onPanelChange?.(panel),
        },
        icon(entry.icon, { size: 14 }),
        entry.label,
        badge,
      );
      this.tabs.set(panel, tab);
      this.badges.set(panel, badge);
      return tab;
    });
  }

  get isOpen() {
    return this._open;
  }

  get panel() {
    return this._panel;
  }

  open(panel, item = null) {
    const next = PANEL_TABLE[panel] ? panel : DEFAULT_PANEL;
    const changed = !this._open || next !== this._panel || item !== this.item;
    this._panel = next;
    this.item = item;
    this._open = true;
    this.root.hidden = false;
    if (this.shell) this.shell.dataset.inspector = "open";
    this.markTabs();
    if (changed) this.renderPanel();
  }

  close() {
    if (!this._open) return;
    this._open = false;
    this.current = null;
    this.root.hidden = true;
    if (this.shell) this.shell.dataset.inspector = "closed";
  }

  toggle() {
    if (this._open) {
      this.close();
      this.options.onClose?.();
      return;
    }
    this.options.onPanelChange?.(this._panel);
  }

  markTabs() {
    for (const [panel, tab] of this.tabs) {
      if (panel === this._panel) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }
  }

  setBadges(counts = {}) {
    for (const [panel, badge] of this.badges) {
      const entry = counts[panel] ?? {};
      const attention = Boolean(entry.attention);
      const count = Number.isFinite(entry.count) ? entry.count : 0;
      const label = count ? String(count) : "";
      replace(badge, label || (attention ? "!" : null));
      if (attention) badge.dataset.attention = "true";
      else delete badge.dataset.attention;
    }
  }

  update(ctx) {
    this.ctx = ctx;
    if (!this._open) return;
    if (!this.current) {
      this.renderPanel();
      return;
    }
    if (!PANEL_TABLE[this._panel]?.live) return;
    if (this.current.update) {
      this.current.update({ ...ctx, item: this.item });
      return;
    }
    if (isEditing(this.body)) {
      this.stale.hidden = false;
      return;
    }
    this.renderPanel();
  }

  renderPanel() {
    this.stale.hidden = true;
    if (!this.ctx?.run) {
      this.current = null;
      replace(this.body, this.stale, skeleton(6));
      return;
    }
    const entry = PANEL_TABLE[this._panel] ?? PANEL_TABLE[DEFAULT_PANEL];
    const result = entry.render({ ...this.ctx, item: this.item });
    this.current = result instanceof Node ? { node: result } : result;
    replace(this.body, this.stale, this.current.node);
    this.body.scrollTop = 0;
  }

  // ---------------------------------------------------------------- resizing

  width() {
    return this.root.getBoundingClientRect().width || MIN_WIDTH;
  }

  applyWidth(px) {
    const width = Math.round(Math.min(Math.max(px, MIN_WIDTH), maxWidth()));
    this.shell?.style.setProperty("--inspector-width", `${width}px`);
    this.options.onWidth?.(width);
  }

  onHandleDown(event) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = this.width();
    this.handle.setPointerCapture?.(event.pointerId);
    const move = (moved) => this.applyWidth(startWidth + (startX - moved.clientX));
    const stop = () => {
      this.handle.removeEventListener("pointermove", move);
      this.handle.removeEventListener("pointerup", stop);
      this.handle.removeEventListener("pointercancel", stop);
    };
    this.handle.addEventListener("pointermove", move);
    this.handle.addEventListener("pointerup", stop);
    this.handle.addEventListener("pointercancel", stop);
  }

  onHandleKey(event) {
    if (event.key === "ArrowLeft") this.applyWidth(this.width() + KEY_STEP);
    else if (event.key === "ArrowRight") this.applyWidth(this.width() - KEY_STEP);
    else return;
    event.preventDefault();
  }
}
