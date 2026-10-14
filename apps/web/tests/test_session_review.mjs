import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { foldApprovals } from "../src/thymira/web/static/js/fold.js";
import { buildApprovalReview } from "../src/thymira/web/static/js/review-model.js";
import { outcomeTone } from "../src/thymira/web/static/js/status.js";
import { SessionReviewView } from "../src/thymira/web/static/js/views/session-review.js";

test("incomplete audit reports use a warning tone", () => {
  assert.equal(outcomeTone("incomplete"), "warn");
});

class FakeNode {
  constructor(tagName = "#text", text = "") {
    this.tagName = tagName.toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.className = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.text = text;
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
    };
  }

  append(...nodes) {
    for (const node of nodes) {
      const child = node instanceof FakeNode ? node : new FakeNode("#text", String(node));
      child.parentNode = this;
      this.childNodes.push(child);
    }
  }

  replaceChildren(...nodes) {
    this.childNodes = [];
    this.text = "";
    this.append(...nodes);
  }

  addEventListener(name, callback) {
    this.listeners.set(name, callback);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  set textContent(value) {
    this.replaceChildren();
    if (value !== "" && value !== null && value !== undefined) this.append(new FakeNode("#text", String(value)));
  }

  get textContent() {
    return this.text + this.childNodes.map((child) => child.textContent).join("");
  }

  querySelectorAll(selector) {
    const selectors = selector.split(",").map((item) => item.trim());
    const matches = (node) => selectors.some((item) =>
      item.startsWith(".") ? node.classList.contains(item.slice(1)) : node.tagName.toLowerCase() === item,
    );
    const found = [];
    const visit = (node) => {
      for (const child of node.childNodes) {
        if (matches(child)) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return found;
  }
}

class FakeTextNode extends FakeNode {
  constructor(text) {
    super("#text", String(text));
  }
}

const originals = {};

function installBrowser(fetchImpl) {
  for (const key of ["Node", "document", "window", "sessionStorage", "fetch"]) {
    originals[key] = globalThis[key];
  }
  globalThis.Node = FakeNode;
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => new FakeTextNode(text),
  };
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.window = { location: { origin: "http://localhost:8080" }, dispatchEvent() {} };
  globalThis.fetch = fetchImpl;
}

afterEach(() => {
  for (const [key, value] of Object.entries(originals)) {
    if (value === undefined) delete globalThis[key];
    else globalThis[key] = value;
  }
});

function response(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function event(seq, type, payload, subjectId = null) {
  return { seq, event_id: `event_${seq}`, type, payload, subject_id: subjectId, ts: "2026-09-27T10:00:00Z", actor: null };
}

function startEvents(decisionId = "decision_start") {
  return [
    event(1, "risk.classified", {
      risk_profile: {
        risk_level: "high",
        activity_category: "model_development",
        confidence: 0.8,
        risk_factors: ["automated_decision_support"],
        missing_information: ["affected_population", "decision_role", "deployment_context", "human_oversight", "intended_use", "potential_consequences"],
      },
    }),
    event(2, "policy.decision", {
      id: decisionId,
      subject_kind: "run",
      subject_id: "run_1",
      decision: "REQUIRE_HUMAN_REVIEW",
      rule_id: "review_execution_start",
      reason: "The risk assessment requires human review.",
      execution_constraints: { local_execution_only: true },
    }),
    event(3, "human.approval_requested", {
      decision_id: decisionId,
      summary: "execution.start: risk high (model_development); confidence 0.80",
      reason: "The risk assessment requires a human decision.",
      rule_id: "review_execution_start",
    }, "run_1"),
    event(4, "run.transitioned", {
      command: "wait_for_approval",
      resume_from: "execution_start",
      policy_decision: { id: decisionId, subject_kind: "run" },
    }, "run_1"),
  ];
}

function toolEvents(decisionId = "decision_tool") {
  return [
    event(1, "policy.decision", {
      id: decisionId,
      subject_kind: "tool_call",
      subject_id: "call_1",
      decision: "REQUIRE_HUMAN_REVIEW",
      rule_id: "review_workspace_write",
      reason: "The requested tool capability needs review.",
    }),
    event(2, "human.approval_requested", {
      decision_id: decisionId,
      summary: "write_file",
      reason: "Review this exact call.",
      tool: "write_file",
      arguments: { path: "reports/result.csv", content: "id,score\\n1,0.7" },
      sandbox_mode: "workspace",
      tool_intent_sha256: "d".repeat(64),
    }, "call_1"),
    event(3, "run.transitioned", {
      command: "wait_for_approval",
      policy_decision: { id: decisionId, subject_kind: "tool_call" },
    }, "run_1"),
  ];
}

function riskUncertaintyEvents(decisionId = "decision_uncertain") {
  return [
    event(1, "policy.decision", {
      id: decisionId,
      subject_kind: "run",
      subject_id: "run_1",
      decision: "REQUIRE_HUMAN_REVIEW",
      rule_id: "GOV-006",
      reason: "A human must review this Run-control request before it changes the Run state.",
    }),
    event(2, "human.approval_requested", {
      decision_id: decisionId,
      summary: "MIRA could not classify inherent risk with sufficient confidence after one attempt; human review is required.",
      reason: "A human must review this Run-control request before it changes the Run state.",
      rule_id: "GOV-006",
    }, "run_1"),
    event(3, "run.transitioned", {
      command: "wait_for_approval",
      resume_from: "risk_uncertainty",
      policy_decision: { id: decisionId, subject_kind: "run", policy_name: "base+credit-risk@1.0" },
    }, "run_1"),
  ];
}

function interviewLimitEvents({
  decisionId = "decision_interview_limit",
  reason = "per_field_question_limit",
  requestPayload = {
    review_kind: "missing_activity_profile",
    reason: "per_field_question_limit",
    field: "decision_effect",
    questions_asked: { total: 4, max_total: 12, field_total: 3, max_per_field: 3 },
    last_question: { event_id: "question_3", field: "decision_effect", question: "Will the model affect a credit decision?" },
    last_answer: {
      event_id: "answer_3",
      answer: "It may be used as one input to the decision.",
      sufficiency: { sufficient: false, reason: "The degree of influence remains unclear." },
    },
    remaining_fields: ["deployment_context", "human_oversight"],
    decision_effects: {
      approve: "Continue without this missing fact; later checks still apply.",
      reject: "Stop this Run at the interview limit.",
    },
  },
} = {}) {
  return [
    event(1, "policy.decision", {
      id: decisionId, subject_kind: "run", subject_id: "run_1", decision: "REQUIRE_HUMAN_REVIEW",
      rule_id: "review_interview_limit", reason: "The bounded risk interview needs a decision.",
    }),
    event(2, "human.approval_requested", {
      decision_id: decisionId,
      summary: "The risk interview reached its bounded question limit.",
      reason: "The bounded interview needs a decision.",
      request_payload: { ...requestPayload, reason },
    }, "run_1"),
    event(3, "run.transitioned", {
      command: "wait_for_approval", resume_from: "interview_limit",
      policy_decision: { id: decisionId, subject_kind: "run" },
    }, "run_1"),
  ];
}

function waitingTurn(id = "run_1", stale = false) {
  return {
    id,
    run: { id, status: "WAITING_FOR_APPROVAL" },
    state: { condition: "waiting", wait_reason: "approval", stage: "executing" },
    ...(stale ? { stale: true } : {}),
  };
}

function installEvents(events, extra = () => null) {
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/events")) {
      return response({ items: events, has_more: false, next_after_seq: null });
    }
    return extra(url, init) ?? response({ id: "run_1", status: "WAITING_FOR_APPROVAL" });
  });
}

async function flush() {
  for (let index = 0; index < 16; index += 1) await Promise.resolve();
}

function buttonWithText(root, text) {
  return root.querySelectorAll("button").find((button) => button.textContent === text);
}

test("the parked decision is the only focused approval, with either recorded ID field", () => {
  const events = [
    event(1, "human.approval_requested", { decision_id: "decision_old", summary: "old tool" }),
    event(2, "human.approval_requested", { policy_decision_id: "decision_parked", summary: "execution.start" }),
    event(3, "human.approval_requested", { decision_id: "decision_newer", summary: "new tool" }),
    event(4, "run.transitioned", { command: "wait_for_approval", policy_decision: { id: "decision_parked" } }),
  ];
  const folded = foldApprovals(events);
  assert.equal(folded.focus?.decisionId, "decision_parked");
  assert.equal(folded.pending.length, 3);
  const answered = foldApprovals([
    ...events,
    event(5, "human.approval", { policy_decision_id: "decision_parked", approved: true }),
  ]);
  assert.equal(answered.resolved.find((request) => request.decisionId === "decision_parked")?.resolution.approved, true);
  assert.equal(answered.focus, null);

  const noPark = foldApprovals(events.slice(0, 3));
  assert.equal(noPark.focus?.decisionId, "decision_newer");
});

test("execution-start context displays recorded risk, missing facts and start-only consequences", () => {
  const folded = foldApprovals(startEvents());
  const review = buildApprovalReview(startEvents(), folded.focus);
  assert.equal(review.kind, "execution_start");
  assert.equal(review.riskProfile.activity_category, "model_development");
  assert.deepEqual(review.riskProfile.missing_information, [
    "affected_population", "decision_role", "deployment_context", "human_oversight", "intended_use", "potential_consequences",
  ]);
  assert.equal(review.executionConstraints.local_execution_only, true);
});

test("clarification retires the old review without inventing a human approval", () => {
  const events = [...startEvents(), event(5, "run.transitioned", {
    command: "wait_for_information", resume_from: "risk_clarification", supersedes_decision_id: "decision_start",
  })];
  const folded = foldApprovals(events);
  assert.equal(folded.focus, null);
  assert.equal(folded.pending.length, 0);
  assert.equal(folded.resolved.length, 0);
  assert.equal(folded.superseded[0].decisionId, "decision_start");
});

test("risk uncertainty is distinct from run start and retains its recorded checkpoint", () => {
  const events = riskUncertaintyEvents();
  const review = buildApprovalReview(events, foldApprovals(events).focus);
  assert.equal(review.kind, "risk_uncertainty");
  assert.equal(review.resumeFrom, "risk_uncertainty");
  assert.match(review.request.summary, /could not classify inherent risk/);
});

test("interview-limit review explains the recorded question boundary and decision effects", async () => {
  const events = interviewLimitEvents();
  const request = foldApprovals(events).focus;
  const review = buildApprovalReview(events, request);
  assert.equal(review.kind, "interview_limit");
  assert.equal(review.interviewLimit.field, "decision_effect");
  assert.deepEqual(review.interviewLimit.questionsAsked, {
    total: 4, maxTotal: 12, fieldTotal: 3, maxPerField: 3,
  });
  assert.equal(review.interviewLimit.lastAnswer.sufficiency.reason, "The degree of influence remains unclear.");
  assert.match(review.scope.approveOutcome, /without this missing fact/);
  assert.match(review.scope.approveOutcome, /later checks still apply/);
  assert.match(review.scope.rejectOutcome, /Stop this Run/);

  let submitted;
  installEvents(events, (url, init) => {
    if (url.pathname.endsWith("/approve")) {
      submitted = { body: JSON.parse(init.body), path: url.pathname };
      return response({ id: "run_1", status: "RUNNING" });
    }
    return null;
  });
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  try {
    view.update(waitingTurn());
    await flush();
    assert.match(root.textContent, /MIRA preflight · unresolved answer review/);
    assert.match(root.textContent, /This continues the same Run/);
    assert.match(root.textContent, /Decision effect/);
    assert.match(root.textContent, /3 of 3/);
    assert.match(root.textContent, /4 of 12/);
    assert.match(root.textContent, /Will the model affect a credit decision\?/);
    assert.match(root.textContent, /It may be used as one input to the decision/);
    assert.match(root.textContent, /The degree of influence remains unclear/);
    assert.match(root.textContent, /without this missing fact/);
    assert.match(root.textContent, /later checks still apply/);
    assert.match(root.textContent, /Stop this Run at the interview limit/);
    buttonWithText(root, "Accept unresolved facts and continue").listeners.get("click")();
    await flush();
    assert.equal(submitted.body.expected_decision_id, "decision_interview_limit");
  } finally { view.dispose(); }
});

test("legacy missing-activity-profile review derives only recorded interview counts", () => {
  const events = [
    event(1, "activity_profile.questioned", { field: "deployment_context", question: "Where will this be used?" }),
    event(2, "activity_profile.answered", {
      question_event_id: "event_1", field: "deployment_context", answer: "Offline research.",
      sufficiency: { sufficient: true, reason: "The scope is clear." },
    }),
    event(3, "activity_profile.questioned", { field: "decision_effect", question: "Will this change a decision?" }),
    event(4, "activity_profile.answered", {
      question_event_id: "event_3", field: "decision_effect", answer: "It may inform a review.",
      sufficiency: { sufficient: false, reason: "The level of influence is not clear." },
    }),
    event(5, "policy.decision", {
      id: "decision_legacy_limit", subject_kind: "run", decision: "REQUIRE_HUMAN_REVIEW",
      rule_id: "review_missing_activity_profile", reason: "The profile remains incomplete.",
    }),
    event(6, "human.approval_requested", {
      decision_id: "decision_legacy_limit", summary: "Risk interview limit reached.",
      request_payload: { reason: "missing_activity_profile" },
    }, "run_1"),
    event(7, "run.transitioned", {
      command: "wait_for_approval", resume_from: "interview_limit",
      policy_decision: { id: "decision_legacy_limit" },
    }, "run_1"),
  ];
  const review = buildApprovalReview(events, foldApprovals(events).focus);
  assert.equal(review.kind, "interview_limit");
  assert.equal(review.interviewLimit.questionsAsked.total, 2);
  assert.equal(review.interviewLimit.questionsAsked.fieldTotal, 1);
  assert.equal(review.interviewLimit.questionsAsked.maxTotal, null);
  assert.equal(review.interviewLimit.questionsAsked.maxPerField, null);
  assert.equal(review.interviewLimit.lastQuestion.question, "Will this change a decision?");
  assert.equal(review.interviewLimit.lastAnswer.answer, "It may inform a review.");
  assert.match(review.scope.approveOutcome, /without resolving/);
  assert.match(review.scope.approveOutcome, /Later policy gates and any required tool reviews still apply/);
  assert.match(review.scope.rejectOutcome, /stops the Run/);
});

test("start review identifies the requested work and mandatory per-tool review", () => {
  const events = [event(0, "run.started", { run: { conversation: { message: "Profile the uploaded CSV" }, prompt: "Bounded conversation context" } }), ...startEvents()];
  events.find((item) => item.type === "policy.decision").payload.execution_constraints.requires_human_review = true;
  const review = buildApprovalReview(events, foldApprovals(events).focus);
  assert.equal(review.requestedWork, "Profile the uploaded CSV");
  assert.match(review.scope.effects.join(" "), /Every tool call requires its own human approval/);
  assert.doesNotMatch(review.requestedWork, /Bounded conversation context/);
});

test("uncertainty review explains the exact assessment, known profile facts, and later controls", async () => {
  const events = riskUncertaintyEvents();
  const request = events.find((item) => item.type === "human.approval_requested");
  request.payload.request_payload = { risk_assessment_id: "assessment_shown" };
  events.unshift(event(-1, "activity_profile.recorded", { activity_profile: {
    id: "profile_shown", version: 2, purpose: "Offline model research",
    affected_population: "Historical credit applicants", decision_effect: "Provides a score for human review",
    autonomy: "Human makes the final decision", human_oversight: "Required before any outcome",
    jurisdiction: "European Union", data_categories: ["Credit history"],
  } }));
  events.splice(1, 0, event(0, "risk_assessment.recorded", { risk_assessment: {
    id: "assessment_shown", activity_profile_id: "profile_shown", activity_profile_version: 2,
    confidence: 0.74, justification: "The intended effect on credit decisions is ambiguous.", missing_information: ["decision_effect"],
  } }));
  events.push(event(99, "risk_assessment.recorded", { risk_assessment: { id: "assessment_other", confidence: 1, justification: "Unrelated assessment" } }));
  const review = buildApprovalReview(events, foldApprovals(events).focus);
  assert.match(review.scope.targets.join(" "), /effect on credit decisions is ambiguous/);
  assert.match(review.scope.targets.join(" "), /74%/);
  assert.doesNotMatch(review.scope.targets.join(" "), /Unrelated assessment/);
  assert.equal(review.knownFacts.find((fact) => fact.field === "purpose")?.value, "Offline model research");
  assert.match(review.scope.effects.join(" "), /Known facts from the assessment-linked profile/);
  assert.match(review.scope.approveOutcome, /execution-start Gate/);
  assert.match(review.scope.approveOutcome, /tool reviews still apply/);

  installEvents(events);
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  try {
    view.update(waitingTurn());
    await flush();
    assert.match(root.textContent, /Specific uncertainty/);
    assert.match(root.textContent, /The intended effect on credit decisions is ambiguous/);
    assert.match(root.textContent, /Known facts from the assessed profile/);
    assert.match(root.textContent, /Historical credit applicants/);
    assert.match(root.textContent, /execution-start Gate/);
    assert.match(root.textContent, /tool reviews still apply/);
    assert.doesNotMatch(root.textContent, /Unrelated assessment/);
  } finally { view.dispose(); }
});

test("tool review retains exact arguments and reports absent side-effect facts as unknown", () => {
  const events = toolEvents();
  const request = foldApprovals(events).focus;
  const review = buildApprovalReview(events, request);
  assert.equal(review.kind, "tool");
  assert.equal(review.toolCall.tool, "write_file");
  assert.deepEqual(review.toolCall.arguments, { path: "reports/result.csv", content: "id,score\\n1,0.7" });
  assert.equal(review.sideEffects, null);
  assert.equal(review.scope.action, "Write the requested file into this Run's workspace.");
  assert.deepEqual(review.scope.outputs, ["reports/result.csv"]);
  assert.match(review.scope.effects[0], /Writes the supplied content/);

  const pythonEvents = [
    event(1, "policy.decision", {
      id: "decision_python",
      subject_kind: "tool_call",
      decision: "REQUIRE_HUMAN_REVIEW",
      rule_id: "GOV-008",
      reason: "The tool declares local side effects; a human must review the execution.",
    }),
    event(2, "human.approval_requested", {
      decision_id: "decision_python",
      tool: "run_python",
      sandbox_mode: "workspace_write",
      arguments: {
        code: "print(1)",
        description: "Perform exploratory data analysis on the German credit dataset",
        output_artifacts: [{ kind: "other", path: "eda_german_credit.py" }],
        timeout_s: 30,
      },
    }),
    event(3, "run.transitioned", {
      command: "wait_for_approval",
      policy_decision: { id: "decision_python", subject_kind: "tool_call" },
    }),
  ];
  const pythonReview = buildApprovalReview(pythonEvents, foldApprovals(pythonEvents).focus);
  assert.equal(pythonReview.scope.action, "Run the recorded Python code in this Run's workspace.");
  assert.deepEqual(pythonReview.scope.outputs, ["eda_german_credit.py"]);
  assert.match(pythonReview.scope.targets.join(" "), /Perform exploratory data analysis/);
  assert.match(pythonReview.scope.targets.join(" "), /30 seconds/);
  assert.match(pythonReview.scope.effects.join(" "), /workspace write access/);
});

test("findings and plan context are bound to the exact decision evidence", () => {
  const finding = {
    id: "finding_1",
    title: "Population coverage is unclear",
    control_id: "MIRA-RISK-001",
    severity: "HIGH",
    finding: "The affected population is not stated.",
    confidence: 0.9,
    evidence: [{ kind: "event", ref: "seq:8", note: "Interview was unanswered." }],
  };
  const findingEvents = [
    event(1, "audit.finding", { finding }),
    event(2, "policy.decision", { id: "decision_findings", subject_kind: "findings", decision: "REQUIRE_HUMAN_REVIEW", finding_ids: ["finding_1"], reason: "A high finding needs a human decision." }),
    event(3, "human.approval_requested", { decision_id: "decision_findings", summary: "audit findings", reason: "A high finding needs a human decision." }),
    event(4, "run.transitioned", { command: "wait_for_approval", policy_decision: { id: "decision_findings" } }),
  ];
  const findingReview = buildApprovalReview(findingEvents, foldApprovals(findingEvents).focus);
  assert.equal(findingReview.kind, "findings");
  assert.deepEqual(findingReview.findings, [finding]);
  assert.equal(findingReview.findings[0].evidence[0].ref, "seq:8");

  const planEvents = [
    event(1, "policy.decision", { id: "decision_plan", subject_kind: "run", decision: "REQUIRE_HUMAN_REVIEW", reason: "Review the proposed plan." }),
    event(2, "human.approval_requested", { decision_id: "decision_plan", summary: "plan.proposed", plan: ["inspect", "profile"] }),
    event(3, "run.transitioned", { command: "wait_for_approval", policy_decision: { id: "decision_plan" } }),
  ];
  const planReview = buildApprovalReview(planEvents, foldApprovals(planEvents).focus);
  assert.equal(planReview.kind, "plan");
  assert.deepEqual(planReview.plan, ["inspect", "profile"]);
});

test("session review submits the displayed decision ID, keeps note and busy state across polls", async () => {
  let finishAnswer;
  let submitted = null;
  const events = startEvents();
  installEvents(events, (url, init) => {
    if (url.pathname.endsWith("/approve")) {
      submitted = { body: JSON.parse(init.body), path: url.pathname };
      return new Promise((resolve) => { finishAnswer = resolve; });
    }
    return null;
  });
  const root = new FakeNode("main");
  const resolved = [];
  let clarification;
  const view = new SessionReviewView(root, {
    onResolved: (value) => resolved.push(value),
    onClarifyRisk: (value) => { clarification = value; },
  });
  try {
    view.update(waitingTurn());
    await flush();
    assert.match(root.textContent, /The run may start/);
    assert.match(root.textContent, /Each tool call still passes its own policy checks/);
    assert.match(root.textContent, /Model Development/);
    assert.match(root.textContent, /80%/);
    assert.match(root.textContent, /Affected population/);
    assert.match(root.textContent, /Decision role/);
    assert.match(root.textContent, /Deployment context/);
    assert.match(root.textContent, /Human oversight/);
    assert.match(root.textContent, /Intended use/);
    assert.match(root.textContent, /Potential consequences/);
    assert.match(root.textContent, /Local execution only/);
    assert.match(root.textContent, /The risk assessment requires human review/);
    assert.match(root.textContent, /The run will not start/);

    buttonWithText(root, "Review risk context").listeners.get("click")();
    assert.equal(clarification.kind, "execution_start");
    assert.equal(clarification.decisionId, "decision_start");
    assert.deepEqual(clarification.missingInformation, [
      "affected_population", "decision_role", "deployment_context", "human_oversight", "intended_use", "potential_consequences",
    ]);
    assert.equal(submitted, null, "clarifying the context must not submit an approval");

    const note = root.querySelectorAll("textarea")[0];
    note.value = "Reviewed the disclosed scope";
    buttonWithText(root, "Approve run start").listeners.get("click")();
    await flush();
    assert.equal(submitted.body.expected_decision_id, "decision_start");
    assert.equal(submitted.body.note, "Reviewed the disclosed scope");
    assert.equal(buttonWithText(root, "Approve run start").disabled, true);

    view.update(waitingTurn());
    await flush();
    assert.equal(root.querySelectorAll("textarea")[0].value, "Reviewed the disclosed scope");
    assert.equal(buttonWithText(root, "Approve run start").disabled, true);

    finishAnswer(response({ id: "run_1", status: "RUNNING" }));
    await flush();
    assert.equal(resolved.length, 1);
    assert.equal(resolved[0].decisionId, "decision_start");
  } finally {
    view.dispose();
  }
});

test("session review disables stale and loading records, then reports event-read errors plainly", async () => {
  const events = startEvents();
  let failEvents = false;
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/events")) {
      if (failEvents) return response({ code: "event_store_unavailable", message: "The event log could not be verified." }, 503);
      return response({ items: events, has_more: false, next_after_seq: null });
    }
    return response({ id: "run_1" });
  });
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  try {
    view.update(waitingTurn("run_1", true));
    assert.match(root.textContent, /stale or still loading/);
    assert.equal(root.querySelectorAll("button").length, 0);

    view.update(waitingTurn());
    await flush();
    assert.equal(buttonWithText(root, "Approve run start").disabled, false);

    failEvents = true;
    view.update(waitingTurn());
    await flush();
    assert.match(root.textContent, /event_store_unavailable/);
    assert.equal(buttonWithText(root, "Approve run start").disabled, true);
  } finally {
    view.dispose();
  }
});

test("a rejected tool review sends the exact decision ID and reports only recorded call effects", async () => {
  let submitted;
  const events = toolEvents();
  installEvents(events, (url, init) => {
    if (url.pathname.endsWith("/reject")) {
      submitted = { body: JSON.parse(init.body), path: url.pathname };
      return response({ id: "run_1", status: "RUNNING" });
    }
    return null;
  });
  const root = new FakeNode("main");
  const resolved = [];
  const view = new SessionReviewView(root, { onResolved: (value) => resolved.push(value) });
  try {
    view.update(waitingTurn());
    await flush();
    assert.match(root.textContent, /No separate side-effect list is attached to this request/);
    assert.match(root.textContent, /Write the requested file into this Run's workspace/);
    assert.match(root.textContent, /reports\/result\.csv/);
    const exactRequest = root.querySelectorAll("details").find((node) => /Exact tool request/.test(node.textContent));
    assert.ok(exactRequest, "the full tool payload should be available in a disclosure");
    assert.equal(exactRequest.getAttribute("open"), null);
    root.querySelectorAll("button").find((button) => button.textContent === "Reject this call").listeners.get("click")();
    await flush();
    assert.equal(submitted.path.endsWith("/reject"), true);
    assert.equal(submitted.body.expected_decision_id, "decision_tool");
    assert.equal(Object.hasOwn(submitted.body, "note"), false);
    assert.deepEqual(resolved.map((item) => [item.decisionId, item.approved]), [["decision_tool", false]]);
  } finally {
    view.dispose();
  }
});

test("answers stay unavailable until verified events finish loading and disposal ignores a late read", async () => {
  let finishRead;
  installBrowser(async (input) => {
    if (new URL(input).pathname.endsWith("/events")) {
      return new Promise((resolve) => { finishRead = resolve; });
    }
    return response({ id: "run_1" });
  });
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  view.update(waitingTurn());
  await flush();
  assert.match(root.textContent, /Reading the verified review record/);
  assert.equal(root.querySelectorAll("button").length, 0);
  view.dispose();
  finishRead(response({ items: startEvents(), has_more: false, next_after_seq: null }));
  await flush();
  assert.equal(root.hidden, true);
  assert.equal(root.textContent, "");
});

test("a failed answer keeps its note and error after the same pending decision refreshes", async () => {
  let answerCalls = 0;
  const events = startEvents();
  installEvents(events, (url) => {
    if (url.pathname.endsWith("/approve")) {
      answerCalls += 1;
      return response({ code: "stale_decision", message: "The displayed review is no longer current." }, 409);
    }
    return null;
  });
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  try {
    view.update(waitingTurn());
    await flush();
    root.querySelectorAll("textarea")[0].value = "Check the deployment scope first";
    buttonWithText(root, "Approve run start").listeners.get("click")();
    await flush();
    assert.equal(answerCalls, 1);
    assert.match(root.textContent, /Answer was not recorded: stale_decision/);
    assert.equal(root.querySelectorAll("textarea")[0].value, "Check the deployment scope first");
  } finally {
    view.dispose();
  }
});

test("a harmless event poll keeps the same review and answer controls usable", async () => {
  let eventReads = 0;
  let finishRefresh;
  const events = startEvents();
  installBrowser(async (input) => {
    if (new URL(input).pathname.endsWith("/events")) {
      eventReads += 1;
      if (eventReads === 1) return response({ items: events, has_more: false, next_after_seq: null });
      return new Promise((resolve) => { finishRefresh = resolve; });
    }
    return response({ id: "run_1" });
  });
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  try {
    view.update(waitingTurn());
    await flush();
    const note = root.querySelectorAll("textarea")[0];
    note.value = "Still typing";
    view.update(waitingTurn());
    await flush();
    assert.equal(root.querySelectorAll("textarea")[0], note);
    assert.equal(note.disabled, false);
    assert.equal(buttonWithText(root, "Approve run start").disabled, false);
    assert.doesNotMatch(root.textContent, /Refreshing the verified review record/);

    finishRefresh(response({ items: events, has_more: false, next_after_seq: null }));
    await flush();
    assert.equal(root.querySelectorAll("textarea")[0], note);
    assert.equal(note.value, "Still typing");
    assert.equal(note.disabled, false);
    assert.equal(buttonWithText(root, "Approve run start").disabled, false);
  } finally {
    view.dispose();
  }
});

test("risk clarification opens a separate path and does not submit an approval", async () => {
  const events = riskUncertaintyEvents();
  let clarification;
  let actionRequests = 0;
  installEvents(events, (url) => {
    if (url.pathname.endsWith("/approve") || url.pathname.endsWith("/reject")) actionRequests += 1;
    return null;
  });
  const root = new FakeNode("main");
  const view = new SessionReviewView(root, { onClarifyRisk: (value) => { clarification = value; } });
  try {
    view.update(waitingTurn());
    await flush();
    assert.match(root.textContent, /could not classify inherent risk/);
    assert.match(root.textContent, /execution-start Gate and any later required policy or tool reviews still apply/);
    const button = buttonWithText(root, "Review risk context");
    assert.ok(button);
    button.listeners.get("click")();
    assert.equal(clarification.runId, "run_1");
    assert.equal(clarification.decisionId, "decision_uncertain");
    assert.equal(clarification.kind, "risk_uncertainty");
    assert.deepEqual(clarification.missingInformation, []);
    assert.equal(actionRequests, 0);
    assert.match(root.textContent, /Decision ID|Policy references/);
    const eventDetails = root.querySelectorAll("details").find((node) => /Policy references/.test(node.textContent));
    assert.ok(eventDetails, "technical references should be in a disclosure");
    assert.equal(eventDetails.getAttribute("open"), null);
  } finally {
    view.dispose();
  }
});

test("a late event read from the previous run cannot replace the selected run review", async () => {
  let finishFirstRead;
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/run_1/events")) {
      return new Promise((resolve) => { finishFirstRead = resolve; });
    }
    if (url.pathname.endsWith("/run_2/events")) {
      const events = startEvents("decision_run_two");
      events[2].payload.summary = "execution.start: second run";
      return response({ items: events, has_more: false, next_after_seq: null });
    }
    return response({ id: "run_2" });
  });
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  try {
    view.update(waitingTurn("run_1"));
    view.update(waitingTurn("run_2"));
    await flush();
    assert.match(root.textContent, /second run/);
    finishFirstRead(response({ items: startEvents("decision_run_one"), has_more: false, next_after_seq: null }));
    await flush();
    assert.doesNotMatch(root.textContent, /decision_run_one/);
    assert.match(root.textContent, /decision_run_two/);
  } finally {
    view.dispose();
  }
});

test("a stale turn switch clears the previous run's decision before rendering", async () => {
  installEvents(startEvents());
  const root = new FakeNode("main");
  const view = new SessionReviewView(root);
  try {
    view.update(waitingTurn("run_1"));
    await flush();
    assert.match(root.textContent, /decision_start/);
    view.update(waitingTurn("run_2", true));
    assert.match(root.textContent, /stale or still loading/);
    assert.doesNotMatch(root.textContent, /decision_start|Local execution only/);
    assert.equal(root.querySelectorAll("button").length, 0);
  } finally {
    view.dispose();
  }
});
