import assert from "node:assert/strict";
import { test } from "node:test";
import { HIDDEN_EVENT_TYPES, foldThread, stageLabel } from "../../../apps/web/src/thymira/web/static/js/thread-fold.js";

let seq = 0;
const at = (n) => new Date(Date.UTC(2026, 8, 15, 10, 0, n)).toISOString();
function ev(type, payload = {}, extra = {}) {
  seq += 1;
  return { seq, ts: at(seq), type, payload, actor: { kind: "system", id: "system" }, subject_id: null, ...extra };
}
const run = (over = {}) => ({ id: "run_1", prompt: "Profile german_credit", status: "RUNNING", created_at: at(0), final_decision: null, error: null, ...over });

test("the prompt opens the thread and hidden kinds never appear", () => {
  seq = 0;
  const items = foldThread(run(), [ev("run.started"), ev("policy.decision", { decision: "PASS" }), ev("model.selected", { model: "m" })]);
  assert.deepEqual(items.map((item) => item.kind), ["prompt"]);
  assert.equal(items[0].text, "Profile german_credit");
  assert.ok(HIDDEN_EVENT_TYPES.has("policy.decision"));
});

test("questions and answers keep their source, and stage changes become dividers", () => {
  seq = 0;
  const items = foldThread(run(), [
    ev("run.transitioned", { command: "start", stage: "planning", state: { stage: "planning", condition: "active" } }),
    ev("activity_profile.questioned", { question_number: 1, field: "purpose", question: "**What** is the purpose?" }),
    ev("activity_profile.answered", { field: "purpose", answer: "Scoring", answer_source: "live_human", answered_by: "operator" }, { actor: { kind: "human", id: "operator" } }),
    ev("activity_profile.answered", { field: "jurisdiction", answer: "EU", answer_source: "project_context", source_ref: ".thymira/context.md" }),
    ev("run.transitioned", { command: "begin_execution", stage: "executing", state: { stage: "executing", condition: "active" } }),
  ]);
  assert.deepEqual(items.map((item) => item.kind), ["prompt", "question", "answer", "answer", "plan", "stage"]);
  assert.equal(items[1].number, 1);
  assert.equal(items[2].source, "live_human");
  assert.equal(items[3].source, "project_context");
  assert.equal(items[5].stage, "executing");
  assert.equal(stageLabel("experimenting"), "Executing");
});

test("an agent groups its messages, tool calls, reviews and summary; chunks concatenate by request", () => {
  seq = 0;
  const events = [
    ev("agent.started", { agent: "data", agent_id: "agent_1", task_id: "task_1", objective: "Profile it" }),
    ev("model.response_chunk", { request_id: "req_1", source_sequence: 0, message: { role: "assistant", content: "Hello " } }),
    ev("model.response_chunk", { request_id: "req_1", source_sequence: 1, message: { role: "assistant", content: "world" }, terminal: { outcome: "success", chunk_count: 2 } }),
    ev("model.response_chunk", { request_id: "req_2", source_sequence: 0, message: { role: "assistant", content: "", tool_calls: [{ name: "profile_dataset" }] }, terminal: { outcome: "success", chunk_count: 1 } }),
    ev("human.approval_requested", { decision_id: "dec_1", summary: "profile_dataset", reason: "side effects", tool: "profile_dataset", arguments: { name: "german_credit" }, tool_intent_sha256: "abc" }),
    ev("tool.denied", { tool: "profile_dataset", tool_call_id: "call_1", tool_intent_sha256: "abc", task_id: "task_1", reason: "denied for review", decision_id: "dec_1", arguments: { name: "german_credit" } }),
    ev("agent.parked", { agent: "data", agent_id: "agent_1", status: "PENDING" }),
    ev("human.approval", { decision_id: "dec_1", approved: true, note: "go" }, { actor: { kind: "human", id: "operator" } }),
    ev("agent.started", { agent: "data", agent_id: "agent_1", task_id: "task_1" }),
    ev("agent.message", { agent: "data", form: "runtime_context", text: "Current runtime context" }),
    ev("tool.started", { tool: "profile_dataset", tool_call_id: "call_2", tool_intent_sha256: "abc", task_id: "task_1" }),
    ev("tool.completed", { tool: "profile_dataset", tool_call_id: "call_2", tool_intent_sha256: "abc", status: "COMPLETED", result_code: "SUCCESS", artifact_ids: ["art_1"] }),
    ev("agent.completed", { agent: "data", agent_id: "agent_1", status: "COMPLETED" }),
    ev("agent.message", { agent: "data", status: "COMPLETED", text: "data completed: 1000 rows" }),
  ];
  const items = foldThread(run(), events);
  assert.deepEqual(items.map((item) => item.kind), ["prompt", "agent", "agent_summary"]);
  const agent = items[1];
  assert.equal(agent.name, "data");
  assert.equal(agent.status, "COMPLETED");
  assert.deepEqual(agent.items.map((item) => item.kind), ["message", "review", "tool"]);
  assert.equal(agent.items[0].text, "Hello world");
  assert.equal(agent.items[0].agent, "data");
  assert.equal(agent.items[1].resolution.approved, true);
  assert.equal(agent.items[2].status, "COMPLETED");
  assert.deepEqual(agent.items[2].artifactIds, ["art_1"]);
  assert.equal(agent.items[2].denials, 1);
  assert.ok(agent.version >= events.at(-2).seq);
});

test("structured JSON content is flagged, the audit nests its agents, and a terminal run ends with an outcome", () => {
  seq = 0;
  const events = [
    ev("risk.classified", { risk_profile: { risk_level: "high", activity_category: "model_development", confidence: 0.94, needs_human_review: true, risk_factors: ["a"], missing_information: [] } }),
    ev("model.response_chunk", { request_id: "req_9", source_sequence: 0, message: { role: "assistant", content: "{\"tasks\":[]}" }, terminal: { outcome: "success", chunk_count: 1 } }),
    ev("audit.started", {}),
    ev("agent.started", { agent: "euaiact", agent_id: "agent_9" }),
    ev("agent.completed", { agent: "euaiact", agent_id: "agent_9", status: "COMPLETED" }),
    ev("audit.finding", { finding: { id: "f1", severity: "MEDIUM", title: "Confinement enforced", control_id: "A9" } }),
    ev("audit.completed", { status: "PASS" }),
    ev("run.transitioned", { command: "complete", stage: "reporting", state: { stage: "reporting", condition: "terminal", outcome: "completed" } }),
    ev("run.completed", {}),
  ];
  const items = foldThread(run({ status: "COMPLETED", final_decision: "WARNING" }), events);
  assert.deepEqual(items.map((item) => item.kind), ["prompt", "risk", "message", "audit", "stage", "outcome"]);
  assert.equal(items[1].level, "high");
  assert.equal(items[2].structured, true);
  assert.equal(items[2].agent, "THY");
  assert.deepEqual(items[3].items.map((item) => item.kind), ["agent"]);
  assert.equal(items[3].findings.length, 1);
  assert.equal(items[3].status, "PASS");
  assert.equal(items[5].decision, "WARNING");
});
