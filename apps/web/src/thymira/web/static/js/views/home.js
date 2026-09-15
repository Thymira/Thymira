// Home: the page that starts a thread. One composer on an empty column, the three things worth
// knowing about how a thread runs, and the last few threads for orientation.
//
// A Run is one prompt: this view creates it and hands the record back to the shell, which routes to
// the thread. The draft survives navigation inside the tab (sessionStorage), never localStorage and
// never the API: an unsent prompt is not Run state.

import { api } from "../api.js";
import { h, replace } from "../dom.js";
import { headline, relative } from "../format.js";
import { icon } from "../icons.js";
import { href } from "../router.js";
import { statusLabel, statusTone } from "../status.js";
import { Composer } from "./composer.js";

const DRAFT_KEY = "thymira.console.draft";
const RECENT_LIMIT = 5;

const CARDS = [
  ["search", "Inspect → Plan → Execute → Report", "THY registers the project's datasets, plans the work and reports what it found."],
  ["alert", "Questions and reviews wait for you", "The risk interview and every tool call with side effects stop the thread until you answer."],
  ["shield", "MIRA audits before it completes", "An independent auditor checks the work against methodology and regulation; the Policy Engine decides."],
];

function readDraft() {
  try {
    return sessionStorage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(text) {
  try {
    if (text) sessionStorage.setItem(DRAFT_KEY, text);
    else sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // Storage is unavailable; the draft lasts only as long as this view.
  }
}

export class HomeView {
  constructor(root, { onCreated, recent = () => [], project = () => null } = {}) {
    this.onCreated = onCreated;
    this.recentRuns = recent;
    this.datasets = (project()?.datasets ?? []).map((name) => ({ name }));

    this.eyebrow = h("p", { class: "eyebrow" }, "New thread");
    this.composerHost = h("div");
    this.recent = h("section", { class: "home-recent", hidden: true });

    replace(
      root,
      h(
        "section",
        { class: "home" },
        h(
          "div",
          { class: "home-inner" },
          h(
            "div",
            { class: "home-hero" },
            this.eyebrow,
            h("h1", null, "What should THY work on?"),
          ),
          this.composerHost,
          h(
            "p",
            { class: "small muted", style: { textAlign: "center" } },
            "Datasets and context.md belong to the project, not to one thread. ",
            h("a", { href: href({ view: "project" }) }, "Manage project inputs"),
          ),
          h(
            "div",
            { class: "home-cards" },
            CARDS.map(([name, title, body]) =>
              h("div", { class: "home-card" }, icon(name, { size: 18 }), h("strong", null, title), h("span", null, body)),
            ),
          ),
          this.recent,
        ),
      ),
    );

    this.composer = new Composer(this.composerHost, {
      mode: "new",
      onSend: (text) => this.create(text),
      onInput: (text) => writeDraft(text),
      datasets: () => this.datasets,
    });
    this.composer.setState({ kind: "new" });

    const draft = readDraft();
    if (draft) this.composer.insert(draft);
    this.setProject(project());
    this.refreshRecent();
    this.loadDatasets();
    this.composer.focus();
  }

  async loadDatasets() {
    try {
      const body = await api.projectDatasets();
      this.datasets = (body?.items ?? []).map((dataset) => ({ name: dataset.name }));
      if (!this.disposed) this.composer.setState({ kind: "new" });
    } catch {
      // The chips are a convenience; a prompt can always name a dataset by hand.
    }
  }

  setProject(summary) {
    this.eyebrow.textContent = summary?.name ? `${summary.name} · New thread` : "New thread";
    if (summary?.datasets?.length && !this.datasets.length) {
      this.datasets = summary.datasets.map((name) => ({ name }));
      this.composer.setState({ kind: "new" });
    }
  }

  refreshRecent() {
    const runs = [...(this.recentRuns() ?? [])]
      .sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))
      .slice(0, RECENT_LIMIT);
    this.recent.hidden = !runs.length;
    if (!runs.length) return;
    replace(
      this.recent,
      h("p", { class: "eyebrow" }, "Recent"),
      h(
        "ul",
        null,
        runs.map((run) =>
          h(
            "li",
            null,
            h(
              "a",
              { href: href({ view: "thread", runId: run.id }), title: run.prompt },
              h("i", { class: `thread-dot tone-${statusTone(run.status)}`, "aria-hidden": "true" }),
              h("span", { class: "home-recent-title" }, headline(run.prompt, 90)),
              h("span", { class: "muted small" }, statusLabel(run.status)),
              h("span", { class: "muted small" }, relative(run.created_at)),
            ),
          ),
        ),
      ),
    );
  }

  async create(text) {
    const prompt = String(text ?? "").trim();
    if (!prompt) return;
    this.composer.setError(null);
    this.composer.setBusy(true);
    try {
      const body = await api.createRun(prompt);
      writeDraft("");
      this.composer.clear();
      this.onCreated?.(body?.run ?? body);
    } catch (error) {
      this.composer.setError(`${error?.code ?? "error"}: ${error?.message ?? error}`);
    } finally {
      if (!this.disposed) this.composer.setBusy(false);
    }
  }

  dispose() {
    this.disposed = true;
    this.composer?.dispose?.();
  }
}
