import { api } from "../api.js";
import { artifactImageSource } from "../artifact-image.js";
import { h, replace } from "../dom.js";
import { errorNotice, notice } from "../ui.js";

/** Display bounded registered plots alongside a released conversation response. */
export class SessionPlotsView {
  constructor(root, runId) {
    this.root = root;
    this.runId = runId;
    this.controller = new AbortController();
    this.ready = this.load();
  }

  dispose() { this.controller.abort(); }

  async load() {
    const options = { signal: this.controller.signal };
    try {
      const inventory = await api.artifacts(this.runId, options);
      if (this.controller.signal.aborted) return;
      const plots = (inventory.items ?? []).filter((item) => item.valid && item.kind === "plot");
      if (!plots.length) return;
      const cards = [];
      for (const artifact of plots.slice(0, 6)) {
        const card = h("figure", { class: "session-plot" }, h("figcaption", {}, artifact.name));
        cards.push(card);
        try {
          const result = await api.artifact(this.runId, artifact.id, options);
          const source = await artifactImageSource(result, this.runId, artifact.id);
          if (this.controller.signal.aborted) return;
          card.append(source
            ? h("img", { class: "preview-image", alt: artifact.name, src: source, loading: "lazy" })
            : notice(result.withheld_reason ?? "This plot has no safe raster preview. Open Artifacts to inspect it.", "warn"));
        } catch (error) {
          if (this.controller.signal.aborted) return;
          card.append(errorNotice(error));
        }
      }
      replace(this.root, h("section", { class: "session-plots", "aria-label": "Registered plots" },
        h("h3", {}, "Plots"), h("div", { class: "session-plot-grid" }, ...cards),
        plots.length > 6 ? h("p", { class: "muted small" }, "Showing six plots. Open Artifacts for the complete collection.") : null));
    } catch (error) {
      if (!this.controller.signal.aborted) replace(this.root, errorNotice(error));
    }
  }
}
