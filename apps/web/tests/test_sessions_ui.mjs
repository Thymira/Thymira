import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { SessionView } from "../src/thymira/web/static/js/views/session.js";
import { RunView } from "../src/thymira/web/static/js/views/run.js";
import { activeTurnLabel, isUncertainSendError, runSectionForTurn, sessionTitle, turnBadge, turnStateLabel } from "../src/thymira/web/static/js/session-model.js";

class FakeNode {
  constructor(tagName = "#text", text = "") {
    this.tagName = tagName.toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.readOnly = false;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      add: (...names) => this.setClasses([...this.className.split(/\s+/).filter(Boolean), ...names]),
      remove: (...names) => this.setClasses(this.className.split(/\s+/).filter((name) => name && !names.includes(name))),
      toggle: (name, force) => {
        const has = this.classList.contains(name);
        const next = force === undefined ? !has : Boolean(force);
        if (next && !has) this.classList.add(name);
        if (!next && has) this.classList.remove(name);
        return next;
      },
    };
    this.text = text;
  }

  setClasses(names) {
    this.className = [...new Set(names.filter(Boolean))].join(" ");
  }

  append(...nodes) {
    for (const node of nodes) {
      if (node instanceof FakeNode) {
        node.parentNode = this;
        this.childNodes.push(node);
      } else {
        this.childNodes.push(new FakeNode("#text", String(node)));
      }
    }
  }

  replaceChildren(...nodes) {
    for (const child of this.childNodes) child.parentNode = null;
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

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  querySelectorAll(selector) {
    const selectors = selector.split(",").map((item) => item.trim());
    const matches = (node) => selectors.some((item) =>
      item.startsWith(".") ? node.classList.contains(item.slice(1)) : node.tagName.toLowerCase() === item,
    );
    const results = [];
    const visit = (node) => {
      for (const child of node.childNodes) {
        if (matches(child)) results.push(child);
        visit(child);
      }
    };
    visit(this);
    return results;
  }

  set textContent(value) {
    this.replaceChildren();
    if (value !== "" && value !== null && value !== undefined) this.append(new FakeNode("#text", String(value)));
  }

  get textContent() {
    return this.text + this.childNodes.map((child) => child.textContent).join("");
  }
}

class FakeTextNode extends FakeNode {
  constructor(text) {
    super("#text", String(text));
  }
}

const originals = {};

function installBrowser(fetchImpl, { datasets = () => response({ items: [] }), runtimeStatus = () => response({ status: "ready", summary: "Python prerequisites ready" }), storage: suppliedStorage } = {}) {
  for (const key of ["Node", "document", "window", "sessionStorage", "CustomEvent", "fetch", "setInterval", "XMLHttpRequest"]) {
    originals[key] = globalThis[key];
  }
  globalThis.Node = FakeNode;
  globalThis.document = {
    hidden: false,
    activeElement: null,
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => new FakeTextNode(text),
  };
  const storage = suppliedStorage ?? new Map();
  globalThis.sessionStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  globalThis.CustomEvent = class CustomEvent { constructor(type) { this.type = type; } };
  globalThis.window = {
    location: { origin: "http://localhost:8080", hash: "#/" },
    confirm: () => true,
    dispatchEvent() {},
    addEventListener() {},
    history: { replaceState() {} },
  };
  globalThis.fetch = (input, options) => {
    const path = new URL(input, "http://localhost:8080").pathname;
    if (path.endsWith("/project/datasets")) return datasets(input, options);
    if (path.endsWith("/tools/runtime-status")) return runtimeStatus(input, options);
    return fetchImpl(input, options);
  };
  return { storage };
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

function sessionDetail(id, { title = "", turns = [], discussions = [], activeRunId = null } = {}) {
  return { session: { id, title, conversation: true, created_at: "2026-09-26T10:00:00Z" }, turns, discussions, active_run_id: activeRunId };
}

function discussion(id, {
  ordinal = 1,
  message = "What does this evidence mean?",
  answer = "The recorded result shows a moderate association.",
  kind = "answer",
  status = "completed",
  mode = "ask",
  createdAt = "2026-09-26T10:01:00Z",
  runId = "run_evidence",
  citations = [{ run_id: "run_evidence", seq: 4, label: "EDA output" }],
  error = null,
} = {}) {
  return {
    id, ordinal, user_message: message, assistant_message: answer, kind, status, mode,
    created_at: createdAt, run_id: runId, citations, error,
  };
}

async function flush() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}

function turn(id, { ordinal = 1, status = "RUNNING", assistant = null, message = "Profile the dataset", state = status === "COMPLETED" ? { condition: "terminal", outcome: "completed", stage: "reporting" } : { condition: "active", stage: "executing" } } = {}) {
  return {
    id,
    ordinal,
    user_message: message,
    assistant_message: assistant,
    run: { id, session_id: "ses_123", status, prompt: message, created_at: "2026-09-26T10:00:00Z" },
    state,
  };
}

test("blocked turn explains audit and tool cause after reopening without releasing provisional text", async () => {
  const saved = turn("run_blocked", { assistant: "Unapproved canary", state: { condition: "terminal", outcome: "blocked", stage: "auditing" } });
  saved.terminal_diagnostic = { summary: "A critical audit finding was raised.", policy_rule: "GOV-101", details: ["A19 · Confinement enforced", "run_python: docker was not found"], next_steps: ["Verify the execution environment before new work."] };
  installBrowser(async (input) => {
    if (new URL(input).pathname.endsWith("/sessions/ses_123")) return response(sessionDetail("ses_123", { turns: [saved] }));
    return response({ entries: [] });
  });
  for (let reopen = 0; reopen < 2; reopen++) {
    const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
    try {
      await flush();
      assert.match(view.transcript.textContent, /Why this run was blocked/);
      assert.match(view.transcript.textContent, /A19/);
      assert.match(view.transcript.textContent, /docker was not found/);
      assert.match(view.transcript.textContent, /Verify the execution environment/);
      assert.doesNotMatch(view.transcript.textContent, /Unapproved canary/);
      assert.ok(view.transcript.querySelectorAll("a").some((node) => node.getAttribute("href") === "#/runs/run_blocked/audit"));
      const discuss = view.transcript.querySelectorAll("button").find((node) => node.textContent === "Discuss how to resolve this");
      assert.ok(discuss);
      await discuss.listeners.get("click")();
      assert.equal(view.modeField.value, "ask");
      assert.match(view.messageField.value, /run_blocked/);
      view.messageField.value = "My unsent draft";
      await discuss.listeners.get("click")();
      assert.equal(view.messageField.value, "My unsent draft");
    } finally { view.dispose(); }
  }
});

test("runtime warning remains stable across polling and does not disable follow-up drafting", async () => {
  let probes = 0;
  installBrowser(async (input) => new URL(input).pathname.endsWith("/sessions/ses_123")
    ? response(sessionDetail("ses_123")) : response({ entries: [] }), {
    runtimeStatus: () => { probes++; return response({ status: "unavailable", summary: "docker was not found", next_step: "Install Docker" }); },
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    view.messageField.value = "Explain the analysis";
    view.onDraftChange();
    const warning = view.runtimeRoot.childNodes[0];
    await view.load({ silent: true });
    assert.equal(view.runtimeRoot.childNodes[0], warning);
    assert.equal(probes, 1);
    assert.equal(view.messageField.value, "Explain the analysis");
    assert.equal(view.sendButton.disabled, false);
    assert.match(view.runtimeRoot.textContent, /docker was not found/);
    await view.runtimeStatus.load();
    assert.equal(probes, 2);
  } finally { view.dispose(); }
});

test("a project-path Session citation opens the cited file in the existing explorer", async () => {
  const projectReads = [];
  const cited = discussion("discussion_project_source", {
    runId: null,
    citations: [{ run_id: null, path: "project/src/main.py", label: "src/main.py" }],
  });
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) {
      return response(sessionDetail("ses_123", { discussions: [cited] }));
    }
    if (url.pathname.endsWith("/project/files")) {
      return response({ path: url.searchParams.get("path") ?? "", entries: [], truncated: false });
    }
    if (url.pathname.endsWith("/project/file")) {
      const path = url.searchParams.get("path");
      projectReads.push(path);
      return response({ path, content: "print('cited')", truncated: false, editable: false, sha256: null, read_only_reason: null });
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const inspector = new FakeNode("aside");
  const view = new SessionView(new FakeNode("main"), inspector, "ses_123");
  try {
    await flush();
    const citation = view.transcript.querySelectorAll("a").find((node) => node.getAttribute("href") === "#/sessions/ses_123/files/project%2Fsrc%2Fmain.py");
    assert.ok(citation);
    globalThis.window.location.hash = citation.getAttribute("href");
    view.updateRoute("files");
    await flush();
    assert.deepEqual(projectReads, ["src/main.py"]);
    assert.match(inspector.textContent, /print\('cited'\)/);
  } finally { view.dispose(); }
});

test("a project-root Session citation opens the explorer root", async () => {
  const listedPaths = [];
  const cited = discussion("discussion_project_root", {
    runId: null,
    citations: [{ run_id: null, path: "project", label: "Project" }],
  });
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) {
      return response(sessionDetail("ses_123", { discussions: [cited] }));
    }
    if (url.pathname.endsWith("/project/files")) {
      const path = url.searchParams.get("path") ?? "";
      listedPaths.push(path);
      return response({
        path,
        entries: [{ name: "src", path: "src", directory: true }],
        truncated: false,
      });
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    const citation = view.transcript.querySelectorAll("a").find(
      (node) => node.getAttribute("href") === "#/sessions/ses_123/files/project?directory=1",
    );
    assert.ok(citation);
    globalThis.window.location.hash = citation.getAttribute("href");
    view.updateRoute("files");
    await flush();
    assert.equal(listedPaths.at(-1), "");
    assert.match(view.inspectorRoot.textContent, /src/);
  } finally { view.dispose(); }
});

for (const state of [
  { condition: "waiting", wait_reason: "approval", stage: "auditing" },
  { condition: "active", stage: "auditing" },
  { condition: "terminal", outcome: "blocked", stage: "auditing" },
  { condition: "terminal", outcome: "failed", stage: "executing" },
  { condition: "terminal", outcome: "cancelled", stage: "executing" },
]) {
  test(`provisional text is not rendered for ${state.condition}/${state.outcome ?? state.stage}`, async () => {
    const savedTurn = turn("run_provisional", { assistant: "Unapproved canary", state });
    installBrowser(async (input) => {
      const url = new URL(input);
      if (url.pathname.endsWith("/sessions/ses_123")) return response(sessionDetail("ses_123", {
        turns: [savedTurn], activeRunId: state.condition === "terminal" ? null : savedTurn.id,
      }));
      if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
      throw new Error(`Unexpected request ${url.pathname}`);
    });
    const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
    try {
      await flush();
      assert.doesNotMatch(view.transcript.textContent, /Unapproved canary/);
      assert.equal(view.transcript.querySelectorAll(".conversation-assistant").length, 0);
      assert.match(view.transcript.textContent, /Open run/);
      if (state.condition === "terminal") assert.match(view.transcript.textContent, /No approved response/);
    } finally {
      view.dispose();
    }
  });
}

test("session labels use explicit title first, then the first message, without inventing assistant text", () => {
  assert.equal(sessionTitle({ title: "  Credit risk  " }, [{ user_message: "Other" }]), "Credit risk");
  assert.equal(sessionTitle({ title: "" }, [{ user_message: "  Explore\nGerman credit  " }]), "Explore German credit");
  assert.equal(sessionTitle({ title: "" }, []), "New session");
  const legacyReviewTurn = { ...turn("run_legacy_review", { status: "WAITING_FOR_APPROVAL" }), state: null };
  assert.equal(activeTurnLabel(sessionDetail("ses_123", { turns: [legacyReviewTurn], activeRunId: "run_legacy_review" })), "Waiting for your review");
  const activeStateTurn = turn("run_active", { status: "WAITING_FOR_APPROVAL", state: { condition: "active", stage: "executing" } });
  assert.equal(activeTurnLabel(sessionDetail("ses_123", { turns: [activeStateTurn], activeRunId: "run_active" })), "THY is working on this run");
  assert.equal(turnBadge(activeStateTurn), "EXECUTING");
  assert.equal(runSectionForTurn(activeStateTurn), "overview");
  const interviewTurn = turn("run_interview", { status: "WAITING_FOR_APPROVAL", state: { condition: "waiting", wait_reason: "information", stage: "planning" } });
  const approvalTurn = turn("run_review", { status: "WAITING_FOR_APPROVAL", state: { condition: "waiting", wait_reason: "approval", stage: "executing" } });
  const cancelledTurn = turn("run_cancelled", { status: "BLOCKED", state: { condition: "terminal", outcome: "cancelled", stage: "executing" } });
  assert.equal(runSectionForTurn(interviewTurn), "interview");
  assert.equal(runSectionForTurn(approvalTurn), "approvals");
  assert.equal(turnStateLabel(interviewTurn), "Waiting for your answer");
  assert.equal(turnStateLabel(cancelledTurn), "Run cancelled");
  assert.equal(turnBadge(cancelledTurn), "CANCELLED");
  assert.equal(isUncertainSendError({ status: 0 }), true);
  assert.equal(isUncertainSendError({ status: 422 }), false);
});

test("the session dataset form uploads the selected CSV and unblocks sending after validation", async () => {
  let items = [{ name: "german_credit", path: "data/applications.csv", present: false }];
  let xhr;
  installBrowser(async (input) => {
    const path = new URL(input).pathname;
    if (path.endsWith("/sessions/ses_123")) return response(sessionDetail("ses_123"));
    if (path.endsWith("/project/files")) return response({ path: "", entries: [] });
    throw new Error(`Unexpected ${path}`);
  }, { datasets: () => response({ items }) });
  globalThis.XMLHttpRequest = class {
    constructor() { xhr = this; this.listeners = new Map(); this.upload = { addEventListener() {} }; }
    open(method, url) { this.method = method; this.url = new URL(url); }
    setRequestHeader() {}
    addEventListener(name, callback) { this.listeners.set(name, callback); }
    send(body) { this.body = body; }
  };
  const inspector = new FakeNode("aside");
  const view = new SessionView(new FakeNode("main"), inspector, "ses_123");
  try {
    await flush();
    view.updateRoute("datasets");
    await flush();
    const form = inspector.querySelectorAll(".dataset-add")[0];
    const inputs = form.querySelectorAll("input");
    const fileControl = inputs.find((node) => node.getAttribute("type") === "file");
    const name = inputs.find((node) => node.getAttribute("aria-label") === "Dataset name");
    const file = new File(["age\n30\n"], "applications.csv", { type: "text/csv" });
    fileControl.files = [file];
    name.value = "german_credit";
    const completion = form.listeners.get("submit")({ preventDefault() {} });
    assert.equal(view.sendButton.disabled, true);
    assert.equal(xhr.method, "PUT");
    assert.match(xhr.url.pathname, /\/project\/datasets\/german_credit$/);
    assert.equal(xhr.url.searchParams.get("filename"), "applications.csv");
    assert.equal(xhr.body, file);
    items = [{ ...items[0], present: true, size_bytes: file.size }];
    xhr.status = 200;
    xhr.responseText = JSON.stringify({ dataset: items[0], rows: 1, columns: ["age"] });
    xhr.listeners.get("load")();
    await completion;
    await flush();
    assert.equal(view.sendButton.disabled, false);
    assert.match(inspector.textContent, /german_credit declared: 1 rows, 1 columns/);
    assert.match(inspector.textContent, /FILE PRESENT/);
  } finally { view.dispose(); }
});

test("session review answers in place and becomes unavailable on stale session state", async () => {
  let stale = false;
  let answered = false;
  let answer;
  const waiting = turn("run_review", { state: { condition: "waiting", wait_reason: "approval", stage: "executing" } });
  const events = [
    { seq: 0, type: "policy.decision", payload: { id: "decision_start", subject_kind: "run", decision: "REQUIRE_HUMAN_REVIEW", reason: "Missing deployment context" } },
    { seq: 1, type: "human.approval_requested", payload: { decision_id: "decision_start", summary: "Execution start review" } },
    { seq: 2, type: "run.transitioned", payload: { command: "wait_for_approval", resume_from: "execution_start", policy_decision: { id: "decision_start" } } },
  ];
  installBrowser(async (input, options) => {
    const path = new URL(input).pathname;
    if (path.endsWith("/sessions/ses_123")) {
      if (stale) throw new Error("offline");
      return response(sessionDetail("ses_123", { turns: [answered ? turn(waiting.id, { status: "COMPLETED", assistant: "Profile ready" }) : waiting], activeRunId: answered ? null : waiting.id }));
    }
    if (path.endsWith("/project/files")) return response({ path: "", entries: [] });
    if (path.endsWith("/events")) return response({ items: events, has_more: false });
    if (path.endsWith("/approve")) { answer = JSON.parse(options.body); answered = true; return response({ id: waiting.id }); }
    throw new Error(`Unexpected ${path}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    assert.equal(view.reviewRoot.hidden, false);
    assert.match(view.reviewRoot.textContent, /Missing deployment context/);
    view.reviewView.form.note.value = "Reviewed for local analysis";
    stale = true;
    await view.load({ silent: true });
    assert.equal(view.reviewView.form.approve.disabled, true);
    stale = false;
    await view.load({ silent: true });
    await flush();
    assert.equal(view.reviewView.form.note.value, "Reviewed for local analysis");
    await view.reviewView.resolve(true);
    await flush();
    assert.deepEqual(answer, { note: "Reviewed for local analysis", expected_decision_id: "decision_start" });
    assert.equal(view.reviewRoot.hidden, true);
    assert.match(view.transcript.textContent, /Profile ready/);
    assert.equal(view.sendButton.disabled, false);
  } finally { view.dispose(); }
});

test("session datasets expose upload controls and refuse a missing declared file before sending", async () => {
  let items = [{ name: "german_credit", path: "data/applications.csv", present: false }];
  let posts = 0;
  installBrowser(async (input, options) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) return response(sessionDetail("ses_123"));
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [] });
    if (url.pathname.endsWith("/chat")) { posts += 1; return response(discussion("discussion_new", { kind: "action", mode: "auto", status: "pending", answer: null, citations: [] }), 202); }
    throw new Error(`Unexpected ${options?.method} ${url.pathname}`);
  }, { datasets: () => response({ items }) });
  const inspector = new FakeNode("aside");
  const view = new SessionView(new FakeNode("main"), inspector, "ses_123");
  try {
    await flush();
    view.messageField.value = "Do an EDA";
    assert.equal(view.sendButton.disabled, true);
    assert.match(view.composer.textContent, /german_credit/);
    await view.send();
    assert.equal(posts, 0);
    view.updateRoute("datasets");
    await flush();
    assert.match(inspector.textContent, /Upload and declare/);
    assert.match(inspector.textContent, /data\/applications.csv/);
    assert.match(inspector.textContent, /shared/i);
    items = [{ ...items[0], present: true }];
    await view.datasetStatus.refresh();
    assert.equal(view.sendButton.disabled, false);
    await view.send();
    assert.equal(posts, 1);
  } finally { view.dispose(); }
});

test("switching sessions ignores a late reply and refresh replaces the selected session details", async () => {
  let finishOld;
  let newRead = 0;
  let current = sessionDetail("ses_new", { title: "Initial title" });
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_old")) {
      return new Promise((resolve) => { finishOld = resolve; });
    }
    if (url.pathname.endsWith("/sessions/ses_new")) {
      newRead += 1;
      return response(current);
    }
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const oldView = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_old");
  oldView.dispose();
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_new");
  await flush();
  assert.equal(view.title.textContent, "Initial title");
  current = sessionDetail("ses_new", { title: "Updated title", turns: [turn("run_2", { status: "COMPLETED", assistant: "The report is ready." })] });
  await view.load({ silent: true });
  assert.equal(view.title.textContent, "Updated title");
  assert.match(view.transcript.textContent, /The report is ready/);
  assert.equal(newRead, 2);
  finishOld(response(sessionDetail("ses_old", { title: "Stale old session" })));
  await flush();
  assert.equal(oldView.title.textContent, "Loading session…");
  assert.equal(view.title.textContent, "Updated title");
  view.dispose();
});

test("an initial or failed refresh keeps sending disabled and preserves the last loaded transcript", async () => {
  let failRead = true;
  let current = sessionDetail("ses_123", { title: "Saved history", turns: [turn("run_1", { status: "COMPLETED", assistant: "Saved response." })] });
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) {
      if (failRead) return response({ code: "api_offline", message: "API offline" }, 503);
      return response(current);
    }
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  assert.equal(view.sendButton.disabled, true);
  await flush();
  assert.equal(view.detailLoaded, false);
  assert.equal(view.title.textContent, "Session unavailable");
  assert.doesNotMatch(view.runStatus.textContent, /last saved/);
  assert.equal(view.messageField.disabled, true);
  view.messageField.value = "Do not send before load";
  await view.send();
  assert.equal(view.pending, null);

  failRead = false;
  await view.load();
  assert.equal(view.sendButton.disabled, false);
  assert.match(view.transcript.textContent, /Saved response/);
  failRead = true;
  await view.load({ silent: true });
  assert.match(view.transcript.textContent, /Saved response/);
  assert.equal(view.sendButton.disabled, true);
  assert.match(view.runStatus.textContent, /Could not refresh/);
  view.dispose();
});

test("a late refresh for the same session cannot replace a newer transcript", async () => {
  let finishOld;
  let reads = 0;
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) {
      reads += 1;
      if (reads === 1) return new Promise((resolve) => { finishOld = resolve; });
      return response(sessionDetail("ses_123", { turns: [turn("run_latest", {
        status: "COMPLETED", assistant: "Newest recorded response.",
        state: { condition: "terminal", outcome: "completed" },
      })] }));
    }
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  await view.load({ silent: true });
  finishOld(response(sessionDetail("ses_123")));
  await flush();
  assert.match(view.transcript.textContent, /Newest recorded response/);
  assert.equal(view.detail.turns.length, 1);
  assert.equal(view.sendButton.disabled, false);
  view.dispose();
});

test("an active run makes the conversation busy and creates no assistant placeholder", async () => {
  const detail = sessionDetail("ses_123", { turns: [turn("run_1")], activeRunId: "run_1" });
  let posts = 0;
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) return response(detail);
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    if (init.method === "POST") posts += 1;
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  await flush();
  view.modeField.value = "action";
  view.renderComposerState();
  assert.equal(view.messageField.disabled, true);
  assert.equal(view.sendButton.disabled, true);
  assert.equal(view.transcript.querySelectorAll(".conversation-assistant").length, 0);
  view.messageField.value = "A second message";
  await view.send();
  assert.equal(posts, 0);
  view.dispose();
});

test("session chat distinguishes explanation from work and allows an ask during an active run", async () => {
  const active = turn("run_active");
  let detail = sessionDetail("ses_123", { turns: [active], activeRunId: active.id });
  const requests = [];
  const explanation = discussion("discussion_answer", {
    message: "What does the EDA warning mean?",
    answer: "The warning describes missing values in the recorded profile.",
  });
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123") && init.method !== "POST") return response(detail);
    if (url.pathname.endsWith("/chat") && init.method === "POST") {
      const body = JSON.parse(init.body);
      requests.push({ path: url.pathname, body });
      detail = sessionDetail("ses_123", { turns: [active], discussions: [explanation], activeRunId: active.id });
      return response(explanation, 202);
    }
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    assert.equal(view.modeField.value, "auto");
    assert.match(view.modeHelp.textContent, /Auto/);
    view.modeField.value = "ask";
    view.renderComposerState();
    assert.equal(view.sendButton.disabled, false, "asking about the session remains available during a Run");
    view.messageField.value = "What does the EDA warning mean?";
    await view.send();

    assert.equal(requests.length, 1);
    assert.equal(requests[0].path.endsWith("/chat"), true);
    assert.deepEqual(requests[0].body, {
      message: "What does the EDA warning mean?",
      client_message_id: requests[0].body.client_message_id,
      mode: "ask",
    });
    assert.match(view.transcript.textContent, /Explanation of recorded evidence/);
    assert.match(view.transcript.textContent, /does not create a new result, approval, or audit/);
    assert.match(view.transcript.textContent, /missing values in the recorded profile/);
    assert.ok(view.transcript.querySelectorAll("a").some((node) => /\/runs\/run_evidence\/events/.test(node.getAttribute("href"))));

    view.modeField.value = "action";
    view.renderComposerState();
    assert.equal(view.sendButton.disabled, true, "request-work mode is disabled while a Run is active");
    view.modeField.value = "auto";
    view.renderComposerState();
    assert.equal(view.sendButton.disabled, false, "auto remains available for a session explanation");
  } finally { view.dispose(); }
});

test("the session timeline merges explanations by time and hides their duplicate action Runs", async () => {
  const actionTurn = turn("run_action", { message: "Profile the dataset" });
  actionTurn.run.created_at = "2026-09-26T10:02:00Z";
  const before = discussion("discussion_before", {
    message: "What did the first chart show?", createdAt: "2026-09-26T10:01:00Z",
  });
  const duplicateAction = discussion("discussion_action", {
    message: "Profile the dataset", kind: "action", mode: "action", status: "completed",
    createdAt: "2026-09-26T10:02:00Z", runId: actionTurn.id, answer: null, citations: [],
  });
  const after = discussion("discussion_after", {
    message: "Why are some records missing?", createdAt: "2026-09-26T10:03:00Z",
  });
  const detail = sessionDetail("ses_123", {
    turns: [actionTurn], discussions: [after, duplicateAction, before],
  });
  installBrowser(async (input) => {
    const path = new URL(input).pathname;
    if (path.endsWith("/sessions/ses_123")) return response(detail);
    if (path.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    throw new Error(`Unexpected request ${path}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    const cards = view.transcript.querySelectorAll(".conversation-discussion");
    assert.equal(cards.length, 2);
    assert.doesNotMatch(cards.map((card) => card.textContent).join(" "), /duplicateAction/);
    const text = view.transcript.textContent;
    assert.ok(text.indexOf("What did the first chart show?") < text.indexOf("Profile the dataset"));
    assert.ok(text.indexOf("Profile the dataset") < text.indexOf("Why are some records missing?"));
  } finally { view.dispose(); }
});

test("failed discussions can be resent with a new identity and pending discussions remain explicit", async () => {
  const pending = discussion("discussion_pending", {
    message: "Explain the model warning", answer: null, status: "pending", runId: null,
    citations: [], mode: "ask",
  });
  const failed = discussion("discussion_failed", {
    message: "Summarize the completed analysis", answer: null, status: "failed", error: "provider_unavailable",
    runId: null, citations: [], mode: "ask",
  });
  const requests = [];
  let detail = sessionDetail("ses_123", { discussions: [pending, failed] });
  const retries = [];
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123") && init.method !== "POST") return response(detail);
    if (url.pathname.endsWith("/chat/discussion_pending/retry") && init.method === "POST") {
      retries.push({ path: url.pathname, body: init.body });
      const retryResult = { ...pending, status: "failed", error: "worker_interrupted" };
      detail = sessionDetail("ses_123", { discussions: [retryResult, failed] });
      return response(retryResult);
    }
    if (url.pathname.endsWith("/chat") && init.method === "POST") {
      requests.push(JSON.parse(init.body));
      return response(discussion("discussion_retry", { message: requests.at(-1).message }), 202);
    }
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    assert.match(view.transcript.textContent, /This request is still pending/);
    assert.match(view.transcript.textContent, /provider_unavailable/);
    const pendingRetry = view.transcript.querySelectorAll("button").find((button) => button.textContent === "Retry pending request");
    assert.ok(pendingRetry);
    pendingRetry.listeners.get("click")();
    await flush();
    assert.deepEqual(retries, [{ path: "/api/sessions/ses_123/chat/discussion_pending/retry", body: undefined }]);
    assert.match(view.transcript.textContent, /worker_interrupted/);
    const retry = view.transcript.querySelectorAll("button").find((button) => button.textContent === "Resend as new message");
    assert.ok(retry);
    retry.listeners.get("click")();
    assert.equal(view.messageField.value, pending.user_message);
    assert.equal(view.modeField.value, "ask");
    await view.send();
    assert.equal(requests.length, 1);
    assert.equal(typeof requests[0].client_message_id, "string");
    assert.ok(requests[0].client_message_id.length > 0);
    assert.equal(requests[0].message, pending.user_message);
    assert.equal(requests[0].mode, "ask");
  } finally { view.dispose(); }
});

test("a chat transport retry after reload preserves the exact message, mode, and request ID", async () => {
  let detail = sessionDetail("ses_123");
  let posts = 0;
  const bodies = [];
  const { storage } = installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123") && init.method !== "POST") return response(detail);
    if (url.pathname.endsWith("/chat") && init.method === "POST") {
      posts += 1;
      bodies.push(JSON.parse(init.body));
      if (posts === 1) throw new TypeError("socket closed after request");
      const accepted = discussion("discussion_accepted", { message: bodies.at(-1).message });
      detail = sessionDetail("ses_123", { discussions: [accepted] });
      return response(accepted, 202);
    }
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const firstView = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  await flush();
  firstView.modeField.value = "ask";
  firstView.messageField.value = "Explain the latest chart";
  await firstView.send();
  const retryId = firstView.pending.clientMessageId;
  assert.equal(firstView.pending.mode, "ask");
  firstView.dispose();

  const reloaded = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    assert.equal(reloaded.modeField.value, "ask");
    assert.equal(reloaded.messageField.readOnly, true);
    assert.equal(reloaded.retryButton.hidden, false);
    await reloaded.send();
    assert.equal(bodies.length, 2);
    assert.equal(bodies[0].client_message_id, retryId);
    assert.equal(bodies[1].client_message_id, retryId);
    assert.equal(bodies[0].mode, "ask");
    assert.equal(bodies[1].mode, "ask");
    assert.equal(bodies[1].message, "Explain the latest chart");
    assert.equal(reloaded.pending, null);
  } finally { reloaded.dispose(); }
});

test("an uncertain send retries the same message id and only renders the user turn until a reply exists", async () => {
  const empty = sessionDetail("ses_123");
  const accepted = turn("run_accepted", { status: "RUNNING", message: "Profile German credit" });
  const calls = [];
  let detail = empty;
  let postCount = 0;
  const { storage } = installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) return response(detail);
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    if (url.pathname.endsWith("/chat") && init.method === "POST") {
      calls.push(JSON.parse(init.body));
      postCount += 1;
      if (postCount === 1) throw new TypeError("socket closed after request");
      detail = sessionDetail("ses_123", { turns: [accepted], activeRunId: accepted.id });
      return response(discussion("discussion_action", { kind: "action", mode: "auto", status: "pending", answer: null, citations: [], runId: accepted.id }), 202);
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  await flush();
  view.messageField.value = "Profile German credit";
  await view.send();
  assert.equal(view.pending.uncertain, true);
  assert.equal(view.messageField.readOnly, true);
  assert.equal(view.retryButton.hidden, false);
  assert.match(view.sendButton.textContent, /Retry/);
  const stored = JSON.parse(storage.get(`thymira.console.pending-message.ses_123`));
  await view.send();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].client_message_id, calls[1].client_message_id);
  assert.equal(calls[0].message, calls[1].message);
  assert.equal(stored.clientMessageId, calls[0].client_message_id);
  assert.equal(view.pending, null);
  assert.equal(view.messageField.value, "");
  assert.equal(view.transcript.querySelectorAll(".conversation-assistant").length, 0);
  assert.match(view.transcript.textContent, /THY is working/i);
  view.dispose();
});

test("a definite message failure keeps the draft editable and changed text gets a new retry identity", async () => {
  const ids = [];
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) return response(sessionDetail("ses_123"));
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    if (url.pathname.endsWith("/chat")) {
      ids.push(JSON.parse(init.body).client_message_id);
      return response({ code: "message_invalid", message: "Rejected by the API" }, 422);
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  await flush();
  view.messageField.value = "Profile a dataset";
  await view.send();
  assert.equal(view.messageField.value, "Profile a dataset");
  assert.equal(view.messageField.readOnly, false);
  assert.match(view.composerStatus.textContent, /Rejected by the API/);
  const firstId = view.pending.clientMessageId;
  view.messageField.value = "Profile a different dataset";
  view.onDraftChange();
  assert.equal(view.pending, null);
  await view.send();
  assert.equal(ids.length, 2);
  assert.notEqual(ids[0], ids[1]);
  view.dispose();
});

test("a session-busy response remains visible after the authoritative refresh", async () => {
  let detail = sessionDetail("ses_123");
  const ids = [];
  const { storage } = installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_123")) return response(detail);
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    if (url.pathname.endsWith("/chat") && init.method === "POST") {
      const body = JSON.parse(init.body);
      ids.push(body.client_message_id);
      const queued = discussion("discussion_busy", { kind: "action", status: "pending", mode: body.mode, message: body.message, answer: null, runId: null, citations: [] });
      detail = sessionDetail("ses_123", { turns: [turn("run_busy")], discussions: [queued], activeRunId: "run_busy" });
      return response({ code: "session_busy", message: "This session already has a running turn." }, 409);
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  await flush();
  view.messageField.value = "Queue another analysis";
  await view.send();
  assert.match(view.composerStatus.textContent, /already has a running turn/);
  assert.equal(view.sendButton.disabled, false, "Auto remains available for a session question while the Run is active");
  assert.ok(view.pending.clientMessageId);
  assert.ok(storage.get("thymira.console.pending-message.ses_123"));
  await view.send();
  assert.equal(ids.length, 2);
  assert.equal(ids[0], ids[1], "retrying an unchanged 409 draft keeps its request identity");
  view.dispose();
});

test("a send reply arriving after a session switch clears only its own retry record", async () => {
  let finishPost;
  const { storage } = installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/sessions/ses_old")) return response(sessionDetail("ses_old"));
    if (url.pathname.endsWith("/sessions/ses_new")) return response(sessionDetail("ses_new", { title: "New session" }));
    if (url.pathname.endsWith("/project/files")) return response({ path: "", entries: [], truncated: false });
    if (url.pathname.endsWith("/chat") && init.method === "POST") {
      return new Promise((resolve) => { finishPost = resolve; });
    }
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const oldView = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_old");
  await flush();
  oldView.messageField.value = "Keep this request stable";
  const sending = oldView.send();
  await flush();
  const pending = JSON.parse(storage.get("thymira.console.pending-message.ses_old"));
  oldView.dispose();
  const newView = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_new");
  await flush();
  finishPost(response(discussion("discussion_old", { message: "Keep this request stable", status: "pending", answer: null, citations: [] }), 202));
  await sending;
  assert.equal(storage.has("thymira.console.pending-message.ses_old"), false);
  assert.equal(oldView.messageField.value, "Keep this request stable");
  assert.equal(newView.title.textContent, "New session");
  assert.equal(pending.message, "Keep this request stable");
  newView.dispose();
});

test("the standalone Run page keeps all existing tabs and the cancel control", async () => {
  const run = {
    id: "run_123",
    session_id: "ses_123",
    prompt: "Profile German credit",
    status: "COMPLETED",
    created_at: "2026-09-26T10:00:00Z",
    agent_ids: [],
    task_ids: [],
    tool_call_ids: [],
    artifact_ids: [],
    finding_ids: [],
    final_decision: null,
  };
  const calls = [];
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    calls.push({ path: url.pathname, method: init.method ?? "GET", body: init.body });
    if (url.pathname.endsWith("/events")) return response({ items: [], has_more: false, next_after_seq: null });
    if (url.pathname.endsWith("/cancel")) return response(run);
    if (url.pathname.endsWith("/runs/run_123")) return response(run);
    throw new Error(`Unexpected request ${url.pathname}`);
  });
  const view = new RunView(new FakeNode("main"), "run_123", "overview");
  await flush();
  const tabs = view.tabBar.querySelectorAll(".tab");
  assert.deepEqual(tabs.map((tab) => tab.textContent.replace(/\d+$/, "").trim()), [
    "Overview", "Interview", "Plan", "Agents", "Approvals", "Tools", "Artifacts", "Inputs", "Audit", "Events", "Usage",
  ]);
  view.cancel();
  await flush();
  assert.ok(calls.some((call) => call.path.endsWith("/runs/run_123/cancel") && call.method === "POST"));
  view.dispose();
});

test("the actual hash router keeps Run tabs and artifact selection across same-Run navigation", async () => {
  const runId = "run_router";
  const artifactId = "artifact_router";
  const run = {
    id: runId,
    session_id: "ses_router",
    prompt: "Check router navigation",
    status: "COMPLETED",
    created_at: "2026-09-26T10:00:00Z",
    agent_ids: [],
    task_ids: [],
    tool_call_ids: [],
    artifact_ids: [artifactId],
    finding_ids: [],
    final_decision: null,
  };
  const artifact = {
    id: artifactId,
    name: "router-proof.txt",
    kind: "report",
    media_type: "text/plain",
    size_bytes: 12,
    sha256: "0".repeat(64),
    produced_by: "thy",
    created_at: "2026-09-26T10:00:00Z",
    valid: true,
    input_artifact_ids: [],
  };
  const requested = [];
  const elements = new Map();
  const listeners = new Map();
  installBrowser(async (input, init = {}) => {
    const url = new URL(input, "http://localhost:8080");
    requested.push(url.pathname + url.search);
    if (url.pathname === "/api/healthz") return response({ status: "ok" });
    if (url.pathname === "/api/sessions") return response({ items: [], next_cursor: null });
    if (url.pathname === "/api/sessions/ses_router") return response(sessionDetail("ses_router"));
    if (url.pathname === "/api/project/files") return response({ path: "", entries: [] });
    if (url.pathname === "/api/runs" && !url.pathname.endsWith(`/${runId}`)) {
      return response({ items: [], next_cursor: null });
    }
    if (url.pathname === `/api/runs/${runId}`) return response(run);
    if (url.pathname === `/api/runs/${runId}/events`) return response({ items: [], has_more: false, next_after_seq: null });
    if (url.pathname === `/api/runs/${runId}/artifacts` && init.method !== "GET") {
      throw new Error(`Unexpected artifacts request method ${init.method}`);
    }
    if (url.pathname === `/api/runs/${runId}/artifacts`) return response({ items: [artifact] });
    if (url.pathname === `/api/runs/${runId}/artifacts/${artifactId}`) {
      return response({ artifact, content: null, withheld_reason: "Selected route artifact loaded." });
    }
    throw new Error(`Unexpected router request ${url.pathname}`);
  });
  const roots = ["main", "connect", "connect-form", "token", "connect-error", "connect-cancel", "lock", "api-state", "shell", "inspector", "session-rail", "run-rail"];
  globalThis.document.getElementById = (id) => {
    if (!elements.has(id)) elements.set(id, new FakeNode("div"));
    return elements.get(id);
  };
  for (const id of roots) globalThis.document.getElementById(id);
  globalThis.window.location.hash = `#/runs/${runId}/approvals`;
  globalThis.window.history = { replaceState() {} };
  const registered = new Map();
  globalThis.window.addEventListener = (name, callback, capture = false) => {
    const entries = registered.get(name) ?? [];
    entries.push({ callback, capture });
    registered.set(name, entries);
    listeners.set(name, () => {
      let stopped = false;
      const event = { preventDefault() {}, stopImmediatePropagation() { stopped = true; } };
      for (const item of [...(registered.get(name) ?? [])].sort((a, b) => Number(b.capture) - Number(a.capture))) {
        if (!stopped) item.callback(event);
      }
    });
  };
  globalThis.window.removeEventListener = (name, callback) => {
    registered.set(name, (registered.get(name) ?? []).filter((item) => item.callback !== callback));
  };
  globalThis.setInterval = () => 1;

  await import(`../src/thymira/web/static/js/main.js?router-test=${Date.now()}`);
  const main = elements.get("main");
  const activeTab = () => main.querySelectorAll(".tab").find((tab) => tab.classList.contains("is-active"))?.childNodes[0]?.textContent.trim();
  const changeHash = async (hash, expectedTab) => {
    globalThis.window.location.hash = hash;
    listeners.get("hashchange")();
    for (let attempt = 0; attempt < 30 && activeTab() !== expectedTab; attempt += 1) await flush();
    assert.equal(activeTab(), expectedTab, `route ${hash} should select ${expectedTab}`);
  };

  await changeHash(`#/runs/${runId}/approvals`, "Approvals");
  await changeHash(`#/runs/${runId}/interview`, "Interview");
  await changeHash(`#/runs/${runId}/artifacts/${artifactId}`, "Artifacts");
  for (let attempt = 0; attempt < 30 && !requested.some((path) => path.endsWith(`/artifacts/${artifactId}`)); attempt += 1) await flush();
  assert.ok(requested.some((path) => path.endsWith(`/artifacts/${artifactId}`)), "artifact route item id should reach RunView");
  assert.ok(requested.some((path) => path === `/api/runs/${runId}`), "all tab switches should stay on the same Run");
  for (let attempt = 0; attempt < 30 && !main.textContent.includes("Selected route artifact loaded."); attempt += 1) await flush();
  assert.match(main.textContent, /Selected route artifact loaded\./);
  globalThis.window.location.hash = "#/sessions/ses_router/datasets";
  listeners.get("hashchange")();
  await flush();
  assert.match(elements.get("inspector").textContent, /Upload and declare/);
  globalThis.window.location.hash = "#/sessions/ses_router/files";
  listeners.get("hashchange")();
  await flush();
  assert.match(elements.get("inspector").textContent, /Project explorer/);
  globalThis.window.location.hash = "#/sessions/ses_router/datasets";
  listeners.get("hashchange")();
  await flush();
  assert.match(elements.get("inspector").textContent, /Upload and declare/);
  await flush();
});


test("released results show registered plots in chat and preserve them across refresh", async () => {
  const content = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9f8AAAAASUVORK5CYII=";
  const { createHash } = await import("node:crypto");
  const raw = Buffer.from(content, "base64");
  const artifact = { id: "artifact_plot", name: "plots/age.png", kind: "plot", valid: true, size_bytes: raw.length, sha256: createHash("sha256").update(raw).digest("hex") };
  const saved = turn("run_plot", { status: "COMPLETED", assistant: "The EDA produced the registered plots." });
  let artifactReads = 0;
  installBrowser(async (input) => {
    const path = new URL(input).pathname;
    if (path.endsWith("/sessions/ses_123")) return response(sessionDetail("ses_123", { turns: [saved] }));
    if (path.endsWith("/artifacts")) return response({ items: [artifact] });
    if (path.endsWith("/artifact_plot")) { artifactReads++; return response({ run_id: "run_plot", artifact, encoding: "base64", content }); }
    return response({ entries: [] });
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    await view.plotViews.get("run_plot").ready;
    const image = view.transcript.querySelectorAll("img")[0];
    assert.ok(image);
    assert.equal(image.getAttribute("src"), "data:image/png;base64," + content);
    assert.ok(view.transcript.textContent.includes("plots/age.png"));
    await view.load({ silent: true });
    assert.equal(view.transcript.querySelectorAll("img")[0], image);
    assert.equal(artifactReads, 1);
  } finally { view.dispose(); }
});

test("failed Run plots remain evidence, never released results in the transcript", async () => {
  const saved = turn("run_failed", { assistant: "Unapproved output", state: { condition: "terminal", outcome: "failed", stage: "executing" } });
  let artifactReads = 0;
  installBrowser(async (input) => {
    const path = new URL(input).pathname;
    if (path.endsWith("/sessions/ses_123")) return response(sessionDetail("ses_123", { turns: [saved] }));
    if (path.includes("/artifacts")) artifactReads++;
    return response({ entries: [] });
  });
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    assert.equal(view.transcript.querySelectorAll("img").length, 0);
    assert.equal(artifactReads, 0);
    assert.ok(view.transcript.querySelectorAll("a").some((link) => link.textContent === "Artifacts"));
  } finally { view.dispose(); }
});


test("released structured output is readable and model HTML remains text", async () => {
  const message = JSON.stringify({ row_count: 27, columns: ["age", "income"], note: '<img src=x onerror="alert(1)">' });
  const saved = turn("run_summary", { status: "COMPLETED", assistant: message });
  installBrowser(async (input) => new URL(input).pathname.endsWith("/sessions/ses_123")
    ? response(sessionDetail("ses_123", { turns: [saved] })) : response({ entries: [], items: [] }));
  const view = new SessionView(new FakeNode("main"), new FakeNode("aside"), "ses_123");
  try {
    await flush();
    assert.ok(view.transcript.querySelectorAll("dl").length);
    assert.match(view.transcript.textContent, /row count27/);
    assert.ok(view.transcript.textContent.includes('<img src=x onerror="alert(1)">'));
    assert.equal(view.transcript.querySelectorAll("img").length, 0);
  } finally { view.dispose(); }
});
