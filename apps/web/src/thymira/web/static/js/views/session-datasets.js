import { api } from "../api.js";
import { h, replace } from "../dom.js";

// This is a project input summary, not a separate browser-owned Session dataset store.
export class SessionDatasetStatus {
  constructor(root, { onChange, onManage }) {
    this.root = root;
    this.onChange = onChange;
    this.onManage = onManage;
    this.controller = new AbortController();
    this.generation = 0;
    this.disposed = false;
    this.mutations = 0;
    this.ready = false;
    this.message = "Checking project datasets…";
    this.refresh();
  }

  dispose() {
    this.disposed = true;
    this.controller.abort();
  }

  mutationPending(busy) {
    this.mutations = Math.max(0, this.mutations + (busy ? 1 : -1));
    if (busy) {
      this.generation += 1;
      this.ready = false;
      this.message = "Uploading and validating the dataset…";
      this.render();
    } else if (!this.mutations) this.refresh();
  }

  async refresh() {
    if (this.disposed || this.mutations) return;
    const generation = ++this.generation;
    this.ready = false;
    this.message = "Checking project datasets…";
    this.render();
    try {
      const body = await api.projectDatasets({ signal: this.controller.signal });
      if (this.disposed || generation !== this.generation) return;
      const missing = body.items.filter((item) => !item.present);
      this.ready = missing.length === 0;
      this.message = missing.length
        ? `Missing: ${missing.map((item) => `${item.name} (${item.path})`).join(", ")}. Upload a replacement or remove the declaration before sending.`
        : body.items.length
          ? `Available: ${body.items.map((item) => item.name).join(", ")}. Shared by this project's sessions.`
          : "No project datasets yet. Add a CSV or Parquet file for data analysis.";
    } catch (error) {
      if (this.disposed || generation !== this.generation || error?.name === "AbortError") return;
      this.ready = false;
      this.message = `Could not check datasets: ${error?.message ?? "request failed"}. Refresh before sending.`;
    }
    this.render();
  }

  render() {
    if (this.disposed) return;
    replace(this.root,
      h("button", { class: "btn btn-small", type: "button", onClick: this.onManage }, "Add / manage datasets"),
      h("span", { class: "conversation-dataset-status", role: "status" }, this.message),
      h("button", { class: "btn btn-small", type: "button", disabled: this.mutations > 0, onClick: () => this.refresh() }, "Refresh datasets"),
    );
    this.onChange();
  }
}
