import { api } from "../api.js";
import { h, replace } from "../dom.js";
import { errorNotice, loading, notice } from "../ui.js";

function parentPath(path) {
  return String(path ?? "").split("/").filter(Boolean).slice(0, -1).join("/");
}

function readOnlyMessage(result) {
  if (result?.read_only_reason === "redacted") {
    return "This file contains content that is redacted in previews and cannot be edited here.";
  }
  if (result?.truncated) return "This preview is truncated at 64 KiB and cannot be edited here.";
  return "This file is available as a read-only preview.";
}

export class ProjectExplorerView {
  constructor(root, { initialPath = "" } = {}) {
    this.root = root;
    this.path = initialPath;
    this.selectedFile = null;
    this.controller = null;
    this.editor = null;
    this.saveButton = null;
    this.saveStatus = null;
    this.revisionSha256 = null;
    this.baseContent = "";
    this.editable = false;
    this.dirty = false;
    this.saving = false;
    this.routeHash = window.location.hash;
    this.head = h("header", { class: "explorer-head" });
    this.body = h("div", { class: "explorer-body" }, loading("Reading the project directory…"));
    replace(
      root,
      h("section", { class: "project-explorer" },
        this.head,
        notice("Existing UTF-8 project files up to 64 KiB can be edited when their previews contain no redactions.", "neutral"),
        this.body,
      ),
    );
    this.onBeforeUnload = (event) => {
      if (!this.dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    this.onDocumentClick = (event) => {
      if (!this.dirty) return;
      const anchor = event.target?.closest?.('a[href^="#/"]');
      if (anchor && !this.confirmDiscardDraft()) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    this.onHashChange = (event) => {
      if (!this.dirty) {
        this.routeHash = window.location.hash;
        return;
      }
      if (this.confirmDiscardDraft()) {
        this.routeHash = window.location.hash;
        return;
      }
      const { pathname = "", search = "" } = window.location;
      window.history?.replaceState?.(null, "", `${pathname}${search}${this.routeHash}`);
      event.preventDefault?.();
      event.stopImmediatePropagation?.();
    };
    window.addEventListener?.("beforeunload", this.onBeforeUnload);
    window.addEventListener?.("hashchange", this.onHashChange, true);
    document.addEventListener?.("click", this.onDocumentClick, true);
    this.loadDirectory();
  }

  dispose() {
    this.controller?.abort();
    window.removeEventListener?.("beforeunload", this.onBeforeUnload);
    window.removeEventListener?.("hashchange", this.onHashChange, true);
    document.removeEventListener?.("click", this.onDocumentClick, true);
  }

  confirmDiscardDraft() {
    if (!this.dirty) return true;
    if (this.saving) {
      if (this.saveStatus) this.saveStatus.textContent = "Wait for the save to finish before navigating.";
      return false;
    }
    const confirm = window.confirm;
    if (typeof confirm !== "function" || !confirm("Discard unsaved changes to this file?")) {
      return false;
    }
    this.dirty = false;
    this.renderHead();
    return true;
  }

  resetFileState() {
    this.editor = null;
    this.saveButton = null;
    this.saveStatus = null;
    this.revisionSha256 = null;
    this.baseContent = "";
    this.editable = false;
    this.dirty = false;
    this.saving = false;
  }

  async loadDirectory(path = this.path) {
    if (!this.confirmDiscardDraft()) return;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    this.path = path;
    this.selectedFile = null;
    this.resetFileState();
    this.renderHead();
    replace(this.body, loading("Reading the project directory…"));
    try {
      const result = await api.projectFiles(path, { signal: controller.signal });
      if (controller.signal.aborted) return;
      this.renderEntries(result);
    } catch (error) {
      if (controller.signal.aborted) return;
      replace(this.body, errorNotice(error));
    }
  }

  async openFile(path) {
    if (path === this.selectedFile || !this.confirmDiscardDraft()) return;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    this.selectedFile = path;
    this.resetFileState();
    this.renderHead();
    replace(this.body, loading("Reading the file preview…"));
    try {
      const result = await api.projectFile(path, { signal: controller.signal });
      if (controller.signal.aborted) return;
      this.renderFile(result);
    } catch (error) {
      if (controller.signal.aborted) return;
      replace(this.body, errorNotice(error));
    }
  }

  renderFile(result) {
    const content = String(result?.content ?? "");
    const editable = result?.editable === true && !result.truncated && typeof result.sha256 === "string";
    if (!editable) {
      replace(
        this.body,
        notice(readOnlyMessage(result), "warn"),
        h("pre", { class: "explorer-preview mono" }, content),
      );
      return;
    }

    this.editable = true;
    this.baseContent = content;
    this.revisionSha256 = result.sha256;
    const editor = h("textarea", {
      class: ["input", "explorer-editor", "mono"],
      rows: 24,
      "aria-label": `Edit ${result.path}`,
      spellcheck: false,
    });
    editor.value = content;
    this.editor = editor;
    this.saveStatus = h("p", { class: "form-status", role: "status", "aria-live": "polite" });
    this.saveButton = h(
      "button",
      { class: ["btn", "btn-primary"], type: "button", disabled: true, onClick: () => this.saveFile() },
      "Save",
    );
    editor.addEventListener("input", () => this.onEditorInput());
    editor.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        this.saveFile();
      }
    });
    replace(
      this.body,
      h("div", { class: "explorer-editor-actions" },
        this.saveStatus,
        h("span", { class: "muted small" }, "Ctrl+S to save"),
        this.saveButton,
      ),
      editor,
    );
  }

  onEditorInput() {
    if (!this.editor) return;
    this.dirty = this.editor.value !== this.baseContent;
    if (this.saveButton) this.saveButton.disabled = !this.dirty || this.saving;
    if (this.saveStatus) this.saveStatus.textContent = this.dirty ? "Unsaved changes" : "";
    this.renderHead();
  }

  async saveFile() {
    if (!this.editable || !this.dirty || this.saving || !this.editor || !this.revisionSha256) return;
    this.saving = true;
    if (this.editor) this.editor.disabled = true;
    if (this.saveButton) this.saveButton.disabled = true;
    if (this.saveStatus) this.saveStatus.textContent = "Saving…";
    try {
      const result = await api.saveProjectFile(
        this.selectedFile,
        this.editor.value,
        this.revisionSha256,
      );
      this.baseContent = typeof result?.content === "string" ? result.content : this.editor.value;
      this.editor.value = this.baseContent;
      this.revisionSha256 = result?.sha256 ?? this.revisionSha256;
      this.dirty = false;
      if (this.saveStatus) this.saveStatus.textContent = "Saved.";
    } catch (error) {
      const code = error?.code ? `${error.code}: ` : "";
      if (this.saveStatus) this.saveStatus.textContent = `${code}${error?.message ?? error}`;
    } finally {
      this.saving = false;
      if (this.editor) this.editor.disabled = false;
      if (this.saveButton) this.saveButton.disabled = !this.dirty;
      this.renderHead();
    }
  }

  renderHead() {
    const title = this.selectedFile ?? (this.path || ".");
    replace(
      this.head,
      h("div", { class: "explorer-title-row" },
        this.selectedFile
          ? h("button", { class: "btn btn-small", type: "button", disabled: this.saving, onClick: () => this.loadDirectory(parentPath(this.selectedFile)) }, "← Folder")
          : this.path
            ? h("button", { class: "btn btn-small", type: "button", disabled: this.saving, onClick: () => this.loadDirectory(parentPath(this.path)) }, "↑ Up")
            : null,
        h("div", { class: "explorer-title" },
          h("p", { class: "eyebrow" }, "Project explorer"),
          h("h2", { title }, title),
        ),
        this.dirty ? h("span", { class: "tag tone-warn" }, "Unsaved") : null,
      ),
    );
  }

  renderEntries(result) {
    const entries = Array.isArray(result?.entries) ? result.entries : [];
    if (!entries.length) {
      replace(this.body, result?.truncated ? notice("The directory entry limit was reached.", "warn") : h("p", { class: "empty" }, "This directory is empty."));
      return;
    }
    const rows = entries.map((entry) =>
      h(
        "li",
        null,
        h(
          "button",
          {
            class: "explorer-entry",
            type: "button",
            onClick: () => entry.kind === "directory" ? this.loadDirectory(entry.path) : this.openFile(entry.path),
          },
          h("span", { class: "explorer-entry-kind", "aria-hidden": "true" }, entry.kind === "directory" ? "▸" : "·"),
          h("span", { class: "explorer-entry-name" }, entry.name),
          h("span", { class: "explorer-entry-type" }, entry.kind === "directory" ? "Folder" : "File"),
        ),
      ),
    );
    replace(
      this.body,
      result?.truncated ? notice("Showing the first 200 entries.", "warn") : null,
      h("ul", { class: "explorer-list", "aria-label": "Project files" }, rows),
    );
  }
}
