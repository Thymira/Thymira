import { api } from "../api.js";
import { h, replace } from "../dom.js";

export function terminalDiagnostic(turn, onDiscuss) {
  if (turn.state?.condition !== "terminal" || !["blocked", "failed"].includes(turn.state.outcome)) return null;
  const diagnostic = turn.terminal_diagnostic;
  if (!diagnostic?.summary) {
    return turn.run?.error ? h("p", { class: "notice tone-block" }, turn.run.error) : null;
  }
  return h("section", { class: "notice tone-block conversation-diagnostic", "aria-label": "Run diagnostic" },
    h("strong", {}, turn.state.outcome === "blocked" ? "Why this run was blocked" : "Why this run failed"),
    h("p", {}, diagnostic.summary),
    diagnostic.details?.length ? h("ul", {}, ...diagnostic.details.map((detail) => h("li", {}, detail))) : null,
    diagnostic.next_steps?.length ? h("div", {},
      h("strong", {}, "Recorded recommendations"),
      h("ul", {}, ...diagnostic.next_steps.map((step) => h("li", {}, step))),
    ) : null,
    h("p", { class: "small" }, "This run is closed. You can discuss the evidence here and submit a revised request for new work. Recommendations do not grant approval or change the recorded outcome."),
    onDiscuss ? h("p", { class: "conversation-diagnostic-ask" }, "This button prepares a question in Ask mode. It appears as your message; the assistant's explanation appears separately only after you send it.") : null,
    onDiscuss ? h("button", { class: "btn btn-small", type: "button", onClick: () => onDiscuss(turn.id) }, "Discuss how to resolve this") : null,
    h("a", { class: "text-link", href: `#/runs/${encodeURIComponent(turn.id)}/audit` },
      diagnostic.policy_rule ? `View audit · ${diagnostic.policy_rule}` : "View run evidence"),
  );
}

export class SessionRuntimeStatus {
  constructor(root) {
    this.root = root;
    this.controller = new AbortController();
    this.loading = false;
    this.load();
  }

  dispose() {
    this.controller.abort();
  }

  async load() {
    if (this.loading || this.controller.signal.aborted) return;
    this.loading = true;
    if (this.refresh) this.refresh.disabled = true;
    let status;
    try {
      status = await api.toolRuntimeStatus({ signal: this.controller.signal });
    } catch {
      status = { status: "unknown", summary: "Python runtime availability could not be checked.", next_step: "Recheck before requesting Python execution." };
    }
    this.loading = false;
    if (this.controller.signal.aborted) return;
    this.refresh = h("button", { class: "btn btn-small", type: "button", onClick: () => this.load() }, "Recheck Python runtime");
    replace(this.root, h("div", { class: `session-runtime-status notice tone-${status.status === "ready" ? "neutral" : "warn"}` },
      h("span", {}, status.summary),
      status.status !== "ready" && status.next_step ? h("p", { class: "small" }, status.next_step) : null,
      status.status === "unavailable" ? h("p", { class: "small" }, "You can continue the conversation. Tools that do not require Python remain available.") : null,
      this.refresh,
    ));
  }
}
