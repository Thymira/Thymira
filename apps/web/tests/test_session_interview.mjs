import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { SessionInterviewView } from "../src/thymira/web/static/js/views/session-interview.js";

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

  focus() {}

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

const status = (questionId = "question_1") => ({
  profile: { id: "profile_1", decision_effect: "Offline analysis only", data_categories: ["historical records"] },
  pending_question: questionId ? { event_id: questionId, field: "decision_effect", question: "How will the result affect decisions?", profile_version: 1 } : null,
});

test("unchanged polling preserves the answer node, draft and availability", async () => {
  installBrowser(async () => response(status()));
  const view = new SessionInterviewView(new FakeNode("main"));
  view.runId = "run_1";
  await view.refresh();
  const field = view.answerField;
  field.value = "An analyst reviews this offline research.";
  await view.refresh();
  assert.equal(view.answerField, field);
  assert.equal(field.value, "An analyst reviews this offline research.");
  assert.equal(field.disabled, false);
  assert.equal(view.submitButton.disabled, false);
  assert.doesNotMatch(view.root.textContent, /Refreshing|paused/);
});

test("an answer targets the displayed question in the same Run", async () => {
  const requests = [];
  installBrowser(async (url, options) => {
    requests.push({ path: new URL(url).pathname, ...options });
    return response(options.method === "POST" ? status(null) : status());
  });
  let updates = 0;
  const view = new SessionInterviewView(new FakeNode("main"), { onUpdated: () => { updates += 1; } });
  view.runId = "run_1";
  await view.refresh();
  view.answerField.value = "No operational decisions; only exploratory analysis.";
  await view.answer();
  const post = requests.find((request) => request.method === "POST");
  assert.equal(post.path, "/api/runs/run_1/risk-interview");
  assert.deepEqual(JSON.parse(post.body), { answer: "No operational decisions; only exploratory analysis.", expected_question_id: "question_1" });
  assert.equal(updates, 1);
  assert.equal(view.answerField, null);
});

test("a conflicting answer retains the draft and waits for fresh evidence", async () => {
  installBrowser(async (_url, options) => options.method === "POST"
    ? response({ code: "risk_interview_not_pending", message: "Question changed" }, 409)
    : response(status()));
  const view = new SessionInterviewView(new FakeNode("main"));
  view.runId = "run_1";
  await view.refresh();
  view.answerField.value = "My answer";
  await view.answer();
  assert.equal(view.answerField.value, "My answer");
  assert.equal(view.submitButton.disabled, true);
  assert.match(view.root.textContent, /Question changed/);
  await view.refresh();
  assert.equal(view.answerField.value, "My answer");
  assert.equal(view.submitButton.disabled, false);
});

test("clarification is bound to the visible decision and never approves it", async () => {
  const requests = [];
  installBrowser(async (url, options) => {
    requests.push({ path: new URL(url).pathname, ...options });
    return response(options.method === "POST" ? status() : status(null));
  });
  const view = new SessionInterviewView(new FakeNode("main"));
  view.runId = "run_1";
  await view.refresh();
  await view.openClarification({ runId: "run_1", decisionId: "decision_start", missingInformation: ["decision_role"] });
  assert.equal(view.fieldSelect.value, "decision_effect");
  assert.match(view.root.textContent, /Execution stays stopped/);
  await view.clarify();
  const post = requests.find((request) => request.method === "POST");
  assert.equal(post.path, "/api/runs/run_1/risk-interview/clarify");
  assert.deepEqual(JSON.parse(post.body), { field: "decision_effect", expected_decision_id: "decision_start" });
  assert.ok(view.answerField);
});

test("a late poll cannot replace a newly recorded answer", async () => {
  let resolvePoll;
  let polls = 0;
  installBrowser(async (_url, options) => {
    if (options.method === "POST") return response(status(null));
    if (++polls === 1) return response(status());
    return new Promise((resolve) => { resolvePoll = resolve; });
  });
  const view = new SessionInterviewView(new FakeNode("main"));
  view.runId = "run_1";
  await view.refresh();
  const poll = view.refresh();
  view.answerField.value = "A sufficient answer";
  await view.answer();
  resolvePoll(response(status()));
  await poll;
  assert.equal(view.status.pending_question, null);
  assert.equal(view.answerField, null);
});
