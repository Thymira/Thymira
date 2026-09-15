// The console's one composer. Home and a thread share it: a single surface whose function follows
// the Run's state (new · answer · review · paused · working · ended), so the operator always types
// in the same place and the page never grows a second form.
//
// It carries no authority of its own. Approve and Reject here call the same API routes the
// Approvals panel calls; an approval still authorizes exactly one call, once, and the Policy
// Engine still decides. The composer only collects the text and hands it to its owner.

import { h, replace } from "../dom.js";
import { clip } from "../format.js";
import { icon } from "../icons.js";
import { button, skeleton } from "../ui.js";

const HINT = "Enter to send · Shift+Enter for a new line";
const MAX_ROWS = 14;
const MIN_ROWS = 3;
const ROW_HEIGHT = 23;
const PADDING = 26;

const PLACEHOLDER = {
  new: "Describe the work: what to analyse and what to deliver. Use a declared dataset by name.",
  answer: "Answer in your own words; it is recorded as evidence",
  ended: "Describe a follow-up — it starts a new thread with the current project inputs",
};

const SENDING_STATES = new Set(["new", "answer", "ended"]);

// What a state says, as one comparable string: the surface only has to be rebuilt when this
// changes.
function stateKey(state) {
  return [
    state.kind ?? "loading",
    state.question?.question_number ?? "",
    state.question?.field ?? "",
    state.review?.decisionId ?? "",
    state.stage ?? "",
  ].join("|");
}

function argumentSummary(toolCall) {
  const args = toolCall?.arguments;
  if (!args || typeof args !== "object") return "";
  try {
    return clip(JSON.stringify(args), 80);
  } catch {
    return "";
  }
}

export class Composer {
  constructor(root, options = {}) {
    this.options = options;
    this.mode = options.mode === "new" ? "new" : "thread";
    this.state = { kind: this.mode === "new" ? "new" : "loading" };
    this.stateKey = stateKey(this.state);
    this.busy = false;
    this.controls = [];

    this.field = h("textarea", {
      class: "composer-field",
      rows: MIN_ROWS,
      spellcheck: "false",
      "aria-label": "Message",
    });
    this.field.addEventListener("input", () => {
      this.autoGrow();
      this.syncSend();
      this.options.onInput?.(this.field.value);
    });
    this.field.addEventListener("keydown", (event) => this.onKeydown(event));

    this.send = h(
      "button",
      { class: "composer-send", type: "button", "aria-label": "Send", title: "Send", onClick: () => this.submit() },
      icon("send", { size: 17 }),
    );
    this.hint = h("span", { class: "composer-hint" }, HINT);
    this.datasets = h("span", { class: "composer-datasets" });
    this.toolbar = h("div", { class: "composer-toolbar" }, this.datasets, this.hint, this.send);
    this.context = h("div", { class: "composer-context" });
    this.error = h("div", { class: "composer-error", role: "status" });
    this.node = h("section", { class: "composer", dataset: { state: this.state.kind } });
    replace(root, this.node);
    this.draw();
  }

  // ---------------------------------------------------------------- state

  // The thread recomputes its composer state on every 400 ms batch. Redrawing an unchanged surface
  // would detach the textarea and take the focus away from whoever is typing in it, so a state
  // that says the same thing as the current one is a no-op unless an action left it busy.
  setState(state) {
    const next = state && typeof state === "object" ? state : { kind: "loading" };
    const key = stateKey(next);
    if (this.mode === "thread" && !this.busy && key === this.stateKey) return;
    this.state = { ...next, kind: next.kind ?? "loading" };
    this.stateKey = key;
    this.busy = false;
    this.draw();
  }

  // Rebuild the current surface: the caller's `datasets()` or question text changed under it.
  refresh() {
    this.draw();
  }

  setBusy(busy) {
    this.busy = Boolean(busy);
    this.field.disabled = this.busy || !this.fieldEnabled();
    for (const control of this.controls) control.disabled = this.busy;
    this.syncSend();
  }

  setError(text) {
    replace(this.error, text ? String(text) : null);
  }

  focus() {
    if (!this.field.disabled) this.field.focus();
  }

  value() {
    return this.field.value;
  }

  clear() {
    this.field.value = "";
    this.autoGrow();
    this.syncSend();
  }

  // Insert at the caret, so a dataset chip lands where the operator is typing.
  insert(text) {
    const start = this.field.selectionStart ?? this.field.value.length;
    const end = this.field.selectionEnd ?? this.field.value.length;
    this.field.setRangeText(String(text ?? ""), start, end, "end");
    this.field.focus();
    this.autoGrow();
    this.syncSend();
  }

  // ---------------------------------------------------------------- drawing

  fieldEnabled() {
    return SENDING_STATES.has(this.state.kind);
  }

  draw() {
    const kind = this.state.kind;
    this.controls = [];
    this.node.dataset.state = kind;
    replace(this.context);
    this.setError(null);
    if (kind === "loading") {
      replace(this.node, h("div", { class: "composer-context" }, skeleton(2)));
      return;
    }
    if (kind === "review") {
      replace(this.node, this.context, this.reviewBar(), this.error);
      return;
    }
    if (kind === "paused") {
      replace(this.node, this.pausedBar(), this.error);
      return;
    }
    if (kind === "answer") {
      const number = this.state.question?.question_number;
      const field = this.state.question?.field;
      replace(
        this.context,
        h("span", { class: "eyebrow" }, ["Question", number ? ` ${number}` : "", field ? ` · ${field}` : ""].join("")),
        h("p", { class: "question" }, this.state.question?.text ?? ""),
      );
    }
    this.field.disabled = !this.fieldEnabled();
    this.field.placeholder = this.placeholder();
    replace(this.datasets, this.datasetChips());
    replace(this.hint, this.fieldEnabled() ? HINT : null);
    replace(
      this.toolbar,
      this.datasets,
      kind === "ended" ? this.runAgainButton() : null,
      this.hint,
      this.fieldEnabled() ? this.send : null,
    );
    replace(this.node, this.context, this.field, this.toolbar, this.error);
    this.autoGrow();
    this.syncSend();
  }

  placeholder() {
    if (this.state.kind === "working") {
      const stage = String(this.state.stage ?? "").toLowerCase();
      if (stage === "auditing") return "MIRA is auditing…";
      return stage ? `THY is working · ${this.state.stage}` : "THY is working…";
    }
    return PLACEHOLDER[this.state.kind] ?? "";
  }

  datasetChips() {
    if (this.mode !== "new") return null;
    const datasets = this.options.datasets?.() ?? [];
    if (!Array.isArray(datasets) || !datasets.length) return null;
    return datasets.map((dataset) =>
      h(
        "button",
        {
          class: "chip",
          type: "button",
          title: "Insert this dataset name",
          onClick: () => this.insert(`\`${dataset.name}\``),
        },
        dataset.name,
      ),
    );
  }

  runAgainButton() {
    const again = button("Run again", {
      kind: "ghost",
      title: "Start a new thread with this thread's prompt",
      onClick: () => this.options.onRunAgain?.(),
    });
    this.controls.push(again);
    return again;
  }

  pausedBar() {
    const resume = button("Resume", {
      kind: "primary",
      title: "Continue from the last durable checkpoint",
      onClick: () => {
        this.setBusy(true);
        this.options.onResume?.();
      },
    });
    this.controls.push(resume);
    return h(
      "div",
      { class: "composer-review" },
      h("span", null, "Paused at a durable checkpoint."),
      h("span", { class: "composer-hint" }, "Nothing runs until you resume."),
      resume,
    );
  }

  reviewBar() {
    const review = this.state.review ?? {};
    const note = h("input", { class: "input input-small", type: "text", placeholder: "Note (optional)", "aria-label": "Note" });
    const show = button("Show the call", { kind: "ghost", onClick: () => this.options.onShowCall?.() });
    const reject = button("Reject", {
      kind: "danger",
      onClick: () => {
        this.setBusy(true);
        this.options.onReject?.(note.value.trim());
      },
    });
    const approve = button(review.toolCall ? "Approve this call" : "Approve", {
      kind: "approve",
      onClick: () => {
        this.setBusy(true);
        this.options.onApprove?.(note.value.trim());
      },
    });
    this.controls.push(note, show, reject, approve);
    const summary = argumentSummary(review.toolCall);
    return h(
      "div",
      { class: "composer-review" },
      review.toolCall ? h("code", { class: "mono" }, review.toolCall.tool) : h("strong", null, "Review"),
      summary ? h("span", { class: "composer-hint" }, summary) : null,
      show,
      note,
      reject,
      approve,
    );
  }

  // ---------------------------------------------------------------- behaviour

  syncSend() {
    this.send.disabled = this.busy || !this.fieldEnabled() || !this.field.value.trim();
  }

  autoGrow() {
    this.field.style.height = "auto";
    const max = MAX_ROWS * ROW_HEIGHT + PADDING;
    const min = MIN_ROWS * ROW_HEIGHT + PADDING;
    this.field.style.height = `${Math.min(Math.max(this.field.scrollHeight, min), max)}px`;
  }

  onKeydown(event) {
    if (event.key === "Escape") {
      this.field.blur();
      return;
    }
    if (event.key !== "Enter") return;
    if (event.shiftKey && !(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    this.submit();
  }

  submit() {
    if (this.busy || !this.fieldEnabled()) return;
    const text = this.field.value.trim();
    if (!text) {
      this.setError("Write something first.");
      this.field.focus();
      return;
    }
    this.setError(null);
    this.setBusy(true);
    this.options.onSend?.(text, this.state.kind);
  }
}
