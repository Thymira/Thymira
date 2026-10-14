# Human context and continuity

## Problem

Work published through Session could lose facts or identity needed by the next consumer after
reopening. Short clarification replies risked replacing the request, an ambiguous model follow-up
could default to training, and an explanation of a blocked Run did not preserve the exact
verification disposition that caused the Gate decision. Several old end-to-end assertions also
expected internal JSON keys or a governance canary instead of the human-readable report and
evidence that the real producer publishes.

## Decision

Keep continuity tied to current human work and persisted, provenance-bearing evidence. Explain a
finding from the audit snapshot bound to the closing decision, retain exact supported/refuted/
unavailable records, and never infer a supported verdict from a missing record. Project bounded
operational Run and Task identities to the reopened provider without exposing provisional output.
Only explicit current human work authorizes new model work; ambiguous evaluation requests require
the approved source Run and model reference. Adapt report assertions only after exercising the
real profile producer and its downstream consumer.

| Family | Status | Cause and effective consumer evidence |
| --- | --- | --- |
| Verification explanation and provenance | repaired gap | The closing explanation previously omitted exact verification records. `runtime/mira/src/thymira/mira/verification.py` and `flow.py` persist supported verdicts alongside dropped and unavailable records; `runtime/core/src/thymira/core/conversation_diagnostics.py` and `session_chat_context.py` use the bound audit and keep the three dispositions distinct. The reopened Ask provider receives exact IDs and reasons. See `test_conversation_diagnostics.py::test_blocked_diagnostic_preserves_unavailable_finding_verdict_reason` and `test_session_chat_evidence_continuity.py::test_reopened_ask_receives_mira_verification_outcomes_without_releasing_or_dispatching`. |
| Failed and corrected Run/Task identity | repaired gap | A bounded history slice could cut off the newest turn's identity, and Ask had no allowlisted Task status projection. `runtime/core/src/thymira/core/session_chat_context.py` and `conversations.py` now preserve cited Run/Task IDs and recognized operational statuses in the reopened provider request while keeping provisional summaries and outputs hidden. See `test_session_chat_evidence_continuity.py::test_reopened_ask_receives_failed_and_corrected_task_identities_only` and `test_conversation_e2e.py::test_reopened_planner_history_keeps_latest_run_identity_within_budget`. |
| First clarification and Ask routing | current correction confirmed | A first clarification is valid with no parent even when the model proposes continuation. The existing Session boundary controls retain the pending question without creating a work Run; explicit Ask cannot be escalated to Action by a model. See `tests/thymira/test_session_clarifications.py::test_first_question_does_not_require_a_parent_when_model_sets_continuation` and `tests/thymira/test_session_discussions.py::test_explicit_question_cannot_be_escalated_to_action_by_model`. The real post-EDA Ask/reopen consumer is covered by `test_conversation_e2e.py::test_session_upload_reaches_real_eda_tool_and_survives_reconstruction`. |
| Clarification and explicit replacement | current correction confirmed | Short replies preserve the pending human request and its `sin entrenar modelos` restriction; a separate explicit action can replace the dataset and reaches the reopened classifier with only the replacement scope. See `test_session_clarifications.py::test_natural_reply_keeps_pending_request_when_model_omits_continuation` and `test_explicit_dataset_replacement_reaches_classifier_after_reopen`. |
| EDA report, Ask, and artifact lineage | current correction confirmed | The old literal `row_count` assertion described an internal payload rather than the released report. A real uploaded CSV is profiled, its readable facts and source names reach the reopened planner, and read-only Ask cites the originating Run, persists its discussion, and creates no work Run. The no-tool JSON control still refuses to promote payload fields to verified facts. The raw dataset digest is checked in producer/persisted lineage and is not added to prose. See `test_conversation_e2e.py::test_session_upload_reaches_real_eda_tool_and_survives_reconstruction` and `test_no_tool_structured_payload_is_not_a_verified_profile_after_restart`. |
| Governance visibility and pending review | current correction confirmed | The old canary assertion did not represent authoritative profile evidence. `tests/thymira/test_conversation_e2e.py` sends one scoped synthetic INTERNAL candidate through the real MIRA verifier and Gate; the retained finding, supported record, final decision and audit binding are checked together. BLOCK, WARNING, rejected review and approved review exercise visibility and follow-up; WARNING completes and releases the report without a finding-review approval. The normal no-finding EDA case also exercises its default WARNING decision. Product Gate policy is unchanged. See the full module, including `test_final_governance_controls_response_visibility_and_followup_context`. |
| Plot follow-up and model scope | repaired gap | A graph continuation could inherit an Experiment despite the current human request asking only for plots. `runtime/thy/src/thymira/thy/nodes/plan_scope.py` now removes that unrequested task while `plan.py` preserves the required plot artifact; missing `plots/prior-analysis.png` closes the task and Run as failed even when the tool returns successfully. An ambiguous seed change remains blocked, explicit training remains available, and evaluation without a concrete approved source asks for a reference rather than training. See `test_thy_plan.py::test_graph_follow_up_cannot_add_model_work_to_a_prior_eda_scope`, `test_model_follow_up_without_new_scope_terms_does_not_authorize_training`, `test_explicit_model_follow_up_with_new_seed_keeps_the_planned_experiment`, and `test_previous_model_metrics_without_a_registered_reference_never_train`. |
| Real plot publication and correction | current correction confirmed | The graph-scope regression above proves required-artifact failure behavior, not publication. The existing THY execution control proves that a failed published plot gets one approved correction and resumes through the real artifact path: `tests/thymira/test_thy_execute.py::test_only_published_plot_failure_gets_a_correction_after_approval_resumes`. |
| Approved-model metric continuation | current correction confirmed | After reopening, the actual planner receives the approved model artifact ID, digest, source Run and requested metrics; the real evaluator uses that identity and records no new `MODEL_TRAINED` event. See `test_model_evaluation_continuation.py::test_prior_model_evaluation_survives_approval_and_reconstruction`. |
| Negative consequences and classification | current correction confirmed | A persisted profile answer stating there are no consequences remains in the next real classifier request and does not impose a mitigation requirement. See `test_core_risk_interview.py::test_generated_mitigation_question_does_not_expand_the_consequence_fact`. |
| Human review correlation | repaired gap | Resolution correlation previously omitted the request `subject_id`, and selecting only the two newest requests could drop the relevant older request. `runtime/core/src/thymira/core/session_review_evidence.py` now requires exact request/response subject identity; `session_chat_context.py` ranks relevant review requests before the bounded selection; a different human reviewer remains valid. See `test_session_chat_evidence_continuity.py::test_review_request_evidence_includes_its_exact_later_human_resolution` and `test_question_selects_a_relevant_older_review_request`. |

Retrospective inspection of preserved pre-repair evidence (not a pytest run):
`.tmp-human-context-implementation/tri-state-red/test_reopened_ask_receives_mir0/runtime/runs/run_4f36f3dba5ca46389aab62728dfb22a9/events.jsonl`
contains `audit.completed` at seq 16 with unavailable finding
`finding_dd8537f95f2c494c98133ebf5c71fcb1`, title “Candidate requires review”, body “A recorded
candidate has cited evidence that could not be fully judged.”, and reason “The verifier could not
make a grounded ruling from the available evidence.” The same event has a separate dropped finding
`finding_3286e57f2b48428993594d0a8db5612d` with reason “The cited record contradicted this
candidate.” Both `verification_supported` and `verification_supported_findings` were absent. The
preserved request at
`.tmp-human-context-implementation/tri-state-red/test_reopened_ask_receives_mir0/runtime/discussions/model-evidence/turn_b1daeb3c1e974add868d775dcefdd182.json`
showed only the generic unavailable title/body excerpt in `diagnostic.detail_excerpts`; it omitted
the finding ID, unavailable rationale and `audit_finding_verification`. Root independently verified
this trace in `.tmp-human-context-review/tri-state-preserved-request-root-read.json`. This is
retrospective evidence, not an additional test result, and does not imply that the old trace
recorded a positive supported verdict.

## Alternatives considered

Do not infer `supported=true` from the absence of an unavailable record, use a missing audit as
proof that a claim was confirmed, or treat a conservative BLOCK as confirmation. Do not copy raw
task summaries, tool output or dataset hashes into prompts; those expand context without creating
authority. Do not add a second conversation-memory store, an unbounded history list, a generic
intent classifier, or synthetic `row_count`/governance-canary prompt fields. Keep the exact audit
binding and existing Gate policy as the authority.

## Consequences

The Session consumer receives complete bounded records with Run/event citations and exact finding
IDs. Legacy/pass-through retained findings without a recorded verifier decision remain explicitly
“verdict not recorded”; missing or malformed evidence never becomes a positive verdict. The graph
scope seam proves that a missing required plot fails the Run; actual plot publication and repair
remain covered by the existing real repair control, not this synthetic scope seam. The governance
candidate is a narrow test fixture, not a claim that the EDA case runs the full MIRA model fan-out.
Planner prose uses the dataset name, relevant facts and released Run link; raw dataset SHA stays in
verified lineage. The historical review projection remains bounded and does not imply access to
older evidence outside the selected context.

The four governance outcomes, including the real-candidate WARNING case, passed in 80.20 seconds;
the full focused v3 selection passed 142 tests in 314.23 seconds with one Pydantic event-loop
deprecation warning. Logs and JUnit are
`.tmp-human-context-implementation/evidence/governance-4-20261004-v2.txt`,
`governance-4-20261004-v2.xml`, `final-focused-20261004-v3.txt`, and
`final-focused-20261004-v3.xml`. Ruff check/format passed on 21 files, the type ratchet reports
zero diagnostics (baseline zero), and the note validator passed; see `ruff-final-20261004-v3.txt`,
`ty-ratchet-20261004-v3.txt`, and `notes-check-20261004-v3.txt` in the same evidence directory.

### Independent review and source stability

Root completed independent review with the sole implementer inactive. The first review required restoring the explicit WARNING-None governance variant; that correction returned to the same Luna implementer. Root reviewed the final change after the implementer completed again. The production hashes were unchanged between reviews, and final source/test inputs exactly matched the successful source gates.

Existing ten production files changed, one narrow audit-explanation helper added, and 477 other initially captured files remained byte-for-byte unchanged. The eleven captured original diagnostic/evidence files were also unchanged. No policies, import boundaries, dependencies, snapshot, service process, historical Run or global lint/type exclusion was modified.

The full serial run used a frozen manifest of 926 source and static test-input files. Its aggregate
SHA-256 is `50bd4c2042cbcd1727cc5e8dfd4cbbaca83e32b34277ec0d64d74dfeff299b3b`. The post-suite comparison found zero changed or missing
files and the identical key set. Runtime caches, temporary outputs and the closure documentation
are outside that manifest. See `.tmp-human-context-review/independent-review-final.json`,
`final-source-hashes.json`, and `source-freeze-verification.json`.

### Final validation

The single full serial execution collected 4,342 cases and finished with **4,263 passed, 1 failed,
78 skipped and 61 warnings in 1,754.31 seconds**. No production or test file changed during it.
The only failure is
`tests/thymira/test_acceptance_demo_run.py::test_demo_run_records_what_the_model_saw_and_what_it_left_behind`
at the unchanged `tests/thymira/acceptance/experiment.system-prompt.expected.md` comparison
(line 408 of the test). The runtime denial still leaves the Experiment FAILED, with no MODEL or
METRICS artifact; the assertion fails on the existing prompt snapshot drift. Work09 owns that
contract review. No snapshot was refreshed. Logs and JUnit:
`.tmp-human-context-review/root-final-all.log`, `root-final-all.xml`, and
`full-suite-summary.json`.

The complete E2E module passed all eight cases, including all four governance variants. The new
audit-projection module passed nine cases, Core conversations nine, conversation diagnostics ten,
Session evidence continuity twelve, clarifications twenty-three, discussions twenty-seven, human
review evidence seven, the real model-evaluation continuation one, THY planning sixty-seven and
THY execution thirty-one. These are subsets of the full run, not additional totals. The focused
v3 selection contains existing controls and new regressions; its 142 cases are not added to the
full-run count. Existing EDA/governance controls were adapted only after real producer/consumer
proof; the no-tool profile control and ambiguous-model-follow-up control were renamed to express
their supported negative behavior. New regressions cover exact verification records, cross-subject
review rejection, older relevant reviews, task/Run identity, oversized history, explicit dataset
replacement, graph scope, missing model references and explicit-training recovery. The original
test-function comparison is in `.tmp-human-context-review/test-function-delta.json`.

Root source Ruff passed; Ruff format reported 748 files already formatted; source YAML passed; ty ratchet reported 0 diagnostics with baseline 0; imports reported Contracts: 4 kept, 0 broken. Package/invariant inventory, 16 skills, roadmap agreement, Agent Notes, offline lock check (251 packages), and git diff --check passed. The global god-class report remains informational (39 modules and 39 classes flagged); changed production adds no new flag category. MiraAuditFlow retains its pre-existing size/cohesion flags.

Root gates are recorded under `.tmp-human-context-review/final-source-ruff.log`,
`final-source-format.log`, `final-source-yaml.log`, `final-types.log`, `final-imports.log`,
`final-package-invariants.log`, `final-skills.log`, `final-roadmap.log`, `final-notes.log`,
`final-lock.log`, `final-diff-check.log`, `final-god-classes.json`, and
`final-module-shape-comparison.json`. The final code/test inputs exactly match
`gate-source-hashes.json`; these checks remain valid for the frozen suite source.

The literal global gate remains **open**. Before full pytest, `just check` stopped on 156
pre-existing temporary-file Ruff diagnostics; global format reported 42 previous fixture files.
After the full run, it stops on 493 Ruff diagnostics: the same 156 previous diagnostics plus 337
in newly generated work08 fixtures. Global formatting reports 79 files requiring formatting,
including temporary outputs. Global YAML reports 6,650 errors and 142 warnings: 3,263 errors and
48 warnings in pre-existing/other fixtures, plus 3,387 errors and 94 warnings in work08 fixtures.
Source YAML remains clean. This does not relabel new fixture failures as historical or present a
clean whole-workspace gate. No evidence was removed and no global exclusion was introduced.
See `post-suite-just-check.log`, `post-suite-global-format.log`,
`post-suite-global-yaml.log`, and `post-suite-global-gates-summary.json`.

### Orchestration limits

The selector selected gpt-6-luna with xhigh reasoning for the sole implementer. Fast was unavailable in the exposed selector. The environment did not expose an independent selector for verifying or changing the principal model/reasoning, so the principal configuration is not claimed as verified.

Only work08 is closed at the demonstrated boundaries. Work09 and the literal whole-workspace
gates remain open; this note does not close the full stabilization or foundation program.
