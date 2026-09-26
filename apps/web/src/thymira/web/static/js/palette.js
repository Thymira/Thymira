// The Ctrl+K command palette: actions first, then the loaded threads. `fuzzyMatch` is pure and
// unit-tested with node; the palette itself renders with h() only, so a thread title reaches the
// list as a text node and never as markup.

import { h, replace } from "./dom.js";
import { icon } from "./icons.js";

const WORD_BREAKS = new Set([" ", "-", "_", "/", ".", ":"]);
const MATCH_SCORE = 2;
const WORD_START_BONUS = 3;
const GAP_PENALTY = 1;

export function fuzzyMatch(query, source) {
  const needle = String(query ?? "").toLowerCase().trim();
  const haystack = String(source ?? "");
  if (!needle) return { score: 0, indices: [] };
  const lower = haystack.toLowerCase();
  const indices = [];
  let score = 0;
  let cursor = 0;
  for (const character of needle) {
    if (character === " ") continue;
    const at = lower.indexOf(character, cursor);
    if (at < 0) return null;
    const wordStart = at === 0 || WORD_BREAKS.has(haystack[at - 1]);
    score += MATCH_SCORE + (wordStart ? WORD_START_BONUS : 0) - GAP_PENALTY * (at - cursor);
    indices.push(at);
    cursor = at + 1;
  }
  return { score, indices };
}

// The matched characters are marked so the operator sees why a row is in the list.
function highlight(source, indices) {
  const marked = new Set(indices);
  const nodes = [];
  let run = "";
  let inMatch = false;
  const flush = () => {
    if (!run) return;
    nodes.push(inMatch ? h("mark", null, run) : document.createTextNode(run));
    run = "";
  };
  [...String(source)].forEach((character, index) => {
    const isMatch = marked.has(index);
    if (isMatch !== inMatch) {
      flush();
      inMatch = isMatch;
    }
    run += character;
  });
  flush();
  return nodes;
}

export class CommandPalette {
  constructor(host, { commands }) {
    this.host = host;
    this.commands = commands;
    this.shell = null;
    this.open_ = false;
    this.selected = 0;
    this.rows = [];
    this.restoreFocus = null;
    this.input = h("input", {
      class: "palette-input",
      type: "text",
      placeholder: "Search commands and threads…",
      "aria-label": "Command palette",
      autocomplete: "off",
      spellcheck: "false",
    });
    this.list = h("div", { class: "palette-list", role: "listbox" });
    this.card = h(
      "div",
      { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Command palette" },
      h("div", { class: "palette-head" }, icon("search", { size: 16 }), this.input),
      this.list,
      h("div", { class: "palette-hint" }, "↑↓ to move · Enter to run · Esc to close"),
    );
    this.input.addEventListener("input", () => this.refresh());
    // The whole card listens, not only the field: Escape and Tab must be caught wherever the focus
    // is inside the palette.
    this.card.addEventListener("keydown", (event) => this.onKey(event));
    this.host.addEventListener("mousedown", (event) => {
      if (event.target === this.host) this.close();
    });
  }

  get isOpen() {
    return this.open_;
  }

  open() {
    if (this.open_) return;
    this.open_ = true;
    this.restoreFocus = document.activeElement;
    replace(this.host, this.card);
    this.host.hidden = false;
    // It claims to be modal, so it has to be one: the shell behind it stops taking focus and
    // clicks for as long as the palette is up, and Tab cycles inside the card.
    this.shell = document.getElementById("shell");
    if (this.shell) this.shell.inert = true;
    this.input.value = "";
    this.selected = 0;
    this.refresh();
    this.input.focus();
  }

  close() {
    if (!this.open_) return;
    this.open_ = false;
    this.host.hidden = true;
    replace(this.host);
    if (this.shell) this.shell.inert = false;
    if (this.restoreFocus?.isConnected) this.restoreFocus.focus();
    this.restoreFocus = null;
  }

  toggle() {
    if (this.open_) this.close();
    else this.open();
  }

  // Focus stays inside the card: the first stop is the field, the last is the last row.
  trapTab(event) {
    const stops = [this.input, ...this.list.querySelectorAll("button")];
    const last = stops[stops.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === this.input || !this.card.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      this.input.focus();
    }
  }

  onKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      this.close();
      return;
    }
    if (event.key === "Tab") {
      this.trapTab(event);
      return;
    }
    // A row button runs itself on Enter and moves with the arrows through the roving selection
    // below, so only the field drives the list.
    if (event.target !== this.input) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!this.rows.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      this.selected = (this.selected + step + this.rows.length) % this.rows.length;
      this.paint();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      this.run(this.selected);
    }
  }

  run(index) {
    const entry = this.rows[index];
    if (!entry) return;
    this.close();
    entry.command.run();
  }

  refresh() {
    const query = this.input.value;
    this.rows = [];
    for (const command of this.commands()) {
      const match = fuzzyMatch(query, `${command.label} ${command.hint ?? ""}`.trim());
      if (match) this.rows.push({ command, match });
    }
    this.rows.sort((left, right) => right.match.score - left.match.score);
    this.selected = 0;
    this.paint();
  }

  paint() {
    const nodes = [];
    let group = null;
    this.rows.forEach((entry, index) => {
      if (entry.command.group !== group) {
        group = entry.command.group;
        nodes.push(h("div", { class: "palette-group" }, group));
      }
      const row = h(
        "button",
        {
          class: "palette-item",
          type: "button",
          role: "option",
          "aria-selected": index === this.selected ? "true" : "false",
          onClick: () => this.run(index),
        },
        h("span", { class: "palette-item-label" }, highlight(entry.command.label, entry.match.indices)),
        entry.command.hint ? h("span", { class: "palette-item-hint" }, entry.command.hint) : null,
      );
      // The pointer and the keyboard agree on one selection: hovering a row moves it.
      row.addEventListener("mousemove", () => {
        if (this.selected === index) return;
        this.selected = index;
        this.paint();
      });
      nodes.push(row);
    });
    if (!nodes.length) nodes.push(h("p", { class: "empty" }, "Nothing matches."));
    replace(this.list, nodes);
    this.list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }
}
