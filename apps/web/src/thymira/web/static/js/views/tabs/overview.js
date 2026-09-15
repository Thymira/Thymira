import { h } from "../../dom.js";
import { clip, integer, list, percent, timestamp, usd } from "../../format.js";
import {
  auditFindings,
  countBy,
  foldAgents,
  latestActivityProfile,
  latestRiskProfile,
  latestRunState,
  policyDecisions,
} from "../../fold.js";
import { decisionLabel, decisionTone, riskTone } from "../../status.js";
import { chips, copyable, decisionTag, empty, findingList, kv, mono, panel, table, tag } from "../../ui.js";

// The panel is the record, not the call to action: the pending question and the pending review are
// the transcript's and the composer's job, so this panel no longer repeats them as callouts.
export function renderOverview(ctx) {
  const { run, events } = ctx;
  const state = latestRunState(events);
  const { agents } = foldAgents(events);
  const cost = agents.reduce((sum, agent) => sum + agent.cost, 0);
  return h(
    "div",
    { class: "overview" },
    h(
      "div",
      { class: "columns" },
      panel(
        "Lifecycle",
        kv([
          ["Stage", state?.stage],
          ["Condition", state?.condition],
          ["Outcome", state?.outcome],
          ["Waiting for", state?.wait_reason],
          ["Final decision", decisionTag(run.final_decision)],
          ["Policy decision", run.policy_decision_id ? copyable(run.policy_decision_id) : null],
          ["Created", timestamp(run.created_at)],
          ["Started", run.started_at ? timestamp(run.started_at) : null],
          ["Completed", run.completed_at ? timestamp(run.completed_at) : null],
          ["Session", run.session_id ? mono(run.session_id) : null],
          ["Allowed models", chips(run.model_route_policy?.allowed_routes)],
          ["Agent cost", usd(cost)],
        ]),
      ),
      riskPanel(events),
      profilePanel(events),
      decisionsPanel(events),
      panel("Audit findings", findingList(auditFindings(events)), { className: "span-2 is-mira" }),
    ),
  );
}

function riskPanel(events) {
  const risk = latestRiskProfile(events);
  const aside = h("span", { class: "muted small" }, "a classification, never an authorization");
  if (!risk) return panel("Risk classification", empty("MIRA has not classified this run yet."), { aside });
  return panel(
    "Risk classification",
    kv([
      ["Level", tag(String(risk.risk_level ?? "unknown").toUpperCase(), riskTone(risk.risk_level))],
      ["Category", risk.activity_category],
      ["Confidence", percent(risk.confidence)],
      ["Human review", risk.needs_human_review ? tag("REQUIRED", "review") : "not required"],
      ["Risk factors", chips(risk.risk_factors)],
      ["Missing information", chips(risk.missing_information, "warn")],
    ]),
    { aside },
  );
}

function profilePanel(events) {
  const profile = latestActivityProfile(events);
  if (!profile) return panel("Activity profile", empty("No activity profile recorded yet."), { className: "span-2" });
  return panel(
    "Activity profile",
    kv([
      ["Purpose", profile.purpose],
      ["Jurisdiction", profile.jurisdiction],
      ["Decision effect", profile.decision_effect],
      ["Affected population", profile.affected_population],
      ["Autonomy", profile.autonomy],
      ["Human oversight", profile.human_oversight],
      ["Data categories", list(profile.data_categories)],
      ["Sensitive attributes", list(profile.sensitive_attributes)],
      ["Potential consequences", list(profile.potential_consequences)],
    ]),
    { aside: profile.version ? h("span", { class: "muted small" }, `version ${profile.version}`) : null, className: "span-2" },
  );
}

function decisionsPanel(events) {
  const decisions = policyDecisions(events);
  const aside = h("span", { class: "muted small" }, "decided by the Policy Engine");
  if (!decisions.length) return panel("Policy decisions", empty("No policy decision recorded yet."), { aside, className: "span-2" });
  const counts = countBy(decisions, (decision) => decision.decision);
  const notable = decisions.filter((decision) => decision.decision !== "PASS").slice(-8).reverse();
  return panel(
    "Policy decisions",
    [
      h(
        "div",
        { class: "summary-strip" },
        [...counts].map(([decision, count]) =>
          h(
            "div",
            { class: `stat tone-${decisionTone(decision)}` },
            h("span", { class: "stat-label" }, decisionLabel(decision)),
            h("span", { class: "stat-value" }, integer(count)),
          ),
        ),
      ),
      notable.length
        ? table(
            [
              { label: "Seq", numeric: true, render: (decision) => decision.seq },
              { label: "Decision", render: (decision) => decisionTag(decision.decision) },
              { label: "Rule", render: (decision) => mono(decision.rule_id) },
              { label: "Subject", render: (decision) => decision.subject_kind ?? "—" },
              { label: "Reason", render: (decision) => clip(decision.reason, 120) },
            ],
            notable,
            { className: "compact" },
          )
        : h("p", { class: "muted small" }, "Every decision so far is PASS."),
    ],
    { aside, className: "span-2" },
  );
}
