import { api } from "../api.js";
import { artifactImageSource } from "../artifact-image.js";
import { h, replace } from "../dom.js";
import { bytes, timestamp } from "../format.js";
import { errorNotice, loading, notice } from "../ui.js";

export class ArtifactInspectorView {
  constructor(root, runId) {
    this.root = root;
    this.runId = runId;
    this.items = [];
    this.selectedId = null;
    this.controller = new AbortController();
    this.list = h("ul", { class: "inspector-artifact-list" });
    this.preview = h("div", { class: "inspector-artifact-preview" }, loading("Reading artifacts…"));
    this.fullView = h("a", {
      class: "btn btn-small",
      href: `#/runs/${encodeURIComponent(runId)}/artifacts`,
    }, "Full artifact view");
    replace(root, h("div", { class: "artifact-inspector" }, this.fullView, this.list, this.preview));
    this.load();
  }

  dispose() {
    this.controller.abort();
  }

  async load() {
    try {
      const result = await api.artifacts(this.runId, { signal: this.controller.signal });
      if (this.controller.signal.aborted) return;
      this.items = Array.isArray(result?.items) ? result.items : [];
      this.list.replaceChildren(
        ...this.items.map((artifact) =>
          h("li", null,
            h("button", {
              class: ["inspector-artifact", artifact.valid ? null : "is-superseded"],
              type: "button",
              dataset: { artifactId: artifact.id },
              onClick: () => this.select(artifact),
            },
            h("span", { class: "inspector-artifact-name" }, artifact.name),
            h("span", { class: "inspector-artifact-meta" }, `${bytes(artifact.size_bytes)} · ${artifact.valid ? "Active" : "Superseded"}`),
            ),
          ),
        ),
      );
      if (!this.items.length) {
        replace(this.preview, h("p", { class: "empty" }, "This run has not produced artifacts yet."));
        return;
      }
      const initial = this.items.find((artifact) => artifact.valid) ?? this.items[this.items.length - 1];
      await this.select(initial);
    } catch (error) {
      if (!this.controller.signal.aborted) replace(this.preview, errorNotice(error));
    }
  }

  async select(artifact) {
    if (!artifact?.id) return;
    this.selectedId = artifact.id;
    for (const button of this.list.querySelectorAll(".inspector-artifact")) {
      button.classList.toggle("is-selected", button.dataset.artifactId === artifact.id);
    }
    replace(this.preview, loading("Verifying artifact content…"));
    try {
      const result = await api.artifact(this.runId, artifact.id, { signal: this.controller.signal });
      if (this.controller.signal.aborted || this.selectedId !== artifact.id) return;
      const summary = h("dl", { class: "inspector-artifact-summary" },
        h("dt", null, "Name"), h("dd", { class: "mono" }, artifact.name),
        h("dt", null, "Kind"), h("dd", null, artifact.kind ?? "—"),
        h("dt", null, "Created"), h("dd", null, timestamp(artifact.created_at)),
      );
      if (result.content === null || result.content === undefined) {
        replace(this.preview, summary, notice(result.withheld_reason ?? "This content is withheld by the API.", "warn"));
      } else if (result.encoding === "base64") {
        const source = await artifactImageSource(result, this.runId, artifact.id);
        if (this.controller.signal.aborted || this.selectedId !== artifact.id) return;
        replace(this.preview, summary, source
          ? h("img", { class: "preview-image", alt: artifact.name, src: source })
          : notice(`Binary content (${bytes(artifact.size_bytes)}). Open the full artifact view to download it.`, "neutral"));
      } else {
        replace(this.preview, summary, result.redacted ? notice("Redacted content projection supplied by the API.", "warn") : null,
          h("pre", { class: "explorer-preview mono" }, result.content));
      }
    } catch (error) {
      if (!this.controller.signal.aborted && this.selectedId === artifact.id) replace(this.preview, errorNotice(error));
    }
  }
}
