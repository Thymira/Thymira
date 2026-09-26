// Small presentational building blocks shared by every view.

import { h, replace } from "./dom.js";
import { icon } from "./icons.js";
import { decisionLabel, decisionTone, severityTone, statusLabel, statusTone } from "./status.js";

export function tag(text, tone = "neutral", title) {
  return h("span", { class: `tag tone-${tone}`, title }, text);
}

export function statusTag(status) {
  return h(
    "span",
    { class: `status tone-${statusTone(status)}` },
    h("i", { class: "sq", "aria-hidden": "true" }),
    statusLabel(status),
  );
}

export function decisionTag(decision) {
  return decision ? tag(decisionLabel(decision), decisionTone(decision)) : h("span", { class: "muted" }, "—");
}

export function mono(text) {
  return h("code", { class: "mono" }, text ?? "—");
}

export function copyable(value) {
  const copy = h("button", { class: "copy", type: "button", title: "Copy to the clipboard" }, "Copy");
  copy.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select it";
    }
    setTimeout(() => {
      copy.textContent = "Copy";
    }, 1400);
  });
  return h("span", { class: "copyable" }, h("code", { class: "mono" }, value), copy);
}

export function panel(title, body, { aside, className } = {}) {
  return h(
    "section",
    { class: ["panel", className] },
    h("header", { class: "panel-head" }, h("h2", null, title), aside ? h("div", { class: "panel-aside" }, aside) : null),
    h("div", { class: "panel-body" }, body),
  );
}

export function kv(rows) {
  return h(
    "dl",
    { class: "kv" },
    rows
      .filter(Boolean)
      .map(([label, value]) => [
        h("dt", null, label),
        h("dd", null, value === undefined || value === null || value === "" ? "—" : value),
      ]),
  );
}

export function chips(values, tone = "neutral") {
  if (!Array.isArray(values) || !values.length) return h("span", { class: "muted" }, "—");
  return h("span", { class: "chips" }, values.map((value) => tag(String(value), tone)));
}

export function codeBlock(value, { className } = {}) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return h("pre", { class: ["code", className] }, text);
}

export function empty(text) {
  return h("p", { class: "empty" }, text);
}

export function loading(text = "Loading…") {
  return h("p", { class: "loading" }, text);
}

export function notice(text, tone = "neutral") {
  return h("p", { class: `notice tone-${tone}` }, text);
}

export function errorNotice(error) {
  const code = error?.code ? `${error.code}: ` : "";
  return notice(`${code}${error?.message ?? String(error)}`, "block");
}

export function button(label, { onClick, kind, type = "button", title, disabled } = {}) {
  const kinds = (Array.isArray(kind) ? kind : [kind]).filter(Boolean).map((name) => `btn-${name}`);
  return h("button", { class: ["btn", ...kinds], type, title, disabled, onClick }, label);
}

// A run's progress through its workflow stages. `index` is -1 (not started), 0..steps.length-1
// (currently in that step) or steps.length (finished the last step). `outcomeTone` colours the
// current or final step when the Run reached a terminal outcome (ok/block); it stays undefined
// while the Run is still active.
export function stepper(steps, index, outcomeTone) {
  const finished = index >= steps.length;
  const markedPosition = finished ? steps.length - 1 : index;
  return h(
    "ol",
    { class: "stepper" },
    steps.map((step, position) => {
      const done = position < index;
      const marked = position === markedPosition && outcomeTone;
      return h(
        "li",
        {
          class: [
            "step",
            done ? "is-done" : null,
            position === index ? "is-current" : null,
            marked ? `tone-${outcomeTone}` : null,
            step.owner ? `is-owner-${step.owner}` : null,
          ],
        },
        h("i", { class: "step-dot", "aria-hidden": "true" }),
        h("span", { class: "step-label" }, step.label),
      );
    }),
  );
}

export function fact(label, value) {
  return h("div", { class: "fact" }, h("span", { class: "fact-label" }, label), h("span", { class: "fact-value" }, value));
}

export function stat(label, value) {
  return h("div", { class: "stat" }, h("span", { class: "stat-label" }, label), h("span", { class: "stat-value" }, value));
}

export function table(columns, rows, { emptyText = "Nothing recorded.", rowClass, expand, className } = {}) {
  if (!rows.length) return empty(emptyText);
  const body = h("tbody");
  for (const row of rows) {
    const cells = columns.map((column) =>
      h("td", { class: [column.numeric ? "num" : null, column.className] }, column.render(row)),
    );
    const tr = h("tr", { class: [rowClass?.(row), expand ? "is-expandable" : null] }, cells);
    body.append(tr);
    if (!expand) continue;
    tr.tabIndex = 0;
    const toggle = () => {
      const next = tr.nextElementSibling;
      if (next?.classList.contains("row-detail")) {
        next.remove();
        tr.classList.remove("is-open");
        return;
      }
      tr.after(h("tr", { class: "row-detail" }, h("td", { colspan: columns.length }, expand(row))));
      tr.classList.add("is-open");
    };
    tr.addEventListener("click", (event) => {
      if (event.target.closest("a, button, input, textarea, select")) return;
      toggle();
    });
    tr.addEventListener("keydown", (event) => {
      if (event.target !== tr || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      toggle();
    });
  }
  const head = h(
    "thead",
    null,
    h("tr", null, columns.map((column) => h("th", { class: column.numeric ? "num" : null, scope: "col" }, column.label))),
  );
  return h("div", { class: "table-wrap" }, h("table", { class: ["grid", className] }, head, body));
}

// Render a value fetched on demand; an expected "not found" code becomes a quiet empty state.
export function asyncBlock(load, render, { quiet = {} } = {}) {
  const holder = h("div", { class: "stack" }, loading());
  load()
    .then((value) => {
      holder.replaceChildren(...[render(value)].flat(Infinity).filter(Boolean));
    })
    .catch((error) => {
      if (error?.name === "AbortError") return;
      const message = quiet[error?.code];
      holder.replaceChildren(message ? empty(message) : errorNotice(error));
    });
  return holder;
}

export function findingList(findings) {
  if (!findings.length) return empty("No finding recorded.");
  return h(
    "ol",
    { class: "findings" },
    findings.map((finding) =>
      h(
        "li",
        { class: "finding" },
        h(
          "div",
          { class: "finding-head" },
          tag(finding.severity ?? "—", severityTone(finding.severity)),
          h("strong", null, finding.title ?? "Finding"),
          h("code", { class: "mono muted" }, finding.control_id),
          typeof finding.confidence === "number" ? h("span", { class: "muted small" }, `confidence ${Math.round(finding.confidence * 100)}%`) : null,
        ),
        finding.finding ? h("p", null, finding.finding) : null,
        finding.recommendation ? h("p", { class: "muted" }, `Recommendation: ${finding.recommendation}`) : null,
      ),
    ),
  );
}

// A placeholder with the shape of the content that is still loading, so the layout does not jump
// when the API answers. It carries no Run data at all.
export function skeleton(lines = 3) {
  return h(
    "div",
    { class: "skeleton", "aria-hidden": "true" },
    Array.from({ length: Math.max(1, lines) }, (_, index) =>
      h("span", { class: "skeleton-line", style: { width: `${100 - index * 12}%` } }),
    ),
  );
}

export function iconButton(name, { label, onClick, kind, pressed, title, disabled } = {}) {
  const kinds = (Array.isArray(kind) ? kind : [kind]).filter(Boolean).map((entry) => `btn-${entry}`);
  return h(
    "button",
    {
      class: ["btn", "btn-icon", ...kinds],
      type: "button",
      "aria-label": label,
      "aria-pressed": pressed === undefined ? null : String(Boolean(pressed)),
      title: title ?? label,
      disabled,
      onClick,
    },
    icon(name, { size: 16 }),
  );
}

const AVATAR_FALLBACK = { thy: "T", mira: "M", user: "Y", agent: "A" };

// Who is speaking in the transcript. `tool` is the only kind drawn as a glyph; the rest carry one
// or two letters so the colour is never the only signal.
export function avatar(kind = "agent", text) {
  const letters = String(text ?? "")
    .replace(/[^A-Za-z0-9]/g, "")
    .slice(0, kind === "user" ? 1 : 2)
    .toUpperCase();
  const body = kind === "tool" && !letters ? icon("tool", { size: 14 }) : letters || AVATAR_FALLBACK[kind] || "?";
  return h("span", { class: "avatar", dataset: { kind }, "aria-hidden": "true" }, body);
}

export function kbd(text) {
  return h("kbd", { class: "kbd" }, text);
}

// The shell's single confirmation dialog. It resolves true only when the confirm button submitted
// the form: closing with Escape, the backdrop or Cancel resolves false.
export function confirmDialog({ title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false }) {
  const dialog = document.getElementById("confirm");
  if (!dialog) return Promise.resolve(false);
  return new Promise((resolve) => {
    let confirmed = false;
    const confirm = h(
      "button",
      { class: ["btn", danger ? "btn-danger" : "btn-primary"], type: "submit", value: "confirm", onClick: () => { confirmed = true; } },
      confirmLabel,
    );
    const cancel = h("button", { class: "btn btn-ghost", type: "submit", value: "cancel" }, cancelLabel);
    const form = h(
      "form",
      { method: "dialog" },
      h("h2", { id: "confirm-title" }, title),
      h("p", { class: "dialog-body" }, body),
      h("div", { class: "dialog-actions" }, cancel, confirm),
    );
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      replace(dialog);
      resolve(confirmed);
    };
    dialog.addEventListener("close", onClose);
    replace(dialog, form);
    dialog.showModal();
    // A destructive confirmation opens on the safe choice: a stray Enter must not cancel a thread.
    (danger ? cancel : confirm).focus();
  });
}

export function downloadBytes(name, data, mediaType) {
  const url = URL.createObjectURL(new Blob([data], { type: mediaType || "application/octet-stream" }));
  const link = h("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
