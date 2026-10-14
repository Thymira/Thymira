import { api, listAllSessions } from "../api.js";
import { h, replace } from "../dom.js";
import { relative, shortId } from "../format.js";
import { sessionTitle } from "../session-model.js";

function newestFirst(sessions) {
  return [...sessions].sort((left, right) =>
    String(right.updated_at ?? right.created_at).localeCompare(String(left.updated_at ?? left.created_at)),
  );
}

export class SessionRail {
  constructor(root, { onCreated } = {}) {
    this.root = root;
    this.onCreated = onCreated;
    this.sessions = [];
    this.previews = new Map();
    this.selected = null;
    this.query = "";
    this.busy = false;
    this.count = h("span", { class: "session-count" });
    this.message = h("p", { class: "session-rail-message", role: "status" });
    this.list = h("ol", { class: "session-list" });
    this.search = h("input", {
      class: "input",
      type: "search",
      placeholder: "Search sessions",
      "aria-label": "Search sessions",
      autocomplete: "off",
      onInput: (event) => {
        this.query = event.target.value.trim().toLowerCase();
        this.render();
      },
    });
    this.newButton = h(
      "button",
      { class: "btn btn-primary btn-block", type: "button", onClick: () => this.create() },
      "+ New session",
    );
    this.refreshButton = h(
      "button",
      { class: "btn btn-small", type: "button", onClick: () => this.refresh() },
      "Refresh",
    );
    replace(
      root,
      h("div", { class: "session-rail-head" }, h("span", { class: "rail-title" }, "Sessions"), this.count),
      h("div", { class: "session-rail-new" }, this.newButton),
      h("div", { class: "session-rail-search" }, this.search),
      this.message,
      this.list,
      h("div", { class: "session-rail-footer" }, this.refreshButton),
    );
  }

  async create() {
    if (this.busy) return;
    this.newButton.disabled = true;
    this.say("Creating a session…");
    try {
      const result = await api.createSession({ client: "web" });
      const session = result?.session ?? result;
      this.upsert(session);
      this.say("");
      this.onCreated?.(session);
    } catch (error) {
      this.say(error?.message ?? "A session could not be created.");
    } finally {
      this.newButton.disabled = false;
    }
  }

  async refresh() {
    if (this.busy) return;
    this.busy = true;
    this.refreshButton.disabled = true;
    if (!this.sessions.length) this.say("Loading conversations…");
    try {
      this.sessions = newestFirst(await listAllSessions());
      this.say(this.sessions.length ? "" : "No conversations yet. Create a session to begin.");
      this.render();
    } catch (error) {
      if (error?.status === 401) {
        this.sessions = [];
        this.render();
        this.say("Connect to the API to list sessions.");
      } else {
        this.say(error?.message ?? "Sessions could not be listed.");
      }
    } finally {
      this.busy = false;
      this.refreshButton.disabled = false;
    }
  }

  upsert(session, turns) {
    if (!session?.id) return;
    if (Array.isArray(turns)) this.previews.set(session.id, turns);
    this.sessions = newestFirst([...this.sessions.filter((item) => item.id !== session.id), session]);
    this.render();
  }

  select(sessionId) {
    this.selected = sessionId ?? null;
    for (const link of this.list.querySelectorAll(".session-item")) {
      const selected = link.dataset.sessionId === this.selected;
      link.classList.toggle("is-selected", selected);
      if (selected) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }

  say(message) {
    this.message.textContent = message ?? "";
  }

  render() {
    const visible = this.sessions.filter((session) => {
      if (!this.query) return true;
      const title = sessionTitle(session, this.previews.get(session.id));
      return `${title} ${session.id}`.toLowerCase().includes(this.query);
    });
    this.count.textContent = this.sessions.length ? String(this.sessions.length) : "";
    this.list.replaceChildren(
      ...visible.map((session) => {
        const date = session.updated_at ?? session.created_at;
        return h(
          "li",
          null,
          h(
            "a",
            {
              class: ["session-item", session.id === this.selected ? "is-selected" : null],
              href: `#/sessions/${encodeURIComponent(session.id)}`,
              dataset: { sessionId: session.id },
              "aria-current": session.id === this.selected ? "page" : null,
              title: session.id,
            },
            h("span", { class: "session-item-title" }, sessionTitle(session, this.previews.get(session.id))),
            h("span", { class: "session-item-meta" }, h("span", { class: "mono" }, shortId(session.id)), date ? h("span", null, relative(date)) : null),
          ),
        );
      }),
    );
    this.select(this.selected);
  }
}
