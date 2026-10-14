# Session model continuity verification — 2026-09-30

This report separates historical inspection, offline scripted conversations and real local
synthetic execution. No external model call was made. Existing history was not rewritten,
and no historical serialized model was loaded.

## Historical findings

Session: session_eaf7228b267145c6969b650304a246ac (“Realicemos un EDA básico”).

| Run | Request | Verified events | Outcome |
| --- | --- | ---: | --- |
| run_65b90cc6c9e44feaa0769075a650d3e5 | Realicemos un EDA básico | 789 | completed |
| run_b866f757a4e549ef9daacef1b81f690e | haz un modelo basico tipo logistic regresion | 650 | completed |
| run_dd78b235995e4bb6a0b5a49104baa35f | Sacame mas metricas del modelo | 208 | failed |

All three event chains verified. Direct UTF-8 manifest reads and SHA-256/size checks covered
25 artifact revisions, including archived entries, without discrepancies. All nine Session
provider-evidence logs verified scope, sequence and hash chain. These checks did not construct
LocalArtifactStore on the historical directories.

The second Run registered logistic_regression_model.pkl as
artifact_49ffe12f3af640a2828dde4833c15fd5, SHA-256
633e232a2f5535b2cbf249652b2d1c8fdb9103917cf8e2aeb049ed7835cdb03e.
Its training evidence records seed 7, test fraction 0.25, shuffle=true, random_state=7,
stratify=null, library versions, estimator arguments and the training script hash.

The model-location discussion is turn_f4cdb729e79b47c6aeb39233cffafbf1.
Neither actual provider request contained the model filename. Only the last two created artifacts
had been selected. An artifact ID was then used as a citation ID, including during repair.

The third Run's actual requests at seq 138, 149, 169 and 193 contained neither the prior model
filename nor its ID. It retrained. The first call at 163 failed at 164 because destinations
collided; the retry at 182 proceeded. The replacement binary at 184 and the original had equal
bytes but different identities. The new METRICS artifact contained accuracy only.
Terminal event 207 cites missing exact name model_metrics.json. Accepting
metrics/model_metrics.json would still leave precision, recall, F1 and AUC absent.
Earlier human reviews were not the terminal normative blocker.

The explanation discussion is turn_6b40ae846d67407aab10781db90a3715.
The failure source r0e207 was visible in both requests; r0e208 was not visible but appeared in
the repaired response. “Explain the recorded terminal outcome…” was the user-side question
prepared by the diagnostic button, not an assistant answer.

## Responsibilities and changes

- **Core** freezes approved prior model references and owns Session/project/snapshot scope,
  approval checks, direct historical reads and idempotent publication of verified input copies.
- **SessionChat** owns bounded question-relevant evidence, work-kind proposals and citations
  against complete records in the exact final frame. Ask remains read-only.
- **THY** owns evaluation planning and actual deliverable completion. Its dedicated evaluator
  has only evaluate_model. A model-authored train_new cannot itself authorize replacing a prior
  model; conservative English/Spanish human training intent checks reject ambiguous wording.
- **Tools** own inference, numerical metrics, split reconstruction, sandbox execution and early
  destination validation. run_experiment still supports versioned output revisions. It rejects
  incompatible binary extensions and collisions within a call, not legitimate destination reuse.
- **The artifact store** retains versioning and lineage. Copies use local input IDs; original
  source identities are separately recorded. No foreign IDs enter local lineage.
- **The web client** renders safe Markdown using DOM nodes and clarifies that the diagnostic
  button prepares a question. It does not own execution or artifact authority.

Exact paths remain exact when the human requires them. Content-mode METRICS completion checks
registered bytes, finite requested values, source Run/model identity, model/data hashes and split.
Unsupported additional deliverables or ambiguous comparisons produce a plan diagnostic instead
of silently dropping work. There is no basename fallback.

Training summaries lead with model access, recorded preparation, split and actual metrics.
Accuracy cannot establish precision/recall balance. Evaluation summaries use metric tables and
method facts instead of raw JSON. Model-generated tradeoffs remain fallible narrative.

Relevant files:
[Core continuity](../runtime/core/src/thymira/core/model_continuity.py),
[planning](../runtime/thy/src/thymira/thy/evaluation_plan.py),
[completion](../runtime/thy/src/thymira/thy/artifact_delivery.py),
[evaluator](../runtime/tools/src/thymira/tools/builtins/evaluate_model.py),
[Session evidence](../runtime/core/src/thymira/core/session_chat_context.py),
[Markdown](../apps/web/src/thymira/web/static/js/views/conversation-markdown.js).

Contract: [conversation sessions](contracts/conversation-sessions.md).
Decisions: [continuity/evaluation](../.agents/notes/implemented/2026-09-30-model-continuity-and-evaluation.md)
and [Session evidence](../.agents/notes/implemented/session-chat-evidence-continuity.md).

## Verification

**Historical read-only replay.** The new model-location projection includes the model filename
and valid citation r1e157 within 16,000 characters. The terminal question scopes evidence to the
failed Run, includes r0e207, excludes r0e208 and can produce a deterministic server diagnostic
after failed repair. The reusable-model projection selects exactly the approved second Run
model and excludes the failed third Run model. This is code replay, not a real-provider response.

The first implementation still omitted the model when long summaries filled the context.
Historical replay exposed this. Summary space is now reserved using escaped character lengths;
a regression includes four long summaries and an older model artifact.

**Local persistence and numerical execution.** The combined test trains a synthetic balanced
classifier with the actual local tool, uses an approved-source fixture, freezes its reference
through the Session API, imports it through Core and executes THY evaluation. The Gate pauses
the exact call. A JSON progress checkpoint, a recorded test human answer, rebuilt store and
new graph resume that deferred call. The result contains five metrics over 25 recorded holdout
rows, uses a different filename, preserves source model ID/hash, emits no new model.trained
event and leaves source events unchanged. Its persisted REPORT passes the independent MIRA
lineage and report-fidelity controls. The added consumer check initially found that evaluation
prose was not persisted as a REPORT; publication was corrected before delivery. Its explicit development sandbox policy and local
subprocess execution do not demonstrate production confinement or a live completed governance cycle.

**Scripted tests.** ScriptedProvider covers model location, framing/source enums, failed repair,
explicit-Run diagnostics, no-Run Ask, persisted work kind, ambiguous/foreign/tampered references,
missing/nonnumeric metrics, existing graph behavior and reconstruction. Focused regressions failed
before correction where feasible.

Final integrated selection: **396 passed** (21 selected test modules; one upstream asyncio
deprecation warning). After adding persisted evaluation reports and the MIRA consumer assertions,
the affected combined/summarize selection passed **10 tests**. These overlap the 396 and are not
added to that total.
Web DOM suite: **65 passed**, including Markdown/XSS, links, draft preservation, stale polls and
stable controls. No live browser/backend rendering claim is made. Counts from overlapping focused
runs are not added to the final selection.

Quality gates: Ruff passes; **707 files** pass formatting; ty reports zero diagnostics.
Imports: **4 contracts kept, 0 broken**. Package/invariant inventory and Agent Notes pass.
The final selection includes MIRA invariant and workspace-import regressions.

GPT-6 Sol xhigh independently reviewed scope, provenance, persistence, implicit training,
citations and completion. Identified concrete cases were corrected with regressions:
wrong-Run explanations, contradictory source selectors, same-byte alias provenance, over-16
model liveness, misleading training wording and partial model comparisons.
Two bounded implementations were delegated to GPT-6 Luna; no recursive delegation occurred.

## Size and structure

The previously flagged ConversationWorkspace loader moved to a function (original class:
193 lines, 10 methods, cohesion 0.25); it is no longer flagged. THY delivery validation and
result rendering have separate modules. Core catalog helpers moved to graph/agent_catalog.py.
Pre-existing Core adapter debt remains: module 2,002 lines/35 top-level definitions;
MiraSubgraph 373 lines, 12 methods, cohesion 0.24. This task did not rewrite the MIRA adapter.

Tools extraction reduced evaluate_model from 642 to 502 lines and run_experiment from 695 to
587. Their focused helpers have 167 and 128 lines. The detector flags none of these four
modules/classes. No lint/type baseline or architecture exception was relaxed.

## Limits and backend

- No new external LLM call, historical model execution, provider upload, Docker/WSL setup,
  push, merge or deployment occurred.
- Real-provider behavior remains unverified. Scripted tests do not prove conversational
  stability; unusual languages or ambiguous phrasing may require clarification.
- recorded_holdout supports the bundled default-training contract with complete validated
  reproducibility metadata and its runtime-added script hash. Legacy events have no explicit
  training-kind marker; recognition relies on the verified producer's contract, not arbitrary
  serialized model inspection. Custom preprocessing/splits are refused.
- Binary ROC-AUC can be unavailable for one-class samples, missing predict_proba or multiclass
  estimators. The artifact records null and a reason; required numeric AUC prevents completion.
  There is no fabricated value or silent all_rows fallback.
- Multi-model comparison and mixed evaluation/additional-deliverable plans are not silently
  reduced to one evaluation. Unsupported or ambiguous combinations return a diagnostic.
- SessionChat reads up to four recent Runs; work snapshots carry up to sixteen approved models.
  Out-of-snapshot, ambiguous or invalid references require clarification.
- Process/listener inspection found no thymira-api backend or listener on standard ports
  8000/8080. No running backend was shown to have loaded these changes. Start the updated source
  before attributing new live results to this implementation.
