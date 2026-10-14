import { api } from "../api.js";
import { h, replace } from "../dom.js";
import { errorNotice, notice } from "../ui.js";

const FIELDS = {
  purpose: "Purpose and intended use",
  affected_population: "People affected",
  decision_effect: "Effect on decisions",
  autonomy: "Autonomy and deployment",
  human_oversight: "Human oversight",
  jurisdiction: "Jurisdiction",
  data_categories: "Data categories",
  sensitive_attributes: "Sensitive attributes",
  potential_consequences: "Potential consequences",
};
const ALIASES = { intended_use: "purpose", decision_role: "decision_effect", deployment_context: "autonomy" };

function display(value) {
  if (Array.isArray(value)) return value.length ? value.join("; ") : "None declared";
  return value || "Not yet specified";
}

/** Keep an event-backed interview inside the conversation without replacing a live answer draft. */
export class SessionInterviewView {
  constructor(root, { onUpdated } = {}) {
    this.root = root;
    this.onUpdated = onUpdated;
    this.runId = null;
    this.status = null;
    this.review = null;
    this.key = null;
    this.loading = false;
    this.posting = false;
    this.disposed = false;
    this.generation = 0;
    this.error = null;
  }

  dispose() {
    this.disposed = true;
    this.generation += 1;
  }

  update(turn) {
    const runId = turn?.id ?? null;
    if (runId !== this.runId) {
      this.generation += 1;
      this.loading = false;
      this.runId = runId;
      this.status = null;
      this.review = null;
      this.key = null;
      this.error = null;
      replace(this.root);
    }
    this.stale = Boolean(turn?.stale);
    if (runId && !this.loading && !this.posting) this.refresh();
    this.syncControls();
  }

  async refresh() {
    const generation = this.generation;
    const runId = this.runId;
    if (!runId) return;
    this.loading = true;
    try {
      const status = await api.riskInterview(runId);
      if (this.disposed || generation !== this.generation) return;
      this.status = status;
      this.error = null;
      this.render();
    } catch (error) {
      if (this.disposed || generation !== this.generation) return;
      this.error = error;
      if (!this.status) replace(this.root, errorNotice(error));
    } finally {
      if (generation === this.generation) {
        this.loading = false;
        this.syncControls();
      }
    }
  }

  async openClarification(review) {
    if (review.runId !== this.runId || this.posting) return;
    this.review = review;
    this.key = null;
    if (!this.status) await this.refresh();
    this.render();
    this.fieldSelect?.focus();
  }

  render() {
    if (!this.status || this.disposed) return;
    const question = this.status.pending_question;
    const key = JSON.stringify([this.runId, this.status.profile?.id, question, this.review?.decisionId]);
    if (key === this.key) { this.syncControls(); return; }
    this.key = key;
    this.answerField = null;
    this.submitButton = null;
    this.fieldSelect = null;
    this.feedback = h("div", { role: "status" });
    const profile = this.status.profile ?? {};
    const facts = h("details", { class: "session-risk-context", open: Boolean(this.review) },
      h("summary", {}, "MIRA preflight · recorded activity facts"),
      h("p", { class: "muted small" }, "These declared facts inform classification. Context from the project can fill them automatically. A declaration does not authorize execution or lower a policy restriction."),
      h("dl", { class: "risk-context-facts" }, ...Object.entries(FIELDS).flatMap(([field, label]) => [
        h("dt", {}, label), h("dd", {}, display(profile[field])),
      ])),
    );
    const content = [];
    if (question) {
      this.review = null;
      this.answerField = h("textarea", {
        class: "input session-risk-answer", rows: 4, maxlength: 8000,
        "aria-label": "Answer the risk question",
        onInput: () => this.syncControls(),
        onKeydown: (event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault(); this.answer();
          }
        },
      });
      this.submitButton = h("button", { class: "btn btn-primary", type: "submit" }, "Record answer and continue");
      content.push(
        h("h2", {}, "MIRA preflight · clarification needed"),
        h("p", { class: "muted small" }, "Your answer stays in this Run's preflight. It does not create another work request or approve execution."),
        h("p", { class: "session-risk-question" }, question.question),
        h("p", { class: "muted small" }, `Current ${FIELDS[question.field] ?? question.field}: ${display(profile[question.field])}`),
        h("form", { onSubmit: (event) => { event.preventDefault(); this.answer(); } },
          this.answerField,
          h("div", { class: "session-review-actions" }, this.submitButton),
        ),
      );
    } else if (this.review) {
      this.fieldSelect = h("select", { class: "input", "aria-label": "Risk fact to clarify" },
        ...Object.entries(FIELDS).map(([field, label]) => h("option", { value: field }, label)),
      );
      const missing = this.review.missingInformation?.[0];
      this.fieldSelect.value = ALIASES[missing] ?? (FIELDS[missing] ? missing : "decision_effect");
      this.submitButton = h("button", { class: "btn btn-primary", onClick: () => this.clarify() }, "Clarify this fact in chat");
      content.push(
        h("h2", {}, "Review the facts used to classify risk"),
        h("p", {}, "Choose a fact to correct or explain. Execution stays stopped. Your answer creates a new profile version and reruns classification; any required approval will be requested again."),
        this.fieldSelect,
        h("div", { class: "session-review-actions" }, this.submitButton,
          h("button", { class: "btn", onClick: () => { this.review = null; this.key = null; this.render(); } }, "Close context editor")),
      );
    }
    replace(this.root, h("section", { class: "session-interview-card" }, ...content, this.feedback, facts));
    this.syncControls();
  }

  syncControls() {
    const disabled = this.posting || this.stale || Boolean(this.error);
    if (this.answerField) this.answerField.disabled = disabled;
    if (this.fieldSelect) this.fieldSelect.disabled = disabled;
    if (this.submitButton) this.submitButton.disabled = disabled || (this.answerField && !this.answerField.value.trim());
    if (!this.feedback) return;
    if (this.error) replace(this.feedback, errorNotice(this.error));
    else if (this.posting) replace(this.feedback, notice("Updating this turn…", "warn"));
    else if (this.stale) replace(this.feedback, notice("Session status is unavailable. Refresh before answering.", "warn"));
    else replace(this.feedback);
  }

  async answer() {
    if (!this.answerField?.value.trim() || this.posting || this.stale || this.error) return;
    const question = this.status?.pending_question;
    if (!question) return;
    await this.mutate(() => api.answerRiskInterview(this.runId, this.answerField.value.trim(), question.event_id));
  }

  async clarify() {
    if (!this.review || this.posting || this.stale || this.error) return;
    await this.mutate(() => api.clarifyRisk(this.runId, this.fieldSelect.value, this.review.decisionId));
  }

  async mutate(operation) {
    const generation = ++this.generation;
    this.loading = false;
    this.posting = true;
    this.syncControls();
    try {
      const status = await operation();
      if (this.disposed || generation !== this.generation) return;
      this.status = status;
      this.review = null;
      this.key = null;
      this.error = null;
      this.render();
      await this.onUpdated?.();
    } catch (error) {
      if (this.disposed || generation !== this.generation) return;
      this.error = error;
      this.syncControls();
    } finally {
      this.posting = false;
      if (!this.disposed && generation === this.generation) this.syncControls();
    }
  }
}
