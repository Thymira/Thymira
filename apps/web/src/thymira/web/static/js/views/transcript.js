// The transcript: the conversation a thread reads as, rendered from the ThreadItem[] that
// thread-fold.js folds out of the Run's event log.
//
// Nothing here reads or changes Run state. Every value the API returned reaches the page as a text
// node or as safe markdown (markdown.js parses text, never HTML), so a prompt, a tool argument or
// a model's answer can never introduce markup.
//
// Updates are keyed: an item whose `version` did not change keeps the very element it already had,
// so an open agent group, an expanded tool row and a half-typed review note all survive the 400 ms
// batch that redraws the thread while it runs.

import { h, replace } from "../dom.js";
import { clip, clock, integer, percent, plainQuestion, relative, timestamp } from "../format.js";
import { icon } from "../icons.js";
import { renderMarkdown } from "../markdown.js";
import { href } from "../router.js";
import { outcomeTone, riskTone } from "../status.js";
import { stageLabel } from "../thread-fold.js";
import {
  asyncBlock,
  avatar,
  chips,
  codeBlock,
  copyable,
  decisionTag,
  empty,
  findingList,
  kv,
  mono,
  notice,
  statusTag,
  table,
  tag,
} from "../ui.js";

const PIN_SLACK_PX = 80;
const MAX_STAGGER = 8;
const OBJECTIVE_CHARS = 90;

// foldTools names a call's lifecycle with its own vocabulary; the row's colour keys on the wire
// ToolCallStatus values, so the two internal placeholders are translated here and nowhere else.
const ROW_STATUS = { PROPOSED: "PENDING", REQUESTED: "PENDING", STARTED: "RUNNING" };

// A group stays open while its agent is still working and closes once it is done, unless the
// operator said otherwise: their toggle wins for the rest of the thread's life.
const OPEN_STATUSES = new Set(["RUNNING", "PARKED", "PENDING", null, undefined]);

function rowStatus(status) {
  const name = String(status ?? "").toUpperCase();
  return ROW_STATUS[name] ?? name;
}

function firstArgument(args) {
  if (!args || typeof args !== "object") return "";
  const entries = Object.entries(args);
  if (!entries.length) return "";
  return entries
    .slice(0, 2)
    .map(([key, value]) => `${key}=${clip(typeof value === "string" ? value : JSON.stringify(value), 60)}`)
    .join(" · ");
}

// Put `nodes` in order under `parent`, touching only what actually moved. The transcript is
// append-only, so an item that did not change is never detached — which is what keeps a half-typed
// review note, its focus and an expanded row alive across the 400 ms batch that redraws the thread.
function syncChildren(parent, nodes) {
  let index = 0;
  for (const node of nodes) {
    if (parent.childNodes[index] === node) {
      index += 1;
      continue;
    }
    parent.insertBefore(node, parent.childNodes[index] ?? null);
    index += 1;
  }
  while (parent.childNodes.length > index) parent.lastChild.remove();
}

function turn(kind, face, name, ts, ...body) {
  return h(
    "article",
    { class: "turn", dataset: { kind } },
    face,
    h(
      "div",
      { class: "turn-head" },
      h("span", { class: "turn-name" }, name),
      ts ? h("span", { class: "turn-time" }, clock(ts)) : null,
    ),
    h("div", { class: "turn-body" }, body),
  );
}

function agentFace(name) {
  const label = String(name ?? "");
  if (label === "THY") return avatar("thy");
  if (label === "MIRA") return avatar("mira");
  return avatar("agent", label.slice(0, 1));
}

function chevron() {
  return icon("chevron-right", { size: 14, className: "chevron" });
}

// ---------------------------------------------------------------- item renderers

function renderPrompt(item) {
  return turn("prompt", avatar("user"), "You", item.ts, h("div", { class: "user-card" }, item.text));
}

function renderQuestion(item) {
  const label = ["Question", item.number ? ` ${item.number}` : "", item.field ? ` · ${item.field}` : ""].join("");
  return turn(
    "question",
    avatar("mira"),
    "MIRA",
    item.ts,
    h("span", { class: "eyebrow" }, label),
    h("div", { class: "prose" }, renderMarkdown(plainQuestion(item.text))),
  );
}

function renderAnswer(item) {
  if (item.source === "project_context") {
    return h(
      "div",
      { class: "stage-divider" },
      ["Answered from context.md", item.field ? ` · ${item.field}` : ""].join(""),
    );
  }
  return turn(
    "answer",
    avatar("user"),
    item.by ? `You · ${item.by}` : "You",
    item.ts,
    h("div", { class: "user-card" }, item.text),
  );
}

function renderRisk(item) {
  return turn(
    "risk",
    avatar("mira"),
    "MIRA",
    item.ts,
    h(
      "div",
      { class: "risk-card" },
      h(
        "div",
        { class: "card-head" },
        tag(String(item.level ?? "unknown").toUpperCase(), riskTone(item.level)),
        h("strong", null, item.category ?? "activity"),
        item.confidence === null ? null : h("span", { class: "muted small" }, `confidence ${percent(item.confidence)}`),
        item.needsHumanReview ? tag("HUMAN REVIEW REQUIRED", "review") : null,
      ),
      item.factors.length ? chips(item.factors) : null,
      item.missing.length ? chips(item.missing, "warn") : null,
      h("span", { class: "muted small" }, "A classification, never an authorization."),
    ),
  );
}

function renderPlanItem(item, view) {
  const body = asyncBlock(
    () => view.deps.loadPlan(),
    (plan) => [
      h(
        "div",
        { class: "card-head" },
        h("strong", null, plan.goal || "Plan"),
        plan.goal_status ? tag(plan.goal_status, outcomeTone(plan.goal_status)) : null,
        h("span", { class: "muted small" }, `revision ${integer(plan.revision)}`),
      ),
      plan.items?.length
        ? h(
            "ol",
            { class: "plan-items" },
            [...plan.items]
              .sort((left, right) => left.ordinal - right.ordinal)
              .map((entry) =>
                h(
                  "li",
                  { class: "plan-item", dataset: { state: String(entry.state ?? "") } },
                  h("span", null, entry.text),
                  tag(entry.state, outcomeTone(entry.state)),
                ),
              ),
          )
        : empty("The plan has no items yet."),
    ],
    { quiet: { plan_not_found: "THY has not published a plan for this thread yet." } },
  );
  return turn("plan", avatar("thy"), "THY", item.ts, h("div", { class: "plan-card" }, body));
}

function renderMessage(item) {
  const name = item.agent || "THY";
  if (item.structured) {
    let parsed = item.text;
    try {
      parsed = JSON.parse(item.text);
    } catch {
      // Keep the raw text: the fold only marked it as parseable, it did not parse it for us.
    }
    return turn(
      "message",
      agentFace(name),
      name,
      item.ts,
      h(
        "details",
        { class: "structured" },
        h("summary", null, "Structured output"),
        codeBlock(parsed),
      ),
    );
  }
  return turn("message", agentFace(name), name, item.ts, h("div", { class: "prose" }, renderMarkdown(item.text)));
}

function renderAgentSummary(item) {
  const name = item.agent || "THY";
  return turn(
    "agent_summary",
    agentFace(name),
    name,
    item.ts,
    h("div", { class: "agent-summary prose" }, renderMarkdown(item.text)),
  );
}

function renderStage(item) {
  return h("div", { class: "stage-divider" }, stageLabel(item.stage));
}

function toolDetail(item, view) {
  const executed = item.attempts.some((attempt) => attempt.type === "tool.completed");
  return h(
    "div",
    { class: ["tool-detail", item.attempts.length > 1 ? "is-wide" : null] },
    item.error ? notice(typeof item.error === "string" ? item.error : JSON.stringify(item.error), "block") : null,
    item.reason && !executed ? notice(item.reason, "warn") : null,
    h(
      "div",
      { class: "stack-tight" },
      h("span", { class: "muted small" }, "Arguments"),
      codeBlock(item.args ?? {}),
    ),
    item.attempts.length
      ? h(
          "div",
          { class: "stack-tight" },
          h("span", { class: "muted small" }, `Attempts (${integer(item.attempts.length)})`),
          table(
            [
              { label: "Seq", numeric: true, render: (attempt) => attempt.seq },
              { label: "Event", render: (attempt) => mono(attempt.type) },
              { label: "Detail", render: (attempt) => clip(attempt.detail, 70) || "—" },
            ],
            item.attempts,
            { className: "compact" },
          ),
        )
      : null,
    kv([
      item.resultCode ? ["Result", tag(item.resultCode, outcomeTone(item.resultCode))] : null,
      item.exitCode === null || item.exitCode === undefined ? null : ["Exit code", integer(item.exitCode)],
      item.agent ? ["Agent", item.agent] : null,
      item.denials ? ["Held for review", `${integer(item.denials)}×`] : null,
      item.artifactIds.length
        ? [
            "Artifacts",
            h(
              "span",
              { class: "chips" },
              item.artifactIds.map((id) =>
                h(
                  "a",
                  { class: "mono", href: href({ view: "thread", runId: view.deps.runId, panel: "artifacts", item: id }) },
                  id,
                ),
              ),
            ),
          ]
        : null,
    ]),
  );
}

function renderTool(item, view) {
  const status = rowStatus(item.status);
  const row = h(
    "summary",
    { class: "tool-row no-marker", dataset: { status } },
    icon("tool", { size: 15 }),
    h("span", { class: "tool-name" }, item.tool),
    h("span", { class: "tool-summary" }, firstArgument(item.args)),
    tag(status, outcomeTone(item.status)),
    item.artifactIds.length ? tag(`${integer(item.artifactIds.length)} artifacts`, "neutral") : null,
    // Wrapped, so the row's [data-status] colour reaches only the leading tool glyph.
    h("span", null, chevron()),
  );
  const node = h("details", null, row, toolDetail(item, view));
  return { node };
}

function reviewResolution(item) {
  const resolution = item.resolution;
  const who = resolution.automatic
    ? "automatically"
    : `by ${resolution.actor?.kind ?? "human"}/${resolution.actor?.id ?? "?"}`;
  return h(
    "p",
    { class: "muted small" },
    `${resolution.approved ? "Approved" : "Rejected"} ${who} · ${timestamp(resolution.ts)}`,
    resolution.note ? ` · ${resolution.note}` : "",
  );
}

function renderReview(item, view) {
  const answerable = view.isAnswerable(item);
  const card = h(
    "article",
    { class: ["review-card", answerable ? "is-answerable" : null] },
    h(
      "div",
      { class: "card-head" },
      icon("shield", { size: 15 }),
      h("strong", null, item.summary || "Review requested"),
      answerable ? tag("WAITING FOR YOU", "review") : null,
    ),
    kv([
      ["Reason", item.reason],
      item.ruleId ? ["Rule", mono(item.ruleId)] : null,
      ["Decision", copyable(item.decisionId)],
      item.sandboxMode ? ["Sandbox", mono(item.sandboxMode)] : null,
      item.expiresAt ? ["Expires", timestamp(item.expiresAt)] : null,
    ]),
    item.toolCall
      ? h(
          "div",
          { class: "stack-tight" },
          h("span", { class: "muted small" }, "Tool ", mono(item.toolCall.tool), " with these exact arguments"),
          codeBlock(item.toolCall.arguments ?? {}),
        )
      : null,
    h("span", { class: "muted small" }, "An approval authorizes exactly this call, once."),
  );
  if (item.resolution) card.append(reviewResolution(item));
  if (!answerable) return { node: card };
  const note = h("input", { class: "input input-small", type: "text", placeholder: "Note recorded with your answer (optional)", "aria-label": "Note" });
  const status = h("span", { class: "form-status", role: "status" });
  const approve = h("button", { class: "btn btn-approve", type: "button" }, item.toolCall ? "Approve this call" : "Approve");
  const reject = h("button", { class: "btn btn-danger", type: "button" }, "Reject");
  const decide = (approved) => () => {
    approve.disabled = true;
    reject.disabled = true;
    note.disabled = true;
    status.textContent = approved ? "Recording the approval…" : "Recording the rejection…";
    const answer = approved ? view.deps.onApprove : view.deps.onReject;
    answer?.(item.decisionId, note.value.trim());
  };
  approve.addEventListener("click", decide(true));
  reject.addEventListener("click", decide(false));
  card.append(h("div", { class: "review-actions" }, note, status, reject, approve));
  return { node: card };
}

function renderOutcome(item) {
  const tone = item.status === "COMPLETED" ? "ok" : "block";
  const again = h("button", { class: "btn btn-small", type: "button" }, "Run again");
  const card = h(
    "article",
    { class: `outcome-card tone-${tone}` },
    h(
      "div",
      { class: "outcome-head" },
      statusTag(item.status),
      decisionTag(item.decision),
      h("span", { class: "muted small" }, timestamp(item.ts)),
    ),
    item.error ? notice(item.error, "block") : null,
    h("div", { class: "form-actions" }, again),
  );
  return { node: card, wire: (view) => again.addEventListener("click", () => view.deps.onRunAgain?.()) };
}

// ---------------------------------------------------------------- containers

function renderAgent(item) {
  const face = h("span");
  const name = h("span", { class: "agent-name" });
  const objective = h("span", { class: "agent-objective" });
  const status = h("span");
  const elapsed = h("span", { class: "muted small" });
  const summary = h("summary", { class: "no-marker" }, face, name, objective, status, elapsed, chevron());
  const children = h("div", { class: "agent-children" });
  const node = h("details", { class: "agent-group" }, summary, children);
  node.addEventListener("toggle", () => {
    node.dataset.userToggled = "true";
  });
  const refresh = (next) => {
    replace(face, agentFace(next.name));
    replace(name, next.name);
    replace(objective, next.objective ? clip(next.objective, OBJECTIVE_CHARS) : "");
    replace(status, tag(next.status ?? "RUNNING", outcomeTone(next.status)));
    replace(elapsed, relative(next.endTs ?? next.ts));
    if (!node.dataset.userToggled) node.open = OPEN_STATUSES.has(next.status);
  };
  refresh(item);
  return { node, children, refresh };
}

function renderAudit(item) {
  const status = h("span");
  const count = h("span", { class: "muted small" });
  const summary = h(
    "summary",
    { class: "no-marker" },
    avatar("mira"),
    h("span", { class: "agent-name" }, "MIRA audit"),
    h("span", { class: "agent-objective" }, "checks the run against methodology and regulation"),
    status,
    count,
    chevron(),
  );
  const children = h("div", { class: "agent-children" });
  const findings = h("div", { class: "audit-body" });
  const node = h("details", { class: "audit-group" }, summary, children, findings);
  node.addEventListener("toggle", () => {
    node.dataset.userToggled = "true";
  });
  const refresh = (next) => {
    replace(status, next.status ? tag(next.status, outcomeTone(next.status)) : tag("RUNNING", "active"));
    replace(count, `${integer(next.findings.length)} findings`);
    replace(findings, findingList(next.findings));
    if (!node.dataset.userToggled) node.open = !next.status;
  };
  refresh(item);
  return { node, children, refresh };
}

const RENDERERS = {
  prompt: renderPrompt,
  question: renderQuestion,
  answer: renderAnswer,
  risk: renderRisk,
  plan: renderPlanItem,
  message: renderMessage,
  agent_summary: renderAgentSummary,
  stage: renderStage,
  tool: renderTool,
  review: renderReview,
  outcome: renderOutcome,
  agent: renderAgent,
  audit: renderAudit,
};

// Every ThreadItem kind foldThread can produce; a kind missing here would silently vanish from the
// transcript, so the set is exported and checked rather than assumed.
export const RENDERER_KINDS = Object.keys(RENDERERS);

export class Transcript {
  constructor(root, deps = {}) {
    this.root = root;
    this.deps = deps;
    this.nodes = new Map();
    this.opts = {};
    this.working = null;
    this.pinned = true;
    this.column = h("div", { class: "transcript-column" });
    this.pill = h(
      "button",
      { class: "scroll-pill", type: "button", hidden: true, onClick: () => this.scrollToBottom() },
      icon("arrow-down", { size: 14 }),
      "New activity",
    );
    replace(root, this.column, this.pill);
    root.addEventListener(
      "scroll",
      () => {
        this.pinned = this.atBottom();
        if (this.pinned) this.pill.hidden = true;
      },
      { passive: true },
    );
  }

  isAnswerable(item) {
    return Boolean(this.opts.answerable) && item.decisionId === this.opts.focusDecisionId && !item.resolution;
  }

  // What, besides the item's own version, changes how it is drawn.
  signature(item) {
    if (item.kind === "review") return this.isAnswerable(item) ? "answerable" : "plain";
    return "";
  }

  render(items, opts = {}) {
    this.opts = opts;
    const wasPinned = this.pinned;
    const created = { count: 0 };
    const nodes = this.renderList(Array.isArray(items) ? items : [], created);
    syncChildren(this.column, this.working ? [...nodes, this.working] : nodes);
    if (wasPinned) this.scrollToBottom();
    else if (created.count) this.pill.hidden = false;
  }

  renderList(items, created) {
    const nodes = [];
    for (const item of items) {
      const entry = this.nodes.get(item.key);
      const signature = this.signature(item);
      if (entry && entry.kind === item.kind && entry.refresh) {
        entry.refresh(item);
        entry.version = item.version;
        syncChildren(entry.children, this.renderList(item.items ?? [], created));
        nodes.push(entry.node);
        continue;
      }
      if (entry && entry.kind === item.kind && entry.version === item.version && entry.signature === signature) {
        nodes.push(entry.node);
        continue;
      }
      const built = this.build(item, created);
      if (!built) continue;
      this.nodes.set(item.key, { kind: item.kind, version: item.version, signature, ...built });
      nodes.push(built.node);
    }
    return nodes;
  }

  build(item, created) {
    const make = RENDERERS[item.kind];
    if (!make) return null;
    const result = make(item, this);
    const built = result instanceof Node ? { node: result } : result;
    built.wire?.(this);
    if (built.children) syncChildren(built.children, this.renderList(item.items ?? [], created));
    // The stagger only reads well for the first few arrivals; beyond that it becomes a wait.
    built.node.dataset.enter = "true";
    built.node.style.setProperty("--i", String(Math.min(created.count, MAX_STAGGER)));
    created.count += 1;
    return built;
  }

  setWorking(label) {
    if (!label) {
      this.working?.remove();
      this.working = null;
      return;
    }
    const wasPinned = this.pinned;
    if (!this.working) {
      this.working = h(
        "div",
        { class: "working-row", role: "status" },
        avatar("thy"),
        h("span", { class: "dots", "aria-hidden": "true" }, h("i"), h("i"), h("i")),
        h("span", { class: "working-label" }),
      );
    }
    replace(this.working.querySelector(".working-label"), label);
    this.column.append(this.working);
    if (wasPinned) this.scrollToBottom();
  }

  scrollToKey(key) {
    const entry = this.nodes.get(key);
    if (!entry) return;
    if (entry.node.tagName === "DETAILS") entry.node.open = true;
    entry.node.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  atBottom() {
    return this.root.scrollHeight - this.root.scrollTop - this.root.clientHeight <= PIN_SLACK_PX;
  }

  scrollToBottom() {
    this.root.scrollTop = this.root.scrollHeight;
    this.pinned = true;
    this.pill.hidden = true;
  }
}
