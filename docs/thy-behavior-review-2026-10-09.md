# THY behavior review — 2026-10-09

This is a diagnosis, not a product change. Historical Sessions, Runs, event logs, models and
artifacts were read directly; none was replayed, deserialized as an executable model or mutated.
New execution used synthetic fixtures, temporary directories and ScriptedProvider. No external
LLM call, historical training, deployment or model-configuration change was made.

## Coverage and interpretation

The local credit-risk workspace contains 25 Sessions and 31 Runs, dated 2026-09-27 through
2026-10-07. All 31 Run event chains pass `verify_events`. This verifies recorded chain integrity,
not that every recorded model claim is true. The canonical `run_state.outcome` distribution is:

| Outcome | Runs |
| --- | ---: |
| completed | 10 |
| failed | 16 |
| blocked | 4 |
| cancelled | 1 |

These Runs span several source revisions and configurations. They are not a controlled model
comparison or a current-version success-rate benchmark. The immutable `run.status` inside the
stored wrapper is not the canonical outcome; using it would incorrectly classify these Runs as
CREATED.

The settled specialist outcomes include 20 completed Data tasks, one failed Data task, eight
completed Coding tasks, six failed Coding tasks, one skipped Coding task and three completed
Experiment tasks. Multiple tasks can belong to one Run. Ordinary `agent.message` narration was
excluded from these counts.

## What Data and Coding actually do

| Specialist | Responsibility | Actual tools | Maximum model turns |
| --- | --- | --- | ---: |
| data | Inspect/profile registered datasets and bounded SQL queries; deterministic basic EDA charts | glob, read_file, profile_dataset, query_sql | 10 |
| coding | Write and execute custom Python, publish outputs, render PDFs and execute notebooks | write_file, run_python, read_file, export_pdf, run_notebook | 40 |
| experiment | Tracked new model training | Separate training specialist; not generic Coding | Separate spec |
| evaluation | Evaluate a verified existing model without fitting a replacement | evaluate_model | Separate spec |

The production catalog contains these four specialists. THY's planner selects tasks; Execute
resolves the selected names against that catalog and passes only each specialist's tool allowlist
through ToolManager and Gate. Data cannot invoke `run_python`; Coding does not receive
`profile_dataset`.

Current planning instructions explicitly prefer `data` with
`profile_dataset(include_plots=true)` for descriptive distributions and numeric correlations.
Coding is justified for custom analysis, custom plots, notebooks or other outputs beyond that
deterministic tool. New training normally belongs to `experiment`; requesting more metrics for
an existing model belongs to `evaluation`.

Thus, an EDA or a chart request does not inherently require Coding. Conversely, selecting Coding
for basic EDA adds script generation, execution, output publication and possibly human reviews.
The October 7 EDA in `run_9bea197be85b4d7a8ce3be55ed663105` selected that more fragile route,
while `run_674ceffd81fd47d1832e3adcc14d1032` completed basic EDA through Data alone.

The recorded planning gateway request at seq 216 in the former Run did not contain today's
basic-EDA/Data preference. Its recorded Coding requests did contain `output_manifest_path`
guidance. This distinguishes an evolving planning prompt from the separately demonstrated
failure to follow output-publication guidance. Current source and a historical running process
must not be assumed to have identical loaded instructions.

The names do not imply distinct language models. Current `analyze` and `code` routing both default
to STANDARD; `extract` defaults to FAST, subject to configured floors and overrides. Historical
specialist `model.selected` events name `gpt-4o-mini` for both Data and Coding. No historical
coding-performance conclusion can be attributed to a Sol-versus-Luna comparison.

## Confirmed current defect: a model clarification loses authorized training

Historical evidence: Session `session_22297cace30742909d7cf2adcc27f213`, Run
`run_70e5695033ed4bf4bd3ae46d1dd807bf`.

1. The linked human request was `Puedes realizar un modelo?`.
2. The human answered the dataset clarification with
   `german_credit, el modelo uno sencillito`.
3. The planner response at seq 135 proposed both Data profiling and Experiment training.
4. Only Data was delegated. Its profile and two PNGs were published; the Run completed at
   seq 695 without any training task or model.

The current pure resolver reproduces the cause:

```python
resolve_model_work_intent(
    "german_credit, el modelo uno sencillito",
    ("Puedes realizar un modelo?",),
    dataset_names=("german_credit",),
)
# Actual: ModelWorkIntent.GENERAL
```

`_single_model_work_intent` sees a model noun but no recognized action in the clarification.
It returns GENERAL with `explicit_scope=True`. `resolve_model_work_intent` consequently returns
before inheriting the linked human training request. `normalize_model_scope` then removes the
Experiment task as unrequested work, retaining Data with no diagnostic.

This is a deterministic scope-resolution defect, not a missing Coding invocation or an
Experiment execution failure. The authorization guard is valuable; its classification of this
clarification is wrong. The closure property should be: a linked training request followed by a
dataset/model-detail answer must preserve that request or ask a clarification, rather than finish
successfully after silently removing training. Explicit prohibitions, explanations and EDA-only
requests must continue to prohibit unrequested training.

## Confirmed current defect: successful evaluation metrics are rejected

The original existing-model continuation tests first fail in their own callback because they
read the old flat terminal-value format. Adapting only that callback in an isolated diagnostic
to read the current canonical result does not make the tests pass. Both continuation cases
instead fail with:

```text
evaluation failed: evaluation metrics id is not a successful task-owned evaluate_model publication
```

The producer/consumer chain explains this second failure:

1. The actual `EvaluateModel` tool trains nothing, computes metrics and registers a METRICS
   artifact. Its typed value contains that artifact's ID.
2. ToolManager persists the canonical result as
   `payload["value"] = {"kind": "success", "value": {"artifact_id": ..., ...}}`.
3. `tool_attempts_for_task` copies the entire canonical envelope into `ToolAttemptEvidence.value`.
4. THY's `_validate_evaluation_result` checks `attempt.value.get("artifact_id")`, which returns
   None because the ID is inside the nested `value`. It finds no successful producer and rejects
   the task even when the actual metrics artifact was published.

The same flat lookup appears in `_evaluation_repair_publication`, the agent-level recovery
consumer. That equivalent consumer was identified by source inspection; the integrated probe
demonstrates the completion failure, not a separate live retry sequence.

The green unit cases in `test_thy_task_completion.py` fabricate flat `value={"artifact_id": ...}`
events in `_record_success`, explaining why that focused module alone missed the integration
defect. A fix must align the existing result projection and both consumers with the actual
producer, preserving task ownership, active artifact identity, hashes and requested metrics.
Removing those validations would hide the error and weaken completion authority.

## Confirmed current defect: a promised chart can complete without any plot

A synthetic current-source THY Run asks for a chart. Its Data task explicitly instructs it to
produce one, but has no named `required_artifacts`. ScriptedProvider invokes the real
`profile_dataset(include_plots=false)` and then returns a valid DataProfile. The tool completes
and publishes the profile JSON. There are no PLOT artifacts, yet `run_thy` returns `error=None`
and a completion summary.

This is the current equivalent of the historical chart omission, isolated from that Run's
additional mistaken input-as-output requirement. The failure is not caused by an unavailable
tool: the diagnostic registers every tool in Data's allowlist and explicitly requires a
completed `profile_dataset` event before asserting chart delivery.

DataProfile describes columns, dtypes and missingness; it carries no chart-delivery requirement.
The task validator enforces the model-authored `required_artifacts`, which can be empty. The
final graph then sees a completed task and no missing declared requirement. Prompt guidance
encourages `include_plots=true`, but the closure contract does not enforce the promised plot.

The required property is completion against the requested output even when the user did not
give a filename. A valid JSON profile alone must not satisfy an explicit chart request. This
does not imply every Data request needs charts or that basic charts should be routed to Coding.

## Why historical Coding tasks failed

| Evidence | Observed cause | Interpretation today |
| --- | --- | --- |
| run_644884962018435a9caf50dd133960ee; run_d277a25b86e941d69ff6986077fcd021 | run_python exit 125: Docker/native container unavailable | Environment readiness, before useful Python execution. Current readiness regressions pass; this does not certify this machine's live Docker setup. |
| run_9da1488cec2549da8ea07220c6965bc7 | Correlation over mixed categorical/numeric columns raises ValueError; invalid write_file calls omit description | Generated-code and tool-argument errors. Current recovery and validation tests pass, without guaranteeing future LLM adherence. |
| run_f5953c8385d94d00b793cdb34be43a3b; run_c2b5ef5e2d4048b39afc7a24e09b7d13 | 40 provider_error responses per Coding task, zero response tokens, no Coding tool execution; then max_turns | Not 40 failed Python programs. Current provider-failure tests settle one rejected call without unchanged retries. |
| run_4ed45afb6a384bbab1be24695c396b66 | Declared plots were not saved, repeated publication failures despite Python exit 0, approval/retry churn; user cancellation | Output-contract failures and review friction. It recorded 33 Coding provider requests, below the 40 ceiling; merely raising max_turns does not explain or solve this Run. |
| run_9bea197be85b4d7a8ce3be55ed663105 | Script saved plots and an output manifest, but run_python received output_artifacts=[] and no output_manifest_path; final CodeResult claimed three unpublished plots | Python execution succeeded, artifact publication did not. Current task completion correctly rejects unregistered final references. |

For the two 40-error Runs, existing offline diagnosis reproduced an incompatible gateway schema
with root combinators in the former `run_python` input schema. Current tests verify that the
gateway receives no unsupported root combinators and that provider failures carry diagnostics.
The old remote error body is absent: schema incompatibility is a supported causal inference,
not a recovered remote error message. The historical error digest hashes the exception type,
not the complete provider request or error text.

In the October 7 publication case, the registered script reference was valid. The missing
manifest argument is visible in `tool.started` seq 293. `agent.completed` seq 311 explicitly
rejects three final artifact references that were not task-owned, active and hash-verified.
Writing or reading a manifest file does not itself register its listed outputs; the producing
call must consume it. Current prompt guidance already describes that contract, but the historical
model did not follow it.

Direct read-only filesystem checks confirm that the three PNGs physically exist in that Run's
workspace (15,528, 151,926 and 77,014 bytes), as does its 260-byte output manifest. None of those
four paths appears as an artifact in the historical store manifest. This independently supports
the publication diagnosis rather than a Python plotting failure.

## Other historical behavior problems

- **False overall success:** `run_f5953c8385d94d00b793cdb34be43a3b` completed despite a failed
  Coding task; `run_56c5cdb030934db6be2197462581cc67` completed despite a failed Data task.
  Current `run_thy` propagates unrepaired task failure in `ThyOutput.error`, and the production
  Core adapter raises `ThyExecutionError`. Current task-completion regressions pass. These
  records must not be interpreted as proof that the current code still ignores failed tasks.
- **Incorrect deliverable planning:** the chart request in `run_56c5...` planned an existing
  input dataset as a required output and called `profile_dataset` without `include_plots`.
  The tool published a JSON profile, not the requested chart. The planner's input/output
  confusion and omission are distinct from Data's correctly rejected task result.
- **Exact-path mismatch:** `run_1c08c45d0bbd43e1ad5f23828b9b2505` published
  `data/eda_plots.png` while the plan required `eda_plots.png`. Both Coding tasks completed, but
  the Run failed its declared deliverable contract. Current exact-path checks are intentional;
  the plan and producer must agree on the requested path.
- **Unrequested training:** the EDA clarification in `run_ac252bfd659a443499700ec43caf2163`
  triggered an Experiment. Current human-intent scope guards and EDA-negative planning tests
  cover that class of error. The current lost-training defect above is a separate false negative.
- **Retraining instead of evaluation:** `run_dd78b235995e4bb6a0b5a49104baa35f` asked for more
  metrics but ran another Experiment and only produced accuracy. Current verified-model
  continuity and a separate evaluation agent address that mechanism.
- **New training mistaken for prior-model evaluation:** `run_492333117a18423fa36a75aeefa8e288`
  rejected an explicit new-model request for lack of a prior model reference. Current planning
  regression `test_session_training_request_does_not_require_a_prior_model_reference` passes.

Some Run failures occurred outside THY's specialists: missing/disallowed model routes, a missing
dataset path, Windows ACL staging, MIRA regulation search validation and projection read-back.
For example, Coding completed in `run_5ba3...`, while MIRA's regulation-search validation failed;
Coding also completed the correlation-matrix request in `run_cc818...`, whose final state was
blocked by governance. A Run's final status alone does not identify the failing agent.

## Historical Run inventory

Abbreviated names below identify unique Run prefixes in the reviewed workspace. D = Data,
C = Coding, X = Experiment. Specialist outcomes are shown only where a task settled.

| Run prefix | Canonical outcome | Specialist evidence / diagnostic |
| --- | --- | --- |
| 9d246eef | failed | Unconfigured model route; no specialist execution |
| 24d89375 | failed | Disallowed model route; no specialist execution |
| 4c64401a | failed | Disallowed model route; no specialist execution |
| 6d11445a | failed | Disallowed model route; no specialist execution |
| 92b2fd1a | failed | Disallowed model route; no specialist execution |
| 849e4d13 | failed | Declared dataset unavailable; no specialist execution |
| d277a25b | failed | D completed, C failed (container readiness); terminal MIRA search failure |
| 64488496 | blocked | D completed, C failed (container readiness) |
| 26781283 | completed | D and C completed |
| 9da1488c | failed | D completed, C failed (code/arguments); explanatory message launched work |
| f81ed581 | failed | ACL staging failure; no specialist execution |
| ac252bfd | blocked | D, C and X completed; EDA caused unrequested training |
| 5ba3b9be | failed | D and C completed; MIRA regulation-search validation failed |
| 1c08c45d | failed | D and two C tasks completed; required path missing |
| 65b90cc6 | completed | D and C completed |
| b866f757 | completed | X completed for explicitly requested logistic model |
| dd78b235 | failed | X completed for an existing-model metrics request; wrong work and delivery |
| 14cca302 | completed | D and C completed; EDA outputs and PDF |
| 3934603e | failed | Planning output retry exhausted; no specialist execution |
| f5953c83 | completed | D completed, C failed after 40 provider errors; false overall success |
| c2b5ef5e | failed | D completed, C failed after 40 provider errors; required charts missing |
| d1c16be3 | completed | D completed; no Coding task |
| 4ed45afb | cancelled | D completed, C skipped on stop; output/review churn |
| 8746878c | completed | Two D tasks completed; duplicate profiling |
| 56c5cdb0 | completed | D failed; chart omitted and input mistaken for output; false overall success |
| cc818401 | blocked | D and C completed, correlation plot published; governance block |
| 49233311 | failed | New training required a prior-model reference; no specialist execution |
| 9bea197b | blocked | D completed, C failed final artifact verification |
| 3e5d50a7 | failed | D completed; terminal projection read-back failed |
| 674ceffd | completed | D completed with profile and charts; no Coding task |
| 70e56950 | completed | D completed; model clarification silently lost training |

## Verification

The focused existing regression selection ran with `uv run --no-sync pytest -q -n 4` and an
isolated `--basetemp`, covering provider failure/diagnostics, Coding recovery, task completion,
planning, Python output manifests, runtime readiness, cumulative turn counts and model-evaluation
continuation. Result: **156 passed, 2 failed**.

Both failures occur in the old test callback in
`tests/thymira/test_model_evaluation_continuation.py`, which reads
`terminal.payload["value"]["artifact_id"]`. The current canonical terminal envelope is
`{"kind": "success", "value": {"artifact_id": ...}}`. The test itself raises the KeyError after
the actual evaluation has published metrics. An isolated diagnostic using the canonical contract
then exposes the separate production completion failure described above; no repository assertion
was weakened and no product fix was made during this review.

The persisted diagnostic file is
`diagnostics/2026-10-09-thy-review/test_behavior_reproductions.py`; it sits outside pytest's
normal `testpaths` because these are intentionally failing diagnosis artifacts. The adjacent
README records commands and `evaluation_callback_probe.patch` preserves the exact isolated
adaptation of the existing evaluation test. Apply that patch to a temporary copy of
`tests/thymira/test_model_evaluation_continuation.py` to recreate the evaluation probe.

Sol independently read the final artifacts with Luna inactive, checked the actual production
producers/consumers, and repeated the behavior diagnostics with the repository's standard
isolation plugin:

```bash
uv run --no-sync pytest -p tests.conftest -c pyproject.toml -q -n 2 \
  diagnostics/2026-10-09-thy-review/test_behavior_reproductions.py \
  --basetemp=.pytest_tmp_thy_behavior_independent_review
```

Result: **3 expected failures**, two upstream event-loop deprecation warnings, 68.89 seconds.
The failures are exactly persisted GENERAL intent, a plan with no Experiment, and no PLOT with
`ThyOutput.error=None`. The EDA/explanation controls and actual completed-profile preconditions
pass before those failing assertions. The chart assertion accepts either real delivery or an
explicit failed closure, so rejecting incomplete work is a legitimate future correction.

Sol also independently repeated the two adapted evaluation continuation cases with
`-p tests.conftest`, two workers and a separate `.pytest_tmp_thy_eval_independent_review` base.
Result: **2 expected failures**, two upstream event-loop deprecation warnings, 144.10 seconds.
Both cases pass canonical result reconstruction and fail the unchanged `resumed.error is None`
assertion with `evaluation metrics id is not a successful task-owned evaluate_model publication`.
No callback KeyError remains. The temporary adapted source differs from the original only by
the added result imports and the callback projection preserved in the diagnostic patch.

Ruff check and Ruff format check pass on the new diagnostic file. Its only scoped lint exception
is S101 for pytest assertions, matching their diagnostic role; repository configuration and
existing test expectations were not changed. Product behavior was not modified.

## Scope left for subsequent work

The immediate product priorities are preserving human model-work intent across linked
clarifications, aligning evaluation completion/recovery with canonical tool results and enforcing
requested chart delivery. Subsequent fixes should start with the diagnostic regressions and verify
the actual producer/consumer chains.
The clarification fix must retain existing prohibition and explanation guards; the evaluation fix
must retain task ownership and independently verified metric requirements. Prompt-only reminders
or larger budgets cannot guarantee either property.

This review does not claim real-provider reliability, a working live container on this host or a
new fully completed governed Run. Historical defects whose current regressions pass are kept
separate from defects reproduced against current source.

## Source and historical evidence locations

Paths below are relative to the repository root. Historical event file line numbers are
one-based; recorded `seq` is zero-based.

- Intent resolver: `packages/schemas/src/thymira/schemas/conversation.py:167`, especially the
  `explicit_scope` early return and the model-noun fallback at the end of
  `_single_model_work_intent`.
- Session action scope: `runtime/core/src/thymira/core/session_chat.py:574`.
- THY intent resolution: `runtime/thy/src/thymira/thy/nodes/plan.py:297`; scope removal:
  `runtime/thy/src/thymira/thy/nodes/plan_scope.py:18`.
- Task delivery validation: `runtime/thy/src/thymira/thy/task_completion.py:28`.
- Evaluation's incorrect flat lookup: `runtime/thy/src/thymira/thy/task_completion.py:148`;
  recovery equivalent: `runtime/agents/src/thymira/agents/task_completion.py:438`; evidence
  projection: the same module at lines 135–146. Canonical producer envelope:
  `runtime/tools/src/thymira/tools/results.py:441`.
- Final failure propagation: `runtime/thy/src/thymira/thy/graph.py:439` and
  `runtime/core/src/thymira/core/graph/adapters.py:490`.
- Historical model-plan response: `examples/credit-risk/.thymira/runtime/runs/`
  `run_70e5695033ed4bf4bd3ae46d1dd807bf/events.jsonl:136`.
- Historical unpublished-output call and rejection: `examples/credit-risk/.thymira/runtime/runs/`
  `run_9bea197be85b4d7a8ce3be55ed663105/events.jsonl:294` and line 312.
- Historical chart omission and failed Data result: `examples/credit-risk/.thymira/runtime/runs/`
  `run_56c5cdb030934db6be2197462581cc67/events.jsonl:136`, line 150 and line 159.

Related prior diagnoses: `docs/session-provider-schema-verification.md`,
`docs/session-python-publication-verification.md`, `docs/session-eda-audit-verification.md` and
`docs/session-model-continuity-verification.md`. Their past passing counts were not substituted
for current test execution.
