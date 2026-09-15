// The project page: what this API process serves. One API process serves one workspace, so there is
// no switcher here — this is that workspace's identity, its knowledge (.thymira/context.md) and its
// declared datasets, which every thread started afterwards reads.
//
// The two editable sections are rendered by views/inputs.js, unchanged: a thread never owns them.

import { h, replace } from "../dom.js";
import { integer } from "../format.js";
import { icon } from "../icons.js";
import { href } from "../router.js";
import { tag } from "../ui.js";
import { renderProjectInputs } from "./inputs.js";

export class ProjectView {
  constructor(root, { project = () => null } = {}) {
    this.head = h("header", { class: "project-head" });
    replace(root, h("section", { class: "project" }, this.head, renderProjectInputs()));
    this.setProject(project());
  }

  setProject(summary) {
    const frameworks = Array.isArray(summary?.frameworks) ? summary.frameworks : [];
    const datasets = Array.isArray(summary?.datasets) ? summary.datasets : [];
    replace(
      this.head,
      icon("database", { size: 20 }),
      h("h1", null, `Project · ${summary?.name ?? "Workspace"}`),
      h("a", { class: "btn btn-primary btn-small", href: href({ view: "home" }) }, "New thread"),
      h(
        "div",
        { class: "chips" },
        summary?.domain ? tag(summary.domain, "mira") : null,
        frameworks.map((framework) => tag(String(framework), "neutral")),
        summary ? tag(summary.audit_required ? "AUDIT REQUIRED" : "AUDIT OPTIONAL", summary.audit_required ? "review" : "neutral") : null,
        datasets.length ? tag(`${integer(datasets.length)} declared`, "neutral") : null,
      ),
    );
  }

  dispose() {}
}
