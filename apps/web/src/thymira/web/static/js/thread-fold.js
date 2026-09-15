// The transcript fold: one pure pass over a Run's redacted event log that turns it into the
// conversation the operator reads. It is unit-tested with node and imports only ./fold.js.
//
// It groups what the log already says and never infers anything from it: no authorization, no
// status the events did not record, nothing added to a Run. Events the transcript does not show
// are still in the inspector's Events panel; this fold simply does not have a shape for them.

import { foldAgents, foldApprovals, foldTools } from "./fold.js";

// Shown in the inspector's Events panel, never in the transcript (spec §7.2). `agent.message`
// with a `form` (runtime_context, resume_history_compacted) is hidden by the same rule, but it is
// a payload test rather than a type, so it is not in this set.
export const HIDDEN_EVENT_TYPES = new Set([
  "policy.decision",
  "model.selected",
  "model.request_recorded",
  "model.input_header_revised",
  "model.input_surface_updated",
  "model.route_selected",
  "activity_profile.recorded",
  "risk_assessment.recorded",
  "pack_binding.recorded",
  "control_evaluation.recorded",
  "artifact.created",
  "artifact.invalidated",
  "subagent.settled",
  "context.compacted",
  "turn.ended",
]);

const TERMINAL_STATUSES = new Set(["COMPLETED", "BLOCKED", "FAILED"]);
const EXECUTING_STAGES = new Set(["executing", "experimenting"]);
const STAGE_LABELS = {
  planning: "Planning",
  executing: "Executing",
  experimenting: "Executing",
  auditing: "Auditing",
  reporting: "Reporting",
};

export function stageLabel(stage) {
  const name = String(stage ?? "").toLowerCase();
  if (STAGE_LABELS[name]) return STAGE_LABELS[name];
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : "—";
}

const actorId = (actor) => (typeof actor === "string" ? actor : (actor?.id ?? null));

function structuredText(text) {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return false;
  try {
    JSON.parse(trimmed);
    return true;
  } catch {
    return false;
  }
}

// A container holds `{ pos, item }` entries; `pos` is the seq of the item's first event, so the
// transcript reads in log order whatever order the folds produced the items in.
const container = () => ({ entries: [] });

function place(target, pos, item) {
  const entry = { pos, item };
  target.entries.push(entry);
  return entry;
}

function materialise(target) {
  // Array.prototype.sort is stable, so two items anchored on the same event keep insertion order
  // (the plan card is inserted before the stage divider that revealed it).
  return target.entries.sort((left, right) => left.pos - right.pos).map((entry) => entry.item);
}

function bump(item, seq) {
  item.version = Math.max(item.version ?? seq, seq);
}

function closeContainer(item, target) {
  item.items = materialise(target);
  for (const child of item.items) bump(item, child.version ?? 0);
}

export function foldThread(run, events) {
  const log = Array.isArray(events) ? events : [];
  const root = container();
  const openAgents = [];
  const agentsById = new Map();
  const chunks = new Map();
  const containers = [{ seq: Number.NEGATIVE_INFINITY, target: root }];
  const audits = [];
  const approvalSeqs = new Map();
  let openAudit = null;
  let previousStage = null;
  let planEntry = null;

  const current = () => openAgents[openAgents.length - 1]?.target ?? root;
  const mark = (seq) => containers.push({ seq, target: current() });
  const containerAt = (seq) => {
    let target = root;
    for (const checkpoint of containers) {
      if (checkpoint.seq > seq) break;
      target = checkpoint.target;
    }
    return target;
  };

  const ensurePlan = (seq, ts) => {
    if (planEntry) {
      planEntry.pos = Math.min(planEntry.pos, seq);
      return planEntry.item;
    }
    planEntry = place(root, seq, { kind: "plan", key: "plan", ts, version: seq });
    return planEntry.item;
  };

  if (run) {
    place(root, Number.NEGATIVE_INFINITY, {
      kind: "prompt",
      key: "prompt",
      text: String(run.prompt ?? ""),
      ts: run.created_at ?? null,
      version: 0,
    });
  }

  for (const event of log) {
    const payload = event.payload ?? {};
    const seq = event.seq;
    const ts = event.ts;
    if (event.type.startsWith("plan.")) {
      bump(ensurePlan(seq, ts), seq);
      continue;
    }
    switch (event.type) {
      case "run.transitioned": {
        const stage = payload.stage ?? payload.state?.stage ?? null;
        if (!stage || stage === previousStage) break;
        if (EXECUTING_STAGES.has(stage)) ensurePlan(seq, ts);
        // The first move into "planning" is the Run starting; it needs no divider of its own.
        if (previousStage !== null || stage !== "planning") {
          place(root, seq, { kind: "stage", key: `stage:${seq}`, stage, ts, version: seq });
        }
        previousStage = stage;
        break;
      }
      case "activity_profile.questioned":
        place(root, seq, {
          kind: "question",
          key: `question:${seq}`,
          number: payload.question_number ?? null,
          field: payload.field ?? null,
          text: payload.question ?? "",
          ts,
          version: seq,
        });
        break;
      case "activity_profile.answered":
        place(root, seq, {
          kind: "answer",
          key: `answer:${seq}`,
          field: payload.field ?? null,
          text: payload.answer ?? "",
          source: payload.answer_source ?? null,
          by: actorId(payload.answered_by) ?? actorId(event.actor),
          ts,
          version: seq,
        });
        break;
      case "risk.classified": {
        const profile = payload.risk_profile;
        if (!profile) break;
        place(root, seq, {
          kind: "risk",
          key: `risk:${seq}`,
          level: profile.risk_level ?? null,
          category: profile.activity_category ?? null,
          confidence: typeof profile.confidence === "number" ? profile.confidence : null,
          needsHumanReview: Boolean(profile.needs_human_review),
          factors: Array.isArray(profile.risk_factors) ? profile.risk_factors : [],
          missing: Array.isArray(profile.missing_information) ? profile.missing_information : [],
          ts,
          version: seq,
        });
        break;
      }
      case "model.response_chunk": {
        const message = payload.message;
        if (!message || message.role !== "assistant") break;
        const requestId = payload.request_id ?? event.subject_id ?? `seq:${seq}`;
        let group = chunks.get(requestId);
        if (!group) {
          group = {
            requestId,
            pos: seq,
            ts,
            version: seq,
            agent: openAgents[openAgents.length - 1]?.item.name ?? "THY",
            target: current(),
            parts: new Map(),
          };
          chunks.set(requestId, group);
        }
        group.version = Math.max(group.version, seq);
        const order = typeof payload.source_sequence === "number" ? payload.source_sequence : group.parts.size;
        group.parts.set(order, typeof message.content === "string" ? message.content : "");
        break;
      }
      case "agent.started": {
        const id = payload.agent_id ?? event.subject_id;
        if (!id) break;
        const known = agentsById.get(id);
        if (known) {
          // A parked agent that starts again extends the same group rather than opening a second.
          if (payload.objective && !known.item.objective) known.item.objective = payload.objective;
          if (!openAgents.includes(known)) {
            openAgents.push(known);
            mark(seq);
          }
          bump(known.item, seq);
          break;
        }
        const target = container();
        const item = {
          kind: "agent",
          key: `agent:${id}`,
          id,
          name: payload.agent ?? "agent",
          objective: payload.objective ?? null,
          status: "RUNNING",
          framework: payload.framework ?? null,
          depth: typeof payload.delegation_depth === "number" ? payload.delegation_depth : null,
          items: [],
          ts,
          endTs: null,
          version: seq,
        };
        const entry = { id, item, target };
        agentsById.set(id, entry);
        // THY's sub-agents name their parent; MIRA's pack agents do not, so an agent that starts
        // while an audit is open and claims no THY parent belongs to that audit's group.
        const parent = current();
        if (parent === root && openAudit && !isThyChild(payload)) place(openAudit.target, seq, item);
        else place(parent, seq, item);
        if (openAudit && parent === root && !isThyChild(payload)) openAudit.anchor(seq, ts);
        openAgents.push(entry);
        mark(seq);
        break;
      }
      case "agent.parked": {
        const entry = agentsById.get(payload.agent_id ?? event.subject_id);
        if (!entry) break;
        // Parking records why the agent is waiting; the work that resumes belongs to the same
        // group, so the group stays open until agent.completed closes it.
        entry.item.status = payload.status ?? "PARKED";
        entry.item.endTs = ts;
        bump(entry.item, seq);
        break;
      }
      case "agent.completed": {
        const entry = agentsById.get(payload.agent_id ?? event.subject_id);
        if (!entry) break;
        entry.item.status = payload.status ?? "COMPLETED";
        entry.item.endTs = ts;
        bump(entry.item, seq);
        const index = openAgents.indexOf(entry);
        if (index >= 0) {
          openAgents.length = index;
          mark(seq);
        }
        break;
      }
      case "agent.message": {
        if (payload.form) break;
        place(current(), seq, {
          kind: "agent_summary",
          key: `summary:${seq}`,
          agent: payload.agent ?? "THY",
          status: payload.status ?? null,
          text: payload.text ?? "",
          ts,
          version: seq,
        });
        break;
      }
      case "audit.started": {
        const item = {
          kind: "audit",
          key: `audit:${seq}`,
          status: null,
          findings: [],
          items: [],
          ts,
          endTs: null,
          version: seq,
        };
        const target = container();
        const entry = place(root, seq, item);
        // The audit opens at preflight, long before MIRA's own work shows in the log, so the card
        // anchors on the first thing the audit itself recorded: a pack agent or a finding.
        const audit = {
          item,
          target,
          anchored: false,
          anchor(at, atTs) {
            if (this.anchored) return;
            this.anchored = true;
            entry.pos = at;
            item.ts = atTs;
          },
        };
        audits.push(audit);
        openAudit = audit;
        break;
      }
      case "audit.finding": {
        const audit = openAudit ?? audits[audits.length - 1];
        if (!audit || !payload.finding) break;
        audit.item.findings.push(payload.finding);
        audit.anchor(seq, ts);
        bump(audit.item, seq);
        break;
      }
      case "audit.completed": {
        const audit = openAudit ?? audits[audits.length - 1];
        if (!audit) break;
        audit.item.status = payload.status ?? payload.audit_report?.status ?? null;
        audit.item.endTs = ts;
        audit.anchor(seq, ts);
        bump(audit.item, seq);
        openAudit = null;
        break;
      }
      case "human.approval":
        if (payload.decision_id) approvalSeqs.set(payload.decision_id, seq);
        break;
      default:
        break;
    }
  }

  for (const group of chunks.values()) {
    const text = [...group.parts.entries()].sort((left, right) => left[0] - right[0]).map(([, part]) => part).join("");
    if (!text.trim()) continue;
    place(group.target, group.pos, {
      kind: "message",
      key: `message:${group.requestId}`,
      agent: group.agent,
      text,
      structured: structuredText(text),
      ts: group.ts,
      version: group.version,
    });
  }

  const { byTask } = foldAgents(log);
  for (const call of foldTools(log, byTask)) {
    const seqs = call.attempts.map((attempt) => attempt.seq);
    const first = seqs.length ? Math.min(...seqs) : 0;
    const last = seqs.length ? Math.max(...seqs) : 0;
    place(containerAt(first), first, {
      kind: "tool",
      key: `tool:${call.key}`,
      tool: call.tool,
      args: call.arguments,
      status: call.status,
      resultCode: call.resultCode,
      exitCode: call.exitCode,
      error: call.error,
      reason: call.reason,
      denials: call.denials,
      artifactIds: call.artifactIds,
      attempts: call.attempts,
      agent: call.agent,
      ts: tsOf(log, first),
      version: last,
    });
  }

  const approvals = foldApprovals(log);
  for (const request of [...approvals.pending, ...approvals.resolved]) {
    const answeredAt = approvalSeqs.get(request.decisionId) ?? request.seq;
    place(containerAt(request.seq), request.seq, {
      kind: "review",
      key: `review:${request.decisionId}`,
      decisionId: request.decisionId,
      summary: request.summary,
      reason: request.reason,
      ruleId: request.ruleId,
      toolCall: request.toolCall,
      sandboxMode: request.sandboxMode,
      expiresAt: request.expiresAt,
      resolution: request.resolution ?? null,
      ts: request.ts,
      version: request.resolution ? answeredAt : request.seq,
    });
  }

  for (const entry of agentsById.values()) closeContainer(entry.item, entry.target);
  for (const audit of audits) closeContainer(audit.item, audit.target);

  if (run && TERMINAL_STATUSES.has(run.status)) {
    place(root, Number.POSITIVE_INFINITY, {
      kind: "outcome",
      key: "outcome",
      status: run.status,
      decision: run.final_decision ?? null,
      error: run.error ?? null,
      ts: run.completed_at ?? log[log.length - 1]?.ts ?? null,
      version: log[log.length - 1]?.seq ?? 0,
    });
  }

  return materialise(root);
}

function isThyChild(payload) {
  const parent = payload.parent_agent;
  return Boolean(parent) && parent !== "mira";
}

function tsOf(log, seq) {
  for (const event of log) {
    if (event.seq === seq) return event.ts;
  }
  return null;
}
