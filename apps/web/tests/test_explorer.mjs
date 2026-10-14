import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ProjectExplorerView } from "../src/thymira/web/static/js/views/explorer.js";

class FakeNode {
  constructor(tagName = "#text", text = "") {
    this.tagName = tagName.toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.attributes = new Map();
    this.className = "";
    this.value = "";
    this.disabled = false;
    this.text = text;
    this.classList = { contains: (name) => this.className.split(/\s+/).includes(name) };
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

  removeEventListener(name) {
    this.listeners.delete(name);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "disabled") this.disabled = true;
    if (name === "class") this.className = String(value);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  closest(selector) {
    if (selector === 'a[href^="#/"]' && this.tagName === "A" && this.getAttribute("href")?.startsWith("#/")) {
      return this;
    }
    return this.parentNode?.closest?.(selector) ?? null;
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

  dispatch(name, fields = {}) {
    let prevented = false;
    let stopped = false;
    this.listeners.get(name)?.({
      ...fields,
      target: this,
      preventDefault: () => { prevented = true; },
      stopImmediatePropagation: () => { stopped = true; },
    });
    return { prevented, stopped };
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

function installBrowser(fetchImpl, { confirm = () => true } = {}) {
  for (const key of ["Node", "document", "window", "sessionStorage", "fetch"]) {
    originals[key] = globalThis[key];
  }
  globalThis.Node = FakeNode;
  const documentListeners = new Map();
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => new FakeTextNode(text),
    addEventListener: (name, callback) => documentListeners.set(name, callback),
    removeEventListener: (name) => documentListeners.delete(name),
    listeners: documentListeners,
  };
  const windowListeners = new Map();
  globalThis.window = {
    location: { origin: "http://localhost:8080", hash: "#/project/files", pathname: "/", search: "" },
    history: {
      replaceState: (_state, _title, url) => {
        const parsed = new URL(url, "http://localhost:8080");
        globalThis.window.location.hash = parsed.hash;
      },
    },
    confirm,
    addEventListener: (name, callback) => windowListeners.set(name, callback),
    removeEventListener: (name) => windowListeners.delete(name),
    listeners: windowListeners,
    dispatchEvent() {},
  };
  globalThis.sessionStorage = { getItem: () => "test-token", setItem() {}, removeItem() {} };
  globalThis.fetch = fetchImpl;
  return { documentListeners, windowListeners };
}

afterEach(() => {
  for (const [key, value] of Object.entries(originals)) {
    if (value === undefined) delete globalThis[key];
    else globalThis[key] = value;
  }
});

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

async function flush() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}

function buttonWithText(root, text) {
  return root.querySelectorAll("button").find((button) => button.textContent.includes(text));
}

function filePreview(overrides = {}) {
  return {
    path: "readme.md",
    content: "Original text\n",
    truncated: false,
    sha256: "a".repeat(64),
    editable: true,
    read_only_reason: null,
    ...overrides,
  };
}

test("the explorer saves a bounded edit with Ctrl+S and adopts its new revision", async () => {
  const requests = [];
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    requests.push({ method: init.method ?? "GET", url, body: init.body ? JSON.parse(init.body) : null });
    if (url.pathname === "/api/project/files") {
      return jsonResponse({ path: "", entries: [{ path: "readme.md", name: "readme.md", kind: "file" }], truncated: false });
    }
    if (url.pathname === "/api/project/file" && init.method === "GET") return jsonResponse(filePreview());
    if (url.pathname === "/api/project/file" && init.method === "PUT") {
      return jsonResponse(filePreview({ content: requests.at(-1).body.content, sha256: "b".repeat(64) }));
    }
    throw new Error(`Unexpected request: ${init.method} ${url.pathname}`);
  });
  const root = new FakeNode("main");
  const view = new ProjectExplorerView(root);
  await flush();
  buttonWithText(root, "readme.md").dispatch("click");
  await flush();

  const editor = root.querySelectorAll("textarea")[0];
  assert.ok(editor);
  editor.value = "Updated text\n";
  editor.dispatch("input");
  const shortcut = editor.dispatch("keydown", { key: "s", ctrlKey: true });
  await flush();

  assert.equal(shortcut.prevented, true);
  assert.deepEqual(requests.at(-1).body, {
    path: "readme.md",
    content: "Updated text\n",
    expected_sha256: "a".repeat(64),
  });
  assert.equal(editor.value, "Updated text\n");
  assert.equal(view.dirty, false);
  assert.equal(view.revisionSha256, "b".repeat(64));
  assert.equal(view.saveStatus.textContent, "Saved.");
  const folderButton = buttonWithText(root, "Folder");
  assert.ok(folderButton);
  assert.equal(folderButton.disabled, false);
  folderButton.dispatch("click");
  await flush();
  assert.equal(view.selectedFile, null);
  view.dispose();
});

test("a stale save keeps the editor draft and reports the conflict", async () => {
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/files")) return jsonResponse({ entries: [], truncated: false });
    if (init.method === "GET") return jsonResponse(filePreview());
    return jsonResponse({ code: "project_file_conflict", message: "Reload before saving." }, 409);
  });
  const root = new FakeNode("main");
  const view = new ProjectExplorerView(root);
  await flush();
  await view.openFile("readme.md");
  const editor = root.querySelectorAll("textarea")[0];
  editor.value = "Local draft\n";
  editor.dispatch("input");

  await view.saveFile();

  assert.equal(view.dirty, true);
  assert.equal(editor.value, "Local draft\n");
  assert.match(view.saveStatus.textContent, /project_file_conflict/);
  assert.equal(view.saveButton.disabled, false);
  view.dispose();
});

test("redacted previews stay read-only", async () => {
  installBrowser(async (input, init = {}) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/files")) return jsonResponse({ entries: [], truncated: false });
    return jsonResponse(filePreview({
      content: "Owner: [REDACTED:EMAIL]\n",
      sha256: "c".repeat(64),
      editable: false,
      read_only_reason: "redacted",
    }));
  });
  const root = new FakeNode("main");
  const view = new ProjectExplorerView(root);
  await flush();
  await view.openFile("readme.md");
  assert.equal(root.querySelectorAll("textarea").length, 0);
  assert.match(root.textContent, /redacted in previews/);
  view.dispose();
});

test("dirty folder navigation asks before discarding the editor draft", async () => {
  let confirmResult = false;
  let directoryReads = 0;
  installBrowser(async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/files")) {
      directoryReads += 1;
      return jsonResponse({ entries: [], truncated: false });
    }
    return jsonResponse(filePreview());
  }, { confirm: () => confirmResult });
  const root = new FakeNode("main");
  const view = new ProjectExplorerView(root);
  await flush();
  await view.openFile("editable.md");
  const editor = root.querySelectorAll("textarea")[0];
  editor.value = "Draft";
  editor.dispatch("input");
  await view.loadDirectory("folder");
  assert.equal(directoryReads, 1);
  assert.equal(view.selectedFile, "editable.md");

  confirmResult = true;
  await view.loadDirectory("folder");
  assert.equal(directoryReads, 2);
  assert.equal(view.dirty, false);
  view.dispose();
});

test("route navigation with a dirty draft can be cancelled", async () => {
  const { windowListeners } = installBrowser(async (input) => {
    const url = new URL(input);
    return url.pathname.endsWith("/files")
      ? jsonResponse({ entries: [], truncated: false })
      : jsonResponse(filePreview());
  }, { confirm: () => false });
  const root = new FakeNode("main");
  const view = new ProjectExplorerView(root);
  await flush();
  await view.openFile("readme.md");
  const editor = root.querySelectorAll("textarea")[0];
  editor.value = "Draft";
  editor.dispatch("input");
  window.location.hash = "#/sessions/session_1";
  let stopped = false;
  windowListeners.get("hashchange")({
    preventDefault() {},
    stopImmediatePropagation() { stopped = true; },
  });

  assert.equal(stopped, true);
  assert.equal(window.location.hash, "#/project/files");
  assert.equal(view.dirty, true);
  view.dispose();
});
