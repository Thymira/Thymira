import { h } from "../../dom.js";
import { clip, timestamp } from "../../format.js";
import { foldApprovals } from "../../fold.js";
import { TERMINAL_STATUSES } from "../../status.js";
import { codeBlock, copyable, empty, kv, mono, panel, table, tag } from "../../ui.js";
import { approvalReviewContext } from "../session-review.js";

export function renderApprovals(ctx) {
  const { pending, resolved, superseded, parkedDecisionId, focus } = foldApprovals(ctx.events);
  const waiting = ctx.run.status === "WAITING_FOR_APPROVAL";
  const ended = TERMINAL_STATUSES.has(ctx.run.status);
  return h(
    "div",
    { class: "stack" },
    panel(
      `${ended ? "Never answered" : "Pending review"} (${pending.length})`,
      pending.length
        ? h(
            "div",
            { class: "cards" },
            [...pending].reverse().map((request) =>
              reviewCard(ctx, request, {
                answerable: waiting && focus?.decisionId === request.decisionId,
                parked: waiting && request.decisionId === parkedDecisionId,
                waiting,
                ended,
              }),
            ),
          )
      : empty(ended ? "No unresolved review remains." : "Nothing is waiting for a human answer."),
      { aside: h("span", { class: "muted small" }, "each answer applies to the recorded decision shown below") },
    ),
    superseded.length ? panel(
      `Superseded by risk clarification (${superseded.length})`,
      h("p", { class: "muted small" }, "These decisions were replaced by an information request. No approval or rejection was recorded."),
      table([
        { label: "Review", render: (request) => clip(request.summary || request.reason, 120) },
        { label: "Superseded", render: (request) => timestamp(request.supersededBy.ts) },
      ], [...superseded].reverse()),
    ) : null,
    panel(
      `Answered (${resolved.length})`,
      table(
        [
          { label: "Answered", render: (request) => timestamp(request.resolution.ts) },
          {
            label: "Answer",
            render: (request) => [
              tag(request.resolution.approved ? "APPROVED" : "REJECTED", request.resolution.approved ? "ok" : "block"),
              request.resolution.automatic ? [" ", tag("AUTOMATIC")] : null,
            ],
          },
          { label: "Request", render: (request) => clip(request.summary || request.reason, 100) },
          { label: "Tool", render: (request) => (request.toolCall ? mono(request.toolCall.tool) : h("span", { class: "muted" }, "run")) },
          {
            label: "By",
            render: (request) => mono(`${request.resolution.actor?.kind ?? "?"}/${request.resolution.actor?.id ?? "?"}`),
          },
          { label: "Note", render: (request) => clip(request.resolution.note, 80) || "—" },
        ],
        [...resolved].reverse(),
        { emptyText: "No review has been answered yet.", expand: (request) => detail(request) },
      ),
    ),
  );
}

function detail(request) {
  return h(
    "div",
    { class: "stack-tight" },
    kv([
      ["Decision", copyable(request.decisionId)],
      ["Rule", request.ruleId ? mono(request.ruleId) : null],
      ["Reason", request.reason],
      ["Requested", timestamp(request.ts)],
      ["Note", request.resolution?.note],
    ]),
    request.toolCall ? codeBlock(request.toolCall.arguments ?? {}) : null,
  );
}

function unanswerableHint({ waiting, ended }) {
  if (ended) return "The run ended before this review was answered; it can no longer be answered.";
  if (waiting) return "The run is parked on a different review; answer that one first.";
  return "The run is not waiting on this review right now.";
}

function reviewCard(ctx, request, { answerable, parked, waiting, ended }) {
  const review = approvalReviewContext(ctx.events, request);
  const note = h("textarea", { class: "input", rows: 2, placeholder: "Note recorded with your answer (optional)" });
  const status = h("span", { class: "form-status", role: "status" });
  const approve = h("button", { class: "btn btn-approve", type: "button" }, review.review.kind === "tool" ? "Approve this call" : "Approve this review");
  const reject = h("button", { class: "btn btn-danger", type: "button" }, review.review.kind === "tool" ? "Reject this call" : "Reject this review");
  const setBusy = (busy) => {
    approve.disabled = busy;
    reject.disabled = busy;
    note.disabled = busy;
  };
  const decide = (approved) => async () => {
    setBusy(true);
    status.textContent = approved ? "Recording your answer for this decision…" : "Recording the rejection…";
    try {
      const text = note.value.trim();
      const run = approved
        ? await ctx.api.approve(ctx.runId, text, request.decisionId)
        : await ctx.api.reject(ctx.runId, text, request.decisionId);
      note.value = "";
      status.textContent = approved ? "Approved." : "Rejected.";
      await ctx.onRunMutated(run);
    } catch (error) {
      status.textContent = `${error?.code ?? "error"}: ${error?.message ?? error}`;
      setBusy(false);
    }
  };
  approve.addEventListener("click", decide(true));
  reject.addEventListener("click", decide(false));
  return h(
    "article",
    { class: ["card", answerable ? "is-answerable" : null] },
    h("header", { class: "card-head" }, h("strong", null, review.title), parked ? tag("RUN PARKED HERE", "review") : null),
    h("p", { class: "muted small" }, `Requested ${timestamp(request.ts)}`),
    review.details,
    answerable
      ? h("div", { class: "decide" }, note, h("div", { class: "form-actions" }, status, reject, approve))
      : h("p", { class: "muted small" }, unanswerableHint({ waiting, ended })),
  );
}
