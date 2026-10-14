# OpenAI real-provider run: plan review diagnosis

## Current status

The structural fix now has a completed real-provider continuation. Retry 2,
`run_18d9a9bb215b4a7d82d66aa2107983a6`, is COMPLETED with approved human review, 593 valid
events and no artifact-manifest errors. One planning model selection at 56 produced the exact
candidate approved manually at 67; no replanning followed approval. Data completes at 93 with
three verified profile outputs and no missing requirements or validation failures. Audit 582
passes A18 and all 14 evaluated deterministic controls; 16 controls are not applicable. All 24
model findings remain recorded (16 HIGH, 8 MEDIUM, support unknown) and the user approves the
exact findings review at 587 before terminal completion at 591–592. See
`real-structural-continuation-final.json`.

The first EDA remains COMPLETED with 693 valid events. The successful continuation is ordinal 4,
with context containing that EDA and both preserved failed attempts; this is not a fresh clean
two-turn run. The exact original second-turn model-only probe separately succeeds in one
request, with zero tools, approvals or transitions. Final broad offline validation passes
2,106 tests (two skips, 67 warnings, 284.56 seconds); 103 focused tests, scoped Ruff/format,
typecheck and all four import contracts pass. Global `just check` is not claimed because three
unrelated existing TEMP-DEBUG Ruff errors remain. The paragraphs below retain intermediate
states as historical evidence.

The latest first EDA Run `run_c0dd948eb5b74380993cc014cf8c51b6` is COMPLETED: 693 valid
events, no manifest errors, strict-JSON profile and A18 PASSED. Its exact linked follow-up
`run_7781e29e9fd3456dbe1d8ddd63ead41e` is now FAILED after 72 events, following the user's
execution-start approval. Both planning attempts attributed invented output paths to a generic
current request rather than a concrete task-derived publication promise. The validator rejected
them before tool execution. The bounded repair feedback did not yet provide enough operational
guidance to correct that proposal. The existing Run histories are preserved.

A diagnostic `--followup-attempt N --resume-root PATH` can reuse the completed EDA and create
a stable new retry identity through the normal Session API. Attempt 1 is a third conversation
turn after the failed second turn, not a clean two-turn success. The API selects context itself:
the runner verifies the completed first Run is retained and every other selected Run is a
verified earlier attempt in the same Session. It records actual context IDs and ordinals and
requires all previous attempts to be terminal. Existing selected attempts resume idempotently;
no approval is prefilled and no terminal Run is reopened. A successful real continuation remains
pending structural-contract validation and ordinary exact runtime approvals.

The planning-guidance focused suite passes 161 tests. Offline runner recovery reuses the
existing Session and completed EDA, rejects changed immutable work, and makes zero network
calls or Run-history changes. CLI invalid-attempt guards, help and compilation pass. A new
retry has not yet demonstrated successful real completion.

Real retry 1 (`run_50afe64853d7444facd1fe80ad77cf5a`) also failed before tool execution after
the user approved its start (`decision_8c01f91836394728aabc5a23736ccd87`, event 116). Its
136-event chain is valid and artifact verification has no errors. Proposals 125 and 131 used
the correct producer filenames but still omitted their paths from the quoted task instruction;
proposal 131 added escaped quote characters. The effective provider request contained the new
field descriptions and repair guidance. This demonstrates that prompt guidance alone has not
resolved the real-provider contract failure. A structural remedy now exposes contextual
`outputs_to_publish`, compiles them into canonical requirements and an explicit publication
instruction before plan approval, and removes the self-quote contract. Nonempty contextual
legacy requirements are rejected rather than dropped. Standalone and already-approved plans
keep their existing contracts. Focused offline validation passes 103 tests. The real OpenAI
model-only probe succeeds in one request with a valid candidate/hash and the three actual
producer paths. It preserves the exact original second-turn conversation (ordinal 2,
context containing only the completed first Run) and uses the production default THY catalog.
It records zero tools, approvals and Run transitions; see `real-structural-plan-probe.json`.
This is planning evidence, not execution, audit or a clean two-turn success. Normal retry 2,
`run_18d9a9bb215b4a7d82d66aa2107983a6`, is a fourth conversation turn and waits for exact
execution-start review `decision_8b1081239944489187fb42f2bfec6fc5`; its approval is pending.
Final broad offline validation remains in progress. No completed
real follow-up is claimed. Broad offline validation passes 2,101 tests (two Windows symbolic-link
skips, 67 warnings); 61 MIRA runner tests pass separately.

The following paragraphs retain earlier real-run observations and intermediate review states.

The OpenAI/LiteLLM connection works. The later isolated Run
`run_758b895f368d43b3ac38b705c40b4181` completed its EDA task and produced a strict-JSON profile
and two valid PNG artifacts. Its canonical event chain and terminal governance evidence validate,
but the Run is terminally BLOCKED and no follow-up was submitted. See
`real-eda-audit-block-evidence.json` and `real-plan-resume-evidence.json` in this directory.

Two independent defects were then identified. A18 recomputes undefined correlations as NaN but
rejects the profile's JSON null representation; A18 is the only CRITICAL finding. Separately,
Core combines the profile tool with MIRA's regulation lookups when checking whether the executed
scope is bounded local profiling. Correcting audit-read attribution alone cannot authorize PASS
or erase the independent A18 finding. The recorded terminal Run remains unchanged. A successful
fresh real-provider continuation has not yet been demonstrated.

After the narrow A18 correction, read-only reverification of this same recorded profile passes
A18. The event chain remains valid and artifact verification reports no errors both before and
after; `real-profile-reverification.json` records the comparison. This does not modify the
terminal BLOCKED Run or demonstrate a new completed Run. Audit-read attribution now has a
separate implementation: Core uses the isolated MIRA registry and paired authenticated call
and agent evidence, retains every completed-tool hash, and still requires a safe profile.
Read-only snapshot reverification confirms all 25 completed calls are safe and retained in the
binding (one profile and 24 regulation lookups), with no training or experiment event. This
does not exclude the auditors' ordinary OpenAI model calls. See
`real-audit-action-reverification.json`. The focused scope/composition/session suite passes
47 tests; scoped quality checks and all four import contracts pass. Final post-scope THY/Core/policy/API/lifecycle validation passes: 970 passed, 2 Windows symbolic-link skips, 4 warnings in 174.00 seconds. MIRA coverage also passes in the prior expanded run (926 MIRA tests in the parallel suite plus 61 runner tests in a separate serial invocation); the serial split avoids pre-existing randomly generated collection IDs in test_mira_runner.py. Scoped Ruff/format, zero-baseline typecheck, four import contracts and Agent Notes pass.

The fresh isolated scenario is C:/Users/carlo/AppData/Local/Temp/thymira-historical-followup-13xfoic0, Run run_c0dd948eb5b74380993cc014cf8c51b6. The user approved the uncertain risk review decision_78f1a9cc64a949c4a0bb1713d257091c; an authenticated nonautomatic approval is recorded at 173. Classification at 184 retains profile v14, high risk, confidence 0.90 and no missing information, without further questions. The execution-start decision_6db8f4aaa85242ffb4d6df0c59f54eca was explicitly approved by the user and recorded at 188. OpenAI proposed a single data task calling profile_dataset(include_plots=true) for german_credit. The user approved plan decision_942d6b698a8347688a996b41a7475100 at 203. The exact candidate resumed without replanning (one planning model selection, at 192). The data task completed at 229 with no missing artifacts or validation failures, publishing the profile JSON and both PNGs at 219–221. Audit 682 is passed_with_warnings, A18 PASSED, with no deterministic failures and no CRITICAL finding. Its 24 model proposals (16 HIGH, 8 MEDIUM) all have supported=null. The recorded review scope is bounded local descriptive profiling and retains all completed-tool hashes. The user approved findings decision_9903201ea1214cea8b25cf2e603c1834. The first Run completed with approved human review (693 valid events; manifest verification has no errors and the profile is strict JSON). The exact follow-up message “¿puedes hacer algo más con el dataset?” created run_7781e29e9fd3456dbe1d8ddd63ead41e in the same Session, with context_run_ids=[run_c0dd948eb5b74380993cc014cf8c51b6]. At the earlier recorded checkpoint it had no artifacts or tool executions and waited at 51 for execution-start decision_daee9c90d2bc4af092039f7ca75b10ea, with local_execution_only=true. Human authorization for that descriptive continuation was subsequently supplied. See real-two-turn-progress.json. See real-audit-retry-eda-execution.json, real-audit-retry-findings-review.json and real-eda-findings-review.md. No approval was copied from the old Run. The first EDA is COMPLETED and verified. The second turn subsequently failed at planning, as described in the current status above; it never executed a tool.

The sections below preserve earlier diagnoses and intermediate states as historical evidence;
their pending-plan and interview statuses are not the current state of this later Run.

## Finding

OpenAI successfully returned a structured plan through LiteLLM. The observed failure is in the runtime's plan-review lifecycle, after the provider response and before any EDA tool execution. The default policy requests human review for plan.proposed, but THY represents this pending review as an execution error. Core then persists a terminal FAILED Run.

## Recorded sequence

Run: run_9ccf751e4b004f55b8d03dd145d80a58. Isolated workspace: C:/Users/carlo/AppData/Local/Temp/thymira-historical-followup-etlkcm1t.

- 274: the exact execution.start review is approved by a human.
- 275: Run transitions from waiting to active, resuming execution_start.
- 278: gpt-4o-mini selected for THY planning.
- 282: request recorded through the LiteLLM gateway.
- 283: model response terminal outcome is success; no provider error code. The response contains a Data task calling profile_dataset(include_plots=true).
- 284: policy returns REQUIRE_HUMAN_REVIEW, rule default, for the new plan review.
- 285: human.approval_requested with summary plan.proposed is recorded.
- 288: Run transitions directly from active/planning to terminal/failed.
- 289: run.failed records THY halted before completion: plan rejected: Action not covered by the policy: when in doubt, ask a human.

The EDA was never executed, and the follow-up message was not submitted. An earlier runner deadline expired while waiting for the first human review; the final terminal failure above occurred after that review was approved and the same Run was resumed.

## Code path

- runtime/policies/src/thymira/policies/defaults/base.yaml:38 sets the unknown-action fallback to REQUIRE_HUMAN_REVIEW. Neither base nor the credit-risk overlay supplies a plan.proposed action rule. This safe fallback is working as configured.
- runtime/thy/src/thymira/thy/nodes/plan.py:310 treats all decisions that do not allow execution alike. REQUIRE_HUMAN_REVIEW sets error=plan rejected, just like a refusal, and does not preserve the proposed plan in state.plan.
- runtime/core/src/thymira/core/graph/adapters.py:490 raises ThyExecutionError for every non-null THY output.error, before returning resumable progress.
- runtime/core/src/thymira/core/dispatch.py:164 wraps the exception as ExecutionDispatchError; the Run service records terminal failure. The dispatcher is correctly recording a reported error; the pending-review classification was already lost.
- runtime/core/src/thymira/core/human_review.py:69 accepts approvals only when the Run is waiting for approval. A terminal Run cannot receive a late approval.
- runtime/core/src/thymira/core/run_resume.py:51 does not resume terminal Runs.

This rejection branch is present in the source snapshot captured before the historical-artifact fix. The defect is an incomplete integration of resumable plan review, independent of the new artifact-origin contract. It can affect any task whose plan uses the default policy fallback.

## Offline confirmation

Executed without network, using existing ScriptedProvider tests:

    uv run pytest tests/thymira/test_thy_plan.py::test_a_synchronous_approval_never_advances_a_review_gated_plan tests/thymira/test_core_graph_adapters.py::test_thy_subgraph_raises_when_thy_reports_an_error tests/thymira/test_core_graph_adapters.py::test_production_factory_thy_failure_leaves_a_consistent_failed_run -q --basetemp=.tmp/pytest-openai-plan-review-diagnosis

Result: 3 passed, 1 deprecation warning, 7.73 seconds. These tests pass because they explicitly assert the current rejection/error/FAILED behavior. They do not demonstrate a working human approval continuation for plan.proposed. Many successful execution fixtures use an explicit test-only plan PASS rule, so they do not exercise this gap.

## Required repair

Distinguish pending plan review from a rejected or invalid plan. Preserve the exact validated candidate plan and its decision binding before returning a waiting result. Core must use RunController to persist waiting/approval, rather than interpreting that result as a failure. The normal approval endpoint must resume the same approved candidate without regenerating it or treating approval of execution.start as approval of the plan. Changed plans or stale decisions must require a new review.

Keep BLOCK/invalid plans terminal and keep tool permissions, artifact completion and THY/MIRA layering intact. Add an offline API lifecycle regression with default policies that observes WAITING, approves the exact pending plan decision, resumes without an extra planning request, and then executes. Afterwards rerun the real two-turn scenario in a fresh workspace. A universal plan PASS rule would conceal the missing lifecycle and is not the proposed repair.

No production source or policy was changed during this diagnosis, and no additional OpenAI calls were made.

## New regression before production repair

`tests/thymira/test_core_plan_approval_integration.py::test_default_policy_parks_plan_review_after_execution_start` uses ScriptedProvider, the real API composition and local stores. The authenticated approval endpoint approves execution.start, then the next plan request is subject to the policy fallback. Before the repair, the assertion expected WAITING_FOR_APPROVAL but observed FAILED with `THY halted before completion: plan rejected: Action not covered by the policy: when in doubt, ask a human.` Result: 1 failed in 10.41 seconds. Full local output: `.tmp/plan-review-red.txt`. This is the target regression, rather than a failure caused by a malformed fixture.

## Separate clarification issue observed during the fresh check

Fresh isolated workspace: `C:/Users/carlo/AppData/Local/Temp/thymira-historical-followup-tsntmx1o`, Run `run_758b895f368d43b3ac38b705c40b4181`. The exact uncertain assessment for profile v13 was manually approved at event 139 and resumed at 140. No repeated MIRA assessment for v13 followed. The separate execution risk classifier at 150 returned high risk, confidence 0.95, no missing information, and human review required because of sensitive attributes. `classify_activity_risk` treated any review requirement as a reason for fact clarification. It asked about an already declared affected population at 159, repeated the question after identical answers, then published profile v14 at 210. The new MIRA assessment at 221 and approval request at 225 correctly belong to v14; the previous approval cannot authorize that new assessment.

The defect is the unnecessary clarification that created v14, rather than a lost approval or a provider failure. Keep human review required for sensitive work, but avoid fact questions when the execution classification is known and has no missing information. Preserve fail-closed clarification/review for missing facts or unknown classification. The manual runner was stopped with this Run still WAITING/APPROVAL at event 226; no synthetic approval or policy change was used.

The first narrow guard was reproduced red and verified green for known classifications with an empty missing-information list. Continuing the real scenario exposed the remaining case: event 238 claimed affected_population, decision_effect and intended_use were missing despite declarations already present. Event 247 asked about decision_effect, whose answer at 257 repeated the existing research-only scope and created v15. MIRA correctly requested a new exact v15 review at 273 (`decision_78e47ab047f5414ba3e74defdf90b392`); the runner was stopped with WAITING/APPROVAL at 274. The final clarification predicate must compare claimed gaps against recorded local facts, while retaining risk review and unknown/fallback handling.

## Implemented repairs after the diagnostic phase

THY now returns a pending plan review with the exact normalized candidate instead of an error. Gate records a single durable candidate snapshot in the existing approval request. Core parks in PLANNING through RunController and resumes the same candidate only after an authenticated, nonautomatic approval for its bound decision. Request, candidate, immutable Run work, execution constraints and policy bindings are validated; restart recovery uses durable events when the checkpoint lags. The production owner consumer checks current Run work before RESUME, and current policy is checked at adapter entry before tasks execute. Selected agent/prompt/schema/tool/skill availability is checked, without claiming same-name file byte attestation.

Core also limits known-classification questions to actually absent profile fields. The helper normalizes only intended_use/purpose, decision_role/decision_effect and deployment_context/autonomy, reuses has_declared_value, and restricts the question selector to genuine gaps. RiskProfile, its reported concerns, needs_human_review, unknown/fallback handling and all policy Gates remain intact. Offline validation observed 93 focused plan/binding/lifecycle tests and all 34 risk-interview tests passing. The final broad run is recorded separately in the investigation validation update.

The v15 uncertainty assessment was explicitly approved by the user and recorded at event 275. After resume at 276, event 286 classifies the unchanged v15 as high risk with no missing facts and needs_human_review=true; no new question or profile version is created. The current real scenario is WAITING/APPROVAL for execution.start at event 289, pending `decision_55da8c8e69d04b50a7f2e41e4ce59eb5`, with local_execution_only=true. The user has been asked to approve that exact start decision. No EDA task has executed and no follow-up has been submitted yet. The same isolated root remains recoverable. OpenAI transmission remains authorized; that consent does not replace the runtime’s exact risk, start, plan or tool approvals.

Final offline suite: 969 passed, 2 Windows symbolic-link skips, 4 deprecation warnings in 179.10 seconds. Typecheck ratchet 0/0, four import contracts kept, scoped Ruff/format and Agent Notes valid. See investigation.md for command scope and the pending real acceptance criterion.

## Real plan review reached after start approval

On 2026-10-13, the user explicitly approved `decision_55da8c8e69d04b50a7f2e41e4ce59eb5`. The same Run records that human answer at 290 and resumes execution_start at 291. OpenAI returns the proposed plan at 299: one data task calling profile_dataset(include_plots=true) for german_credit, with no user-named file requirements. The default plan policy requests review at 300/301. The normalized candidate and immutable scope are durably recorded; scope retains local_execution_only=true. At 304, RunController parks WAITING/APPROVAL in PLANNING with resume_from=plan instead of terminal FAILED. No EDA agent has started.

The current pending plan decision is `decision_320951458d7649ba9b1c064eeef6e739`; the user has been asked to approve this exact candidate. Bounded proof: real-plan-review-evidence.json. This confirms the real pause half of the lifecycle repair; exact-candidate resume and the two-turn scenario remain pending this ordinary approval.

## Real EDA executed; audit scope defect (2026-10-13)

The user approved the exact plan decision `decision_320951458d7649ba9b1c064eeef6e739`. Human approval is recorded at event 305; the same candidate resumes at 306 without a second planning model call. The data agent executes profile_dataset at 320–324 and completes at 331 with no missing required artifacts, unresolved tool calls or validation failures. The JSON profile and both deterministic PNGs verify against the artifact manifest; the profile parses as strict JSON.

The Run subsequently becomes terminal BLOCKED at 850. The policy reason is: "A critical finding remains uncertain or its verification metadata is incompatible; its exact descriptive scope is not complete enough for human review: executed capabilities do not establish bounded local profiling." The audit snapshot includes the successful profile call and MIRA regulation-search tool calls. Core currently evaluates all these calls as if they were executed task capabilities; search_regulation cannot satisfy the profile-only capability condition. The terminal event chain and evidence remain valid. This is a separate audit scope defect under investigation, not a missing-artifact failure or an OpenAI connectivity failure.

The terminal Run is preserved and cannot be resumed through normal lifecycle transitions. The follow-up has not been submitted, so the two-turn real acceptance criterion remains open. Bounded artifact and completion evidence is in real-eda-audit-block-evidence.json; exact-plan resume evidence is in real-plan-resume-evidence.json.

Further report inspection: A18 is the only CRITICAL finding, and it is deterministic, without a model applicability assessment. It reports an undefined correlation recomputed as NaN against the correctly serialized JSON null. The independent source verifier must compare the defined JSON representation of an undefined correlation; a genuine finite correlation mismatch must continue to fail. Fixing audit-tool attribution alone cannot authorize or clear this deterministic critical finding. Both defects need separate regressions; the policy must continue to block real source discrepancies.

## Structural continuation plan review

The user approved retry 2 start decision `decision_8b1081239944489187fb42f2bfec6fc5`.
The authenticated API records that nonautomatic approval at event 52. OpenAI then proposed
a single Data task to call `profile_dataset(include_plots=true)` and publish the profile JSON,
distributions PNG and correlation PNG at the actual producer paths. The compiled obligations
are explicit in the instruction before Gate review. Candidate SHA256
`ba9fda917c8e3b495d0eb37f851f4ed7af7b4626922d127f4bf763824253ebf5` and scope SHA256
`ffe8b5b99881d801bc6970c827757779a6ad5528f571b1e7ad072d20272d508d` verify against the
recorded request. The 67-event chain validates and no tool has executed. Exact plan approval
`decision_a847c078ff174dc8bd40a3e92bfcdb91` is pending. See
`real-structural-continuation-plan-review.md` and its JSON evidence.
