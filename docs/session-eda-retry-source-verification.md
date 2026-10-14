# Session EDA, source quotation and retry verification

This record covers Session `session_922c5c1c50b84f30abddd2cfb87fd1ba` and its two Runs. Historical files were inspected through direct UTF-8/byte reads, never by constructing their LocalArtifactStore. No historical code or serialized model was executed, no provider was called, and no historical record was rewritten.

## Historical evidence

- EDA Run `run_14cca3024f2a4cdc81402620f122f7b6`: 1,434 verified events, completed; 61 registered artifact revisions verified by SHA-256 and byte count, including invalidated revisions.
- Training Run `run_3934603e46364a3babda1d2ff8fa4db5`: 131 verified events, failed in planning; 2 registered artifacts verified by SHA-256 and byte count. No training/tool execution occurred in this Run.
- Nine discussion records have 63 verified Session provider events, with exact Session/discussion ownership, sequence, previous hash and canonical digest checked.

The EDA had 21 `run_python` syntax rejections before execution. There were two exact generated-source variants: six and fifteen submissions respectively. Both used literal backslash-n sequences instead of physical line breaks. The next model request did contain the tool error; each request and gateway digest was distinct. This was repeated invalid generation, not provider replay. Those 21 originating responses account for 294,145 input tokens (including 67,840 cached input tokens) and 9,087 output tokens. Dollar cost is absent in the recorded evidence, so this record does not estimate money. These counts do not include every other request made by the EDA.

Separate recovered failures included a missing declared CSV after a nominally successful Python process, dependent read_file failures, and pandas correlation over categorical strings. Do not describe every tool failure as the same syntax defect, or claim only run_python failed.

The new EDA renderer was also exercised with the real registered profile through a read-only artifact adapter. It returned a 2,274-character report with the recorded 998 rows and 21 columns and three profile/source/schema references. Independent MIRA profile recomputation against the historical CSV/schema passed (197 numeric facts). This caught and fixed a serialization-order mismatch: profile object keys are canonicalized alphabetically while schema columns preserve CSV order.

## Actual terminal cause

In the training Run, the interview limit requested review at event 107 and received human approval at 109. Execution start requested another review at 112 and received human approval at 114. Neither review was pending when the Run closed.

The two planner requests at events 121 and 127 had the same gateway digest `cdb128aa3230bde4106202c20a39cdf9257d3033979599c2119805d579374ba9`. Responses 122 and 128 failed structured validation. Both proposed content matching for a MODEL artifact, which the runtime correctly rejects because content matching requires METRICS with nonempty metric keys. Runtime-owned provenance fields were also exposed in the planner schema. The second prompt contained no validation repair feedback. Event 130 closed with `Exceeded maximum output retries (1)` after an inline resume. This was a technical planning failure after resolved human reviews, not a terminal normative block.

## Source access and conversation scope

The heatmap question, followed by two confirmations, was misclassified as pending work clarification and its text entered the next training request. The chat had artifact metadata but no source-reading operation.

The new typed source quotation request selects a supplied artifact event, with an optional literal substring. Core verifies the approved source snapshot, Session/project scope, registered CODE identity, SHA-256, size and bounded text format before rendering numbered lines. A quotation is not model-authored and cannot create a Run. Work/evidence clarification scope is persisted; read-only clarification text is not appended to later work requests. Existing legacy records are not migrated or rewritten.

Read-only replay against the real evidence selected `r1e822` from the bounded final context and returned `eda_script.py`, digest `7eb8f1688e0d835a0163cfe930f456257e015e4420104ec47d33366b9e1470dd`, lines 14-42 of 42. The missing-value heatmap call is at line 18 and the numeric-only correlation heatmap call is at line 32. The replay used no provider and executed no source code.

## Implementation and validation

Structured retries preserve validation feedback and the existing request cap and usage charging. Field paths and error codes are sent without invalid values, custom exception messages or arbitrary mapping keys. The model schema offers exact-file and metrics-content variants and hides runtime-owned evidence/recovery properties; nonempty invented runtime values are rejected locally. Review requests carry the exact later human response as explanatory evidence without becoming authorization.

Focused offline regressions reproduced the identical structured retry, overbroad planner schema, unsupported source quotation, clarification contamination and missing review-resolution context before correction. Source-boundary tests cover changed bytes, invalidation, foreign Session, unapproved results and absent search text. Provider tests remain scripted; no external LLM call was made.

Repeated identical Python syntax failures now stop the delegation after its second exact-source preflight rejection, before another model request. The count is restored from verified conversation history across an intervening tool approval. Successful tool output containing syntax text is excluded. Different corrected source remains eligible for normal Tool Manager and Gate validation. The hint explains literal backslash-n characters without changing source bytes; this prevents the observed unbounded repetition but does not guarantee that a model produces valid code.

EDA summaries render verified profile shape, column types, missingness, explicitly unconfirmed target suggestions, task outcomes and artifact references. Coding success is attributed to the agent rather than asserted as independently verified execution. MIRA only uses recomputed profile values for a report that directly names that profile in its lineage; an unrelated report without evidence prevents an aggregate pass. This adds no narration-model call or producer-authored metrics sidecar.

Independent Sol review found and prompted fixes for partial A18 coverage, unsupported execution wording, canonical approval payload handling and a remaining runtime-owned planner field. Review also checked source quotation boundaries and retry/resume authorization.

The integrated Session/plan/report suite passed **283 tests**, covering source quotation, clarification
continuity, API consumers, prior-model evaluation without retraining, approval/reconstruction,
planning and independent profile/report audits. The separate `run_python` selection passed
**16 tests** (173 deselected). These selections do not overlap. NumPy warnings came from empty
and nonfinite profile fixtures; Pydantic Graph emitted one event-loop deprecation warning.
The separate agent integration suite passed **184 tests** after the final helper extraction,
covering provider adapters, structured retries, loop termination, delegation settlement, history
and approval/resume identity. The final total is **483 passed across three disjoint selections**;
earlier overlapping focused runs are not added. No external model or Docker test was performed.

Ruff and formatting passed for all 36 touched Python files; full-workspace ty reported zero
diagnostics. Import contracts: **4 kept, 0 broken** (403 files, 1,615 dependencies). Agent Notes
and the package/invariant inventory passed, as did `git diff --check`. This was a targeted
regression run, not the full repository `just check` suite.

Size checks retained the existing aggregate limits: `model_binding.py` decreased from 727 to
670 lines; `runner.py` ended at 1,183 lines, below its 1,200-line task baseline, with its class
at 517 lines (previously 518). Pure lifecycle projections now live in `lifecycle_evidence.py`.
MIRA report claim matching moved unchanged to `report_claims.py`, leaving `report.py` at
564 lines; the EDA renderer is 600 lines and unflagged. No unrelated oversized module was
refactored.

## Operational limits

Existing Runs remain closed and their original reports unchanged. The API and web processes inspected during this task were started before these edits; the running backend must be restarted to load the new Python code. No user-owned process was stopped. A new live Session/Run has not been validated against an external model, so offline checks do not establish universal conversational reliability. Source quotations support bounded registered Python, R, SQL and JavaScript text from approved results; they are not unrestricted filesystem access or notebook execution.
