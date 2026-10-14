import { api, readAllEvents } from "../api.js";
import { h, replace } from "../dom.js";
import { list, percent, timestamp } from "../format.js";
import { foldApprovals } from "../fold.js";
import { buildApprovalReview, humanizeField } from "../review-model.js";
import { codeBlock, copyable, empty, kv, loading, mono, notice, panel, tag } from "../ui.js";

function turnRunId(turn) {
  return turn?.id ?? turn?.run?.id ?? null;
}

function evidenceKey(events, request) {
  if (!request) return null;
  const decision = events.find((event) => event.type === "policy.decision" && event.payload?.id === request.decisionId);
  const parked = [...events].reverse().find((event) =>
    event.type === "run.transitioned" && event.payload?.command === "wait_for_approval" &&
    (event.payload?.policy_decision?.id ?? event.payload?.policy_decision_id ?? event.payload?.decision_id) === request.decisionId,
  );
  return [request.decisionId, request.seq, decision?.seq ?? "missing-decision", parked?.seq ?? "missing-park"].join(":");
}

function approvalWaiting(turn) {
  if (turn?.state && typeof turn.state === "object") {
    return turn.state.condition === "waiting" && turn.state.wait_reason === "approval";
  }
  return String(turn?.run?.status ?? "").toUpperCase() === "WAITING_FOR_APPROVAL";
}

function listRows(values) {
  if (!Array.isArray(values) || !values.length) return empty("None recorded.");
  return h("ul", { class: "review-list" }, values.map((value) => h("li", null, value)));
}

function riskDetails(risk) {
  if (!risk) return notice("No risk classification was attached to this review request.", "warn");
  const factors = Array.isArray(risk.risk_factors) ? risk.risk_factors : [];
  const missing = Array.isArray(risk.missing_information) ? risk.missing_information.map(humanizeField) : [];
  return h(
    "div",
    { class: "stack-tight" },
    kv([
      ["Risk level", String(risk.risk_level ?? "Unknown").replaceAll("_", " ")],
      ["Activity category", humanizeField(risk.activity_category ?? "unknown")],
      ["Confidence", percent(risk.confidence)],
    ]),
    h("strong", { class: "small" }, "Recorded risk factors"),
    listRows(factors),
    h("strong", { class: "small" }, "Information still missing"),
    listRows(missing),
  );
}

function interviewLimitDetails(value) {
  if (!value) return notice("This older review has no interview details attached, and no matching question events were returned.", "warn");
  const counts = value.questionsAsked ?? {};
  const count = (current, maximum) => Number.isInteger(current)
    ? Number.isInteger(maximum) ? `${current} of ${maximum}` : `${current} recorded; limit unavailable`
    : "Unavailable in the recorded evidence";
  const lastAnswer = value.lastAnswer;
  const judgment = lastAnswer?.sufficiency
    ? `${lastAnswer.sufficiency.sufficient === false ? "Insufficient" : lastAnswer.sufficiency.sufficient === true ? "Sufficient" : "Judgment recorded"}${lastAnswer.sufficiency.reason ? ` · ${lastAnswer.sufficiency.reason}` : ""}`
    : null;
  const reasonLabel = ({
    missing_activity_profile: "Activity profile still has a missing fact",
    per_field_question_limit: "Per-field question limit reached",
    total_question_limit: "Total interview question limit reached",
  })[value.reason] ?? value.reason ?? "Reason unavailable in the recorded evidence";
  return panel(
    "Interview limit record",
    kv([
      ["Reason", reasonLabel],
      ["Field", value.field ? humanizeField(value.field) : "Unavailable in the recorded evidence"],
      ["Questions for this field", count(counts.fieldTotal, counts.maxPerField)],
      ["Questions overall", count(counts.total, counts.maxTotal)],
      ["Last question", value.lastQuestion?.question ?? "Unavailable in the recorded evidence"],
      ["Last answer", lastAnswer?.answer ?? "No answer was recorded for the last question."],
      ["Answer judgment", judgment],
      ["Other unresolved fields", value.remainingFields?.length ? list(value.remainingFields.map(humanizeField)) : null],
    ]),
  );
}

function riskAssessmentDetails(review) {
  if (!review.riskAssessment) return null;
  return panel(
    "Risk uncertainty recorded for this review",
    kv([
      ["Specific uncertainty", review.riskAssessment.justification],
      ["Assessment confidence", percent(review.riskAssessment.confidence)],
      ["Unresolved facts", review.riskAssessment.missing_information?.length
        ? list(review.riskAssessment.missing_information.map(humanizeField))
        : "None listed in this assessment"],
      ["Known facts in the assessed profile", review.knownFacts?.length
        ? h("ul", { class: "review-list" }, review.knownFacts.map((fact) => h("li", null, `${humanizeField(fact.field)}: ${fact.value}`)))
        : "No known facts were attached to this assessment."],
    ]),
  );
}

function constraintDetails(constraints) {
  if (!constraints || typeof constraints !== "object") return null;
  const rows = [];
  if (constraints.local_execution_only === true) rows.push(["Execution", "Local execution only"]);
  if (Array.isArray(constraints.allowed_tools) && constraints.allowed_tools.length) {
    rows.push(["Allowed tools", list(constraints.allowed_tools)]);
  }
  if (Array.isArray(constraints.prohibited_tools) && constraints.prohibited_tools.length) {
    rows.push(["Prohibited tools", list(constraints.prohibited_tools)]);
  }
  if (Array.isArray(constraints.required_evidence) && constraints.required_evidence.length) {
    rows.push(["Required evidence", list(constraints.required_evidence)]);
  }
  if (Array.isArray(constraints.prohibited_actions) && constraints.prohibited_actions.length) {
    rows.push(["Prohibited actions", list(constraints.prohibited_actions)]);
  }
  if (typeof constraints.max_tool_calls === "number") rows.push(["Maximum tool calls", constraints.max_tool_calls]);
  if (!rows.length) return null;
  return panel("Execution constraints recorded by policy", kv(rows));
}

function consequence(title, text, tone) {
  return h(
    "div",
    { class: `review-consequence tone-${tone}` },
    h("strong", null, title),
    h("p", null, text),
  );
}

function disclosure(title, child) {
  return h("details", { class: "review-disclosure" }, h("summary", null, title), child);
}

function findingDetails(review) {
  if (!review.findingIds.length) return empty("This policy decision does not list finding IDs.");
  if (!review.findings.length) {
    return notice(`The decision names ${review.findingIds.length} finding(s), but matching audit.finding events were not returned.`, "warn");
  }
  return h(
    "div",
    { class: "stack" },
    review.findings.map((finding) =>
      panel(
        finding.title ?? "Recorded finding",
        h(
          "div",
          { class: "stack-tight" },
          kv([
            ["Control", mono(finding.control_id)],
            ["Severity", tag(String(finding.severity ?? "Unknown").toUpperCase(), "warn")],
            ["Finding", finding.finding],
            ["Confidence", percent(finding.confidence)],
            ["Recommendation", finding.recommendation],
          ]),
          h("strong", { class: "small" }, "Evidence recorded with this finding"),
          Array.isArray(finding.evidence) && finding.evidence.length
            ? h(
                "ul",
                { class: "review-list" },
                finding.evidence.map((evidence) =>
                  h("li", null, `${humanizeField(evidence.kind)} · ${evidence.ref}`, evidence.note ? ` · ${evidence.note}` : ""),
                ),
              )
            : empty("The finding lists no evidence references."),
        ),
      ),
    ),
  );
}

function reviewSpecifics(review) {
  const scope = review.scope;
  const summary = panel(
    "Decision summary",
    kv([
      ["Action", scope.action],
      ...(["execution_start", "risk_uncertainty"].includes(review.kind) && review.requestedWork
        ? [["Your request", h("p", { class: "review-request-text" }, review.requestedWork)]] : []),
      ["Affected inputs or targets", scope.targets.length ? listRows(scope.targets) : empty("No specific inputs or targets are named in this request.")],
      ...(review.kind === "risk_uncertainty" && review.knownFacts?.length
        ? [["Known facts from the assessed profile", h("ul", { class: "review-list" }, review.knownFacts.map((fact) => h("li", null, `${humanizeField(fact.field)}: ${fact.value}`)))]]
        : []),
      ...(scope.outputs.length ? [["Outputs named in the request", listRows(scope.outputs)]] : []),
      ["Recorded effects and limits", scope.effects.length ? listRows(scope.effects) : empty("The request does not record additional effects.")],
    ]),
  );
  const consequences = [
    scope.approveOutcome ? consequence("If you approve", scope.approveOutcome, "ok") : null,
    scope.rejectOutcome ? consequence("If you reject", scope.rejectOutcome, "block") : null,
  ];
  const decision = review.decision;
  const policyDetails = decision
    ? panel(
        "Policy decision",
        kv([
          ["Decision ID", copyable(decision.id)],
          ["Outcome", tag(decision.decision ?? "Unknown", "review")],
          ["Rule", decision.rule_id ? mono(decision.rule_id) : null],
          ["Policy reason", decision.reason],
          ["Subject", `${decision.subject_kind ?? "unknown"}${decision.subject_id ? ` · ${decision.subject_id}` : ""}`],
          ["Policy pack", review.parked?.payload?.policy_decision?.policy_name],
          ["Policy fingerprint", review.parked?.payload?.policy_decision?.policy_sha256 ? mono(review.parked.payload.policy_decision.policy_sha256) : null],
        ]),
      )
    : notice("The verified event record does not contain this policy decision. Answers stay disabled until it can be read.", "warn");
  const recordedRequest = review.request.summary || review.request.reason
    ? panel("Recorded request", kv([["Summary", review.request.summary], ["Reason", review.request.reason]]))
    : null;
  const risk = ["execution_start", "risk_uncertainty"].includes(review.kind)
    ? panel("Risk assessment details", riskDetails(review.riskProfile))
    : null;
  const riskAssessment = review.kind === "risk_uncertainty" ? riskAssessmentDetails(review) : null;
  const interviewLimit = review.kind === "interview_limit" ? interviewLimitDetails(review.interviewLimit) : null;
  const constraints = constraintDetails(review.executionConstraints);
  let specific = null;
  if (review.kind === "tool") {
    const effectValue = review.sideEffects;
    const effectNode = effectValue
      ? Array.isArray(effectValue)
        ? listRows(effectValue.map(humanizeField))
        : h("p", null, String(effectValue))
      : notice("No separate side-effect list is attached to this request. See the recorded policy reason above.", "warn");
    specific = panel(
      "Exact tool request",
      h("div", { class: "stack-tight" },
        kv([
          ["Tool", mono(review.toolCall?.tool ?? "Unknown tool")],
          ["Decision", copyable(review.decisionId)],
          ["Sandbox", review.sandboxMode ? mono(review.sandboxMode) : null],
          ["Approval expires", review.expiresAt ? timestamp(review.expiresAt) : null],
        ]),
        h("span", { class: "muted small" }, "Arguments exactly as recorded"),
        codeBlock(review.toolCall?.arguments ?? {}),
        typeof (review.toolCall?.arguments?.code ?? review.toolCall?.arguments?.content) === "string"
          ? panel("Submitted code or file content", codeBlock(review.toolCall.arguments.code ?? review.toolCall.arguments.content)) : null,
        h("strong", { class: "small" }, "Side effects listed in the request"),
        effectNode,
      ),
    );
  } else if (review.kind === "findings") {
    specific = panel("Findings named by this decision", findingDetails(review));
  } else if (review.kind === "plan") {
    specific = panel(
      "Recorded plan",
      review.plan ? codeBlock(review.plan) : notice("No plan content is attached to the recorded approval events.", "warn"),
    );
  }
  const references = h("div", { class: "stack" }, recordedRequest, policyDetails, risk, riskAssessment, constraints);
  return [
    summary,
    ...consequences,
    interviewLimit,
    specific ? disclosure(review.kind === "tool" ? "Inspect exact code and arguments" : "Inspect the recorded plan or findings", specific) : null,
    disclosure("Policy references and event details", references),
  ];
}

function kindLabel(kind) {
  return ({
    risk_uncertainty: "Risk classification review",
    interview_limit: "MIRA preflight · unresolved answer review",
    execution_start: "Run start review",
    tool: "Tool call review",
    findings: "Findings review",
    plan: "Plan review",
  })[kind] ?? "Policy review";
}

export function approvalReviewContext(events, request) {
  const review = buildApprovalReview(events, request);
  return { review, title: kindLabel(review.kind), details: reviewSpecifics(review) };
}

function answerLabels(kind) {
  return ({
    risk_uncertainty: ["Approve continuation", "Reject continuation"],
    interview_limit: ["Accept unresolved facts and continue", "Reject and stop Run"],
    execution_start: ["Approve run start", "Reject run start"],
    tool: ["Approve this call", "Reject this call"],
    findings: ["Approve findings decision", "Reject findings decision"],
    plan: ["Approve plan", "Reject plan"],
  })[kind] ?? ["Approve this review", "Reject this review"];
}

export class SessionReviewView {
  constructor(root, { onResolved, onClarifyRisk } = {}) {
    this.root = root;
    this.onResolved = onResolved;
    this.onClarifyRisk = onClarifyRisk;
    this.turn = null;
    this.runId = null;
    this.focus = null;
    this.review = null;
    this.events = [];
    this.disposed = false;
    this.loading = false;
    this.stale = false;
    this.eventError = null;
    this.actionError = null;
    this.inFlight = false;
    this.loadGeneration = 0;
    this.controller = null;
    this.form = null;
    this.renderedEvidenceKey = null;
    this.renderedNoFocus = false;
    this.root.hidden = true;
  }

  dispose() {
    this.disposed = true;
    this.loadGeneration += 1;
    this.controller?.abort();
    this.root.hidden = true;
    this.root.replaceChildren();
  }

  update(turn) {
    if (this.disposed) return;
    const nextRunId = turnRunId(turn);
    if (!turn || !nextRunId) {
      this.cancelRead();
      this.turn = null;
      this.runId = null;
      this.focus = null;
      this.review = null;
      this.form = null;
      this.renderedEvidenceKey = null;
      this.content = null;
      this.staleNotice = null;
      this.root.hidden = true;
      this.root.replaceChildren();
      return;
    }

    const changedRun = nextRunId !== this.runId;
    if (changedRun) {
      this.cancelRead();
      this.runId = nextRunId;
      this.events = [];
      this.focus = null;
      this.review = null;
      this.form = null;
      this.renderedEvidenceKey = null;
      this.content = null;
      this.staleNotice = null;
      this.root.replaceChildren();
      this.actionError = null;
    }
    this.turn = turn;
    if (!approvalWaiting(turn)) {
      this.cancelRead();
      this.focus = null;
      this.review = null;
      this.form = null;
      this.content = null;
      this.staleNotice = null;
      this.root.hidden = true;
      this.root.replaceChildren();
      return;
    }

    this.root.hidden = false;
    this.stale = Boolean(turn.stale || turn.isStale || turn.loading || turn.isLoading || turn.refreshStale);
    if (this.stale) {
      if (!this.content) this.renderShell();
      const message = "The session snapshot is stale or still loading. Refresh before answering.";
      if (this.form) this.setStatus(message, true);
      else if (!this.staleNotice || this.staleNotice.parentNode !== this.content) {
        this.staleNotice = notice(message, "warn");
        this.content.append(this.staleNotice);
      }
      this.syncControls();
      return;
    }
    if (this.loading) {
      return;
    }
    this.refreshEvents();
  }

  cancelRead() {
    this.loadGeneration += 1;
    this.controller?.abort();
    this.controller = null;
    this.loading = false;
  }

  async refreshEvents() {
    if (this.disposed || !this.runId || !approvalWaiting(this.turn)) return;
    if (this.loading) return;
    const generation = ++this.loadGeneration;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    this.loading = true;
    const hasCurrentDecision = Boolean(this.focus && this.review?.decision && this.form?.decisionId === this.focus.decisionId);
    if (!hasCurrentDecision) this.renderShell(loading("Reading the verified review record…"));
    this.syncControls();
    try {
      const events = await readAllEvents(this.runId, { signal: controller.signal });
      if (this.disposed || generation !== this.loadGeneration || controller.signal.aborted) return;
      this.eventError = null;
      this.events = events;
      const nextFocus = foldApprovals(events).focus;
      if (nextFocus?.decisionId !== this.focus?.decisionId) this.actionError = null;
      const nextReview = nextFocus ? buildApprovalReview(events, nextFocus) : null;
      const nextEvidenceKey = nextFocus ? evidenceKey(events, nextFocus) : null;
      const shouldRender = nextEvidenceKey !== this.renderedEvidenceKey ||
        (Boolean(nextFocus) && !this.form) ||
        (!nextFocus && !this.renderedNoFocus);
      this.focus = nextFocus;
      this.review = nextReview;
      if (shouldRender) {
        this.renderReview();
        this.renderedEvidenceKey = nextEvidenceKey;
      }
    } catch (error) {
      if (this.disposed || generation !== this.loadGeneration || error?.name === "AbortError") return;
      this.eventError = error;
      const message = `Could not refresh approval evidence: ${error?.code ?? "error"}: ${error?.message ?? error}`;
      if (this.focus && this.content) this.setStatus(message, true);
      else this.renderShell(notice(message, "block"));
    } finally {
      if (this.disposed || generation !== this.loadGeneration) return;
      this.loading = false;
      this.controller = null;
      this.syncControls();
    }
  }

  renderShell(child = null) {
    const content = h("div", { class: "session-review-content" });
    this.content = content;
    this.staleNotice = null;
    this.renderedEvidenceKey = null;
    this.renderedNoFocus = false;
    replace(
      this.root,
      h("section", { class: "session-review stack", "aria-labelledby": "session-review-title" },
        h("header", { class: "session-review-head" },
          h("div", null,
            h("p", { class: "eyebrow" }, "Approval needed"),
            h("h2", { id: "session-review-title" }, this.review ? kindLabel(this.review.kind) : "Run review"),
          ),
          this.focus ? tag("WAITING", "review") : null,
        ),
        content,
      ),
    );
    if (child) content.append(child);
    this.form = null;
  }

  renderReview() {
    const previousForm = this.form;
    const preserveForm = Boolean(previousForm && previousForm.decisionId === this.focus?.decisionId);
    this.renderShell();
    if (!this.focus) {
      this.content.append(notice("This run is waiting for approval, but no open request matches the exact parked decision. No answer is available here.", "warn"));
      this.renderedNoFocus = true;
      return;
    }
    const context = approvalReviewContext(this.events, this.focus);
    this.review = context.review;
    for (const part of context.details) if (part) this.content.append(part);
    if (this.review.kind === "interview_limit") {
      this.content.append(notice("MIRA stopped asking because the interview reached its question limit. Review the last answer and why it was judged insufficient. Accepting leaves that uncertainty recorded; execution and tool approvals still apply. This continues the same Run.", "warn"));
    }
    if (this.onClarifyRisk && ["execution_start", "risk_uncertainty"].includes(this.review.kind)) {
      const clarify = h("button", { class: "btn", type: "button" }, "Review risk context");
      clarify.addEventListener("click", () => this.clarifyRisk());
      this.content.append(panel(
        "Review the risk context",
        h("div", { class: "stack-tight" },
          h("p", null, "Add or correct context in the session before deciding. This action does not approve the pending review."),
          clarify,
        ),
      ));
    }
    if (preserveForm) {
      this.form = previousForm;
      this.content.append(previousForm.panelNode);
    } else {
      const status = h("p", { class: "form-status", role: "status", "aria-live": "polite" });
      const note = h("textarea", {
        class: "input",
        rows: 2,
        placeholder: "Note recorded with your answer (optional)",
        "aria-label": "Optional approval note",
      });
      const [approveLabel, rejectLabel] = answerLabels(this.review.kind);
      const reject = h("button", { class: "btn btn-danger", type: "button" }, rejectLabel);
      const approve = h("button", { class: "btn btn-approve", type: "button" }, approveLabel);
      reject.addEventListener("click", () => this.resolve(false));
      approve.addEventListener("click", () => this.resolve(true));
      const actions = h("div", { class: "form-actions" }, status, reject, approve);
      const panelNode = panel(
        "Your answer",
        h("div", { class: "decide" },
          note,
          h("p", { class: "muted small" }, "The answer is recorded against this exact decision ID."),
          actions,
        ),
      );
      this.form = { note, status, reject, approve, actions, panelNode, decisionId: this.focus.decisionId };
      this.content.append(panelNode);
    }
    this.syncControls();
  }

  setStatus(message, isError = false) {
    if (!this.form?.status) return;
    this.form.status.textContent = message;
    this.form.status.className = isError ? "form-status tone-block" : "form-status";
  }

  clarifyRisk() {
    if (!this.onClarifyRisk || !this.focus || !this.review || this.stale || this.eventError || this.inFlight) return;
    const missingInformation = Array.isArray(this.review.riskProfile?.missing_information)
      ? [...this.review.riskProfile.missing_information]
      : [];
    this.onClarifyRisk({
      runId: this.runId,
      decisionId: this.focus.decisionId,
      kind: this.review.kind,
      reason: this.review.request.summary ?? this.review.decision?.reason ?? null,
      missingInformation,
    });
  }

  syncControls() {
    if (!this.form) return;
    const unavailable = this.disposed || this.stale || Boolean(this.eventError) || !approvalWaiting(this.turn) || !this.review?.decision;
    const canUseVisibleDecision = Boolean(this.focus && this.review?.decision && this.form.decisionId === this.focus.decisionId);
    this.form.note.disabled = unavailable || this.inFlight;
    this.form.approve.disabled = unavailable || (this.loading && !canUseVisibleDecision) || this.inFlight;
    this.form.reject.disabled = unavailable || (this.loading && !canUseVisibleDecision) || this.inFlight;
    if (this.eventError) {
      this.setStatus(`Could not refresh approval evidence: ${this.eventError?.code ?? "error"}: ${this.eventError?.message ?? this.eventError}`, true);
    } else if (this.actionError) {
      this.setStatus(`Answer was not recorded: ${this.actionError?.code ?? "error"}: ${this.actionError?.message ?? this.actionError}`, true);
    } else if (this.loading && !canUseVisibleDecision) {
      this.setStatus("Reading the verified review record…");
    } else if (this.stale) {
      this.setStatus("The session snapshot is stale or still loading. Refresh before answering.", true);
    } else if (this.inFlight) {
      this.setStatus(`Recording your answer for ${this.form.decisionId}…`);
    } else {
      this.setStatus("");
    }
  }

  async resolve(approved) {
    if (this.disposed || this.inFlight || !this.form || this.form.approve.disabled) return;
    const { decisionId, note } = this.form;
    const runId = this.runId;
    const text = note.value.trim();
    this.inFlight = true;
    this.actionError = null;
    this.syncControls();
    try {
      const run = approved
        ? await api.approve(runId, text, decisionId)
        : await api.reject(runId, text, decisionId);
      if (this.disposed) return;
      await this.onResolved?.({ runId, decisionId, approved, run });
      if (this.disposed) return;
      this.inFlight = false;
      if (!this.loading) this.refreshEvents();
    } catch (error) {
      if (this.disposed) return;
      this.inFlight = false;
      if (this.runId === runId && this.form?.decisionId === decisionId) {
        this.actionError = error;
        this.syncControls();
      }
      if (!this.loading) this.refreshEvents();
    }
  }
}
