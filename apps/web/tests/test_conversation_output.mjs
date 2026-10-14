import assert from "node:assert/strict";
import { test } from "node:test";

import { conversationOutput } from "../src/thymira/web/static/js/views/conversation-output.js";
import { terminalDiagnostic } from "../src/thymira/web/static/js/views/session-diagnostics.js";

class FakeNode {
  constructor(tagName, text = "") {
    this.tagName = tagName.toUpperCase();
    this.text = text;
    this.childNodes = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
    };
  }

  append(...nodes) {
    this.childNodes.push(...nodes);
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

  querySelectorAll(selector) {
    const matches = (node) => selector.startsWith(".")
      ? node.classList.contains(selector.slice(1))
      : node.tagName.toLowerCase() === selector;
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

  get textContent() {
    return this.text + this.childNodes.map((child) => child.textContent).join("");
  }
}

class FakeTextNode extends FakeNode {
  constructor(text) {
    super("#text", String(text));
  }
}

function withDom(callback) {
  const previousNode = globalThis.Node;
  const previousDocument = globalThis.document;
  globalThis.Node = FakeNode;
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => new FakeTextNode(text),
  };
  try {
    return callback();
  } finally {
    if (previousNode === undefined) delete globalThis.Node;
    else globalThis.Node = previousNode;
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
}

test("conversation output formats Markdown and links internal artifact routes safely", () => {
  withDom(() => {
    const output = conversationOutput([
      "## Evaluation",
      "**Generated model:** logistic regression.",
      "- Precision: 0.71",
      "- Recall: 0.69",
      "| Metric | Value |\n| --- | --- |\n| F1 | 0.69 |",
      "```json\n{\"validated\": true}\n```",
      "[logistic_regression_model.pkl](#/runs/run_123/artifacts/artifact_456)",
      "[unsafe](javascript:alert(1)) <img src=x onerror=alert(1)>",
    ].join("\n\n"));

    assert.equal(output.querySelectorAll("h2").length, 1);
    assert.equal(output.querySelectorAll("strong").length, 1);
    assert.equal(output.querySelectorAll("ul").length, 1);
    assert.equal(output.querySelectorAll("table").length, 1);
    assert.equal(output.querySelectorAll("pre").length, 1);
    const artifactLink = output.querySelectorAll("a").find((node) => node.textContent === "logistic_regression_model.pkl");
    assert.equal(artifactLink?.getAttribute("href"), "#/runs/run_123/artifacts/artifact_456");
    assert.equal(output.querySelectorAll("img").length, 0);
    assert.equal(output.querySelectorAll("a").some((node) => node.getAttribute("href")?.startsWith("javascript:")), false);
    assert.match(output.textContent, /<img src=x onerror=alert\(1\)>/);
  });
});

test("terminal diagnostic says its button prepares an Ask question, not an explanation", () => {
  withDom(() => {
    const diagnostic = terminalDiagnostic({
      id: "run_failed",
      state: { condition: "terminal", outcome: "failed" },
      terminal_diagnostic: { summary: "The metrics artifact was incomplete." },
    }, () => {});

    assert.match(diagnostic.textContent, /prepares a question in Ask mode/i);
    assert.match(diagnostic.textContent, /appears as your message/i);
    assert.match(diagnostic.textContent, /assistant.*explanation/i);
  });
});
