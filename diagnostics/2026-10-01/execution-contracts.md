# Execution, publication and retry contract investigation

Date: 2026-10-01. Current workspace code only. This is diagnosis, not a repair.

Skills applied: python-debug, python-testing, python-testing-unit,
python-testing-integration, python-general.

## Boundary and evidence

Read AGENTS.md, the member READMEs, current Python source/preflight/publication code,
AgentRunner and its model/tool bridges, request-budget reconstruction, THY delivery and
settlement paths, and the two prior verification documents. Historical claims below are
attributed to those documents; this investigation did not read or execute historical scripts,
resume a Run, call a live provider, or change product code. Current behavior cannot establish
which uncommitted code was loaded by an old process.

Reproductions are in `diagnostics/2026-10-01/test_execution_contracts.py`. Their boundary is
real AgentRunner, ToolManager, Gate, event log and LocalArtifactStore; scripted LLM responses
and a deterministic sandbox replace model/network/process execution. All I/O is beneath each
pytest `tmp_path`. Every witness checks the log hash chain and store hashes. Desired property
assertions deliberately fail on current code; they are not accidental suite regressions.

Final diagnostic result: **6 failed, 1 passed**. The six red assertions demonstrate four
different mechanisms and one already-documented limitation; two assertions exercise the same
schema-dependent mechanism. Existing targeted controls: **60 passed**, covering Python output
manifests, registered-source selection, Python write preflight, cumulative task budgets and
the tool bridge. One dependency event-loop deprecation warning appeared.

Commands, through Git Bash with the existing uv executable:

```bash
uv run --no-sync python -m pytest diagnostics/2026-10-01/test_execution_contracts.py -q -s --basetemp=diagnostics/2026-10-01/tmp/execution-contracts-v2
uv run --no-sync python -m pytest tests/thymira/test_tools_python_output_manifest.py tests/thymira/test_tools_python_source_reference.py tests/thymira/test_tools_python_write_preflight.py tests/thymira/test_agents_cumulative_turns.py tests/thymira/test_agents_tool_bridge.py -q --basetemp=diagnostics/2026-10-01/tmp/execution-contract-controls
```

## Confirmed: an independent later call can erase an unresolved publication failure

Witness: `test_later_unrelated_python_success_does_not_clear_missing_output`.

1. A real `run_python` call uses source A and declares `source_a.txt`. The scripted sandbox
   reports interpreter exit 0 and creates no output. The tool correctly reports publication
   failure and registers no output.
2. A second `run_python` call uses unrelated source B, declares no outputs, and succeeds.
3. The model submits a valid CodeResult with `exit_code=0`.
4. AgentRunner records **COMPLETED**, with both FAILED and COMPLETED tool events still present.

Cause: `runner.py:1128-1147` reconstructs unresolved failures into `latest[tool]`, keyed only
by tool name. At line 1146, the successful B replaces failed A even though the arguments,
source and requested artifact differ. No event says A's missing output was repaired. The
final check at `runner.py:1064-1072` therefore sees no outstanding failure.

Property broken: successful independent work does not settle another execution's failed
publication. The append-only record is intact; its task-success projection is lossy.

Blast radius: all CodeResult-based task calls using the same tool for independent work,
including several Python programs, several file paths, notebook execution or exports.
THY stores the outcome as dependency status (`nodes/execute.py:437-439`) and `_blocked`
at `nodes/execute.py:890-891` permits a dependent task once that status is COMPLETED.
The global exact-deliverable check (`graph.py:413-443`) remains a later barrier when the
missing file was explicitly required by Plan. Undeclared outputs and task-local dependency
readiness do not gain that protection. This is a task projection defect, not evidence of a
Policy Engine bypass or proof that a whole Run necessarily passes MIRA.

Falsification/control: the existing
`test_agent_runner_rejects_completion_after_an_unresolved_tool_failure` passes when there is
only the failed call. The witness changes only the addition of an unrelated successful call.

Historical explanatory scope: can explain apparent recovery after a successful unrelated
tool call. It does not explain why the documented cancelled graphical Run repeatedly failed
publication; that Run never reached this witness's successful terminal projection.

## Confirmed: task success checks depend on the model's output-schema shape

Witnesses: `test_output_without_exit_code_does_not_hide_failed_tool` and
`test_real_evaluation_schema_does_not_complete_after_tool_failure`.

The first has the same actual Python publication failure, followed by a DataProfileOutput.
The second uses the shipped EvaluationResult schema with an allowed `evaluate_model` boundary
returning a failed ToolResult, followed by a model-authored success summary and artifact ID.
Both tasks record **COMPLETED**. The evaluator witness needs no model deserialization,
training or real dataset.

Cause: `runner.py:1064-1069` invokes `_unresolved_tool_failures` only when
`getattr(result.output, "exit_code", None)` is an int. EvaluationResult is just a summary and
optional metrics ID (`thy/agents/evaluation.py:9-14`). Most analytical output schemas likewise
have no exit code. Thus the same recorded tool failure is a completion barrier for Coding but
is ignored for Evaluation/Data/etc. The model's chosen schema becomes a second source of
truth for whether runtime failure evidence is consulted. This is not an authorization grant,
but it affects success and dependency scheduling.

The evaluation prompt itself states that a failed or denied call is not completed evaluation
(`evaluation.py:37-39`); the runtime does not enforce that property at settlement. An invented
`metrics_artifact_id` is accepted as plain text, and this Runner path does not verify it against
the successful tool's returned identity. THY's required metrics checks can still reject the
whole pass later (`artifact_delivery.py:136-163,166-233`). No universal Run-level false pass
is claimed.

Historical explanatory scope: a concrete latent mechanism for false completed evaluation or
analysis steps. It does not explain the documented training Run's structured planning failure;
that failure happened before any evaluation or training tool.

## Confirmed coverage gap: identical invalid registered writes still consume the whole cap

Witness: `test_identical_invalid_registered_write_stops_before_third_request`.

Three identical calls attempt `write_file(kind="code", path="analysis.py")` with
`x = 1\\ny = 2`. Every call receives the same syntax diagnostic before Gate or dispatch.
No source file is written. The model is called three times and the task stops only at its
configured request cap of three.

Cause: the new write preflight is effective (`builtins/file_arguments.py:39-44`), but the
exact-source early-stop tracker only counts `run_python` (`tool_bridge.py:73-74,83-84,266-272`)
and recognizes its specific error prefix (`tool_bridge.py:50-52,127-135`). Moving the same
invalid-source rejection into `write_file` does not carry the repeat property to that earlier
stage of the current saved-source workflow.

Control: `test_current_exact_inline_syntax_guard_stops_at_two` passes with the same invalid
source submitted to `run_python`. The 60-test control batch also passes the existing
registered-source/approval-history guard cases. The earlier source-reference hypothesis that
registered `run_python` syntax errors lose this prefix is **falsified**:
`tool_preflight.py:92-99` deliberately wraps that error into the recognized prefix.

Historical explanatory scope: a continuation of the invalid-generation failure class at a
new seam. It does not establish that a historical Run used today's write preflight, nor that
today's generous Coding cap of 40 is always reached: independent context/run ceilings may
stop sooner (`thy/agents/coding.py:39-47`).

## Confirmed scope gap: tool-capable structured retries lose field-level repair feedback

Witness: `test_tool_capable_structured_retry_preserves_field_feedback`.

The provider returns `final_result` for CodeResult with `stdout` but without required
`exit_code`. On the second request, the provider actually receives:

```text
Validation feedback:
the model returned invalid CodeResult output

Fix the errors and try again.
```

No missing field or validation code is supplied. A scripted corrected result succeeds, proving
the witness reached the intended repair boundary; the assertion that repair feedback names
`exit_code` is red.

Cause: `_tool_turn_response` validates the output call and replaces every ValidationError
with one generic string (`model_binding.py:340-346`). The no-function-tools structured path
instead preserves the provider's safe validation message (`model_binding.py:607-619`), and
the provider now creates field-level feedback. The prior document's structured-feedback fix
therefore covers the planner's no-tools path, but not this tool-capable Coding/evaluator path.
Invalid values are correctly absent; the missing safe field path is a separate issue.

Historical explanatory scope: repeated malformed final results can receive low-information
repair prompts and exhaust requests. It does not overturn the existing diagnosis of the
historical planner failure or establish that any historical Coding failure used this exact
terminal-output defect.

## Confirmed documented limitation: source rewrites are not publication progress

Witness: `test_changed_python_sources_without_publication_progress_stop_early`.

Three different valid sources all declare the same missing `source_a.txt`. There are three
exit-zero publication failures and no output; the task stops only at its cap of three. This
does not break the bounded-request contract. It establishes that no code-level stop detects
the unchanged publication obstruction across changed sources. The earlier documentation
already acknowledges this exact-source limitation.

Code: `run_python.py:193-209` distinguishes publication from interpreter failure correctly.
The repeat guard at `tool_bridge.py:266-272,286-292` is exclusively syntax preflight; source
identity at `tool_bridge.py:116-124` cannot represent publication progress. The instruction
to make at most one targeted correction (`artifact_recovery.py:71-74`) is prompt guidance;
it is not an AgentRunner execution counter for retries inside that delegation. A separate
one-correction THY plot recovery identity is enforced (`artifact_recovery.py:192-228`), but
does not bound the model's internal rewrite loop before settlement.

Historical explanatory scope: directly matches the documented cancelled graphical follow-up's
valid rewrites with the same missing categorical plot declaration. It is independent of the
documented pandas KeyError and of literal-newline syntax failures; those have different
inputs, stages and repair criteria.

## Existing fixes independently contrasted with current code

- Registered execution binds exact active source ID/digest/bytes before Gate and rechecks it
  at execution (`python_reference.py:34-103,106-137`; `run_python.py:129-137,152-164`). The
  source-reference controls pass. This solves filename/source substitution, not semantic
  output mismatch.
- Output manifests remove the independent model-guessed descriptor list for dynamic outputs;
  mixing nonempty fixed descriptors with a manifest is rejected (`python_arguments.py:67-72`).
  Fresh manifest checks occur before Gate and at dispatch (`tool_preflight.py:76-81`;
  `run_python.py:140-142`); descriptors are bounded and outputs plus manifest enter one batch
  (`python_output_manifest.py:21-22,51-68`; `run_python.py:323-327`). Controls pass.
- Publication failure preserves exit zero and names the missing/present declarations
  (`python_output_manifest.py:71-79`; `run_python.py:317-322`). A suspected masking of that
  error by nonempty stdout is **falsified** at the public manager seam:
  `results.py:329-346` converts failure into the manager-owned ToolFailureValue before the
  model projection. This witness's actual model-visible error includes the full diagnostic.
- The task request cap counts durable owned dispatch events (`request_budget.py:17-31`) and
  is used for fresh and resumed calls (`runner.py:954-965`). The cumulative-approval and
  event-log reopening controls pass. This prevents cap replenishment; it cannot decide whether
  another request makes useful progress.
- Valid bytes and hashes establish stored integrity, not fresh creation by the latest program.
  That limit remains explicit in the prior document. `_publish_output_artifacts` reads whatever
  declared file is present after exit (`run_python.py:277-315`). This investigation does not
  restate it as a newly discovered defect.

## Static suspicions kept separate

No experiment here proves these:

- `parallel_tool_calls=False` is passed to the provider (`runner.py:895`), while
  `_tool_turn_response` accepts an arbitrary returned list of calls (`model_binding.py:338-347`).
  The new single-call safety depends on the provider honoring the setting; the runtime's
  restored-history guard still detects split deferred batches (`runner.py:157-193`). A
  noncompliant multi-call provider and full approval reconstruction would be the next
  falsification test. This is not evidence that today's real provider emitted such a batch.
- A malicious or external actor modifying files while publication reads them could invalidate
  stat-based aggregate byte accounting. ToolManager's workspace lock and sandbox cleanup reduce
  this scenario. No concurrent-race or actual confinement experiment was performed, so no
  race defect is claimed.

No repairs, restarts, live Runs or provider calls were performed. No full-repository gate was
run because no production change was made. The next repair design should start from these
red properties and decide explicit settlement/progress identities before changing retry limits.
