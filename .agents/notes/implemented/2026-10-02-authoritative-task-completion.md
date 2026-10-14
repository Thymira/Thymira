# Authoritative task completion

## Problem

AgentRunner keeps only the latest result per tool name and checks that projection only for
output schemas with an integer exit code. An independent successful call can hide failed
required work, while Data and Evaluation outputs can bypass the check altogether. THY uses
the resulting task status to schedule dependencies before its final deliverable validation.

## Decision

Extend the existing terminal settlement with task-scoped runtime evidence. Pair each attempt
with its arguments and terminal result through the existing task and call identities. Relate
repairs through the required deliverable, logical registered source, or evaluation inputs;
changed arguments and source bytes remain eligible. Require successful runtime evidence and
verified publication where the failed work promised an output.

A parked approval may receive a new call ID on resume. Match that continuation through the
recorded run, task, decision and tool-intent hash. A subsequent call with another ticket does
not inherit the spent authorization. The pending denial is retained in the log while the
actual continued attempt supplies its own terminal evidence.

THY validates its existing ArtifactRequirement contracts before a task becomes COMPLETED.
Evaluation claims require the successful task-owned evaluator and its verified metrics
artifact, including the normalized model, dataset, split and provenance contract. A plan may
make an unrelated exploratory attempt irrelevant only after its required work is verified;
the failed attempt remains in the authoritative log. Without a reliable boundary, unresolved
failed work remains a failure.

Keep the bounded plot correction as a separate, identified task. Its original failed task
remains failed, and its dependencies remain blocked. A repair inside the original task can
enable dependencies after that task settles successfully. Reports continue to derive their
task status and result from the same settlement.

## Alternatives considered

Latest-by-tool settlement loses independent failure identity. Keeping every failed call
forever prevents legitimate repair and exploration. Comparing source bytes rejects corrected
code. Trusting final model flags or artifact IDs accepts unperformed work. A second obligation
engine duplicates the existing settlement and scheduler without a named consumer requiring it.

## Consequences

Missing required outputs and unverified evaluation claims fail before dependent work starts.
Exact paths, typed content, hashes, provenance, budgets and one-shot approval remain required.
Provider failure and actual request-budget exhaustion retain separate diagnoses. The existing
THY/MIRA/Gate checks remain in place. This decision addresses prompt 02 only. Review corrections require all outputs of a failed
attempt to be republished after that attempt. The evaluation contract checks the final
referenced metrics publication even when another valid publication exists. Causal parallel
logs keep agent starts on their canonical chain; task completion must respect that surface.
The registered-source recovery control also exposed validated output descriptors represented
as tuples in memory. Both consumers accept lists and tuples without changing output identity.
A preflight failure also preserves raw Python output descriptors without injected defaults.
Normalize an omitted Python output kind to the canonical OTHER default before matching a
later validated repair; WriteFile retains its different optional-registration contract.
The real manager, event log and verified publication regression failed before this correction
and passed afterward.

The ordinary runner regressions first produced five failures and one passing repair control.
Their first implementation pass completed all six cases successfully. Independent review
found weak scheduler controls, a duplicate approval fold in plot recovery, partial output
repair, arbitrary evaluation artifact selection, and a manifest regression without registered source
identity. Those findings were returned to the
same implementer. Final source review accepted the shared settlement integration and requirement checks.
Review also found that adapting a plot-denial control had removed its serialized progress
reconstruction. That positive check was restored in the approval continuation test and passed; its final
independent review confirmed the reconstructed correction identity and preserved one-shot
approval. No further production findings remain. Full suite validation completed with only the independently reproduced pre-existing
conversation assertions and acceptance snapshot drift described below.

The first full suite reported 4110 passed, 11 failed and 78 skipped. Four conversation failures
were reproduced with the captured pre-change producer and scheduler source. Acceptance had
an existing experiment system-prompt snapshot drift; the new task status also exposes its
previously hidden sandbox refusal and requires the assertion to expect FAILED with no result.
No historical run or diagnostic source was changed. Root quality commands remain blocked by
existing temporary Python and CRLF YAML fixtures; the scoped source checks and zero-diagnostic
type gate passed before the review corrections.

Final focused evidence contains 34 passing cases: 23 new cases, ten adapted controls, and
one unchanged parallel-runtime control. The adapted plot continuation was then verified
after restoring its serialized progress round-trip; that repeated execution is not added
to the case count. The additional adapted acceptance test reaches its pre-existing snapshot
drift. All tests use synthetic providers and isolated fixtures.

Final source checks: Ruff check passes; Ruff format reports 740 files already formatted;
the canonical type ratchet reports zero diagnostics against the unchanged zero baseline;
import-linter reports four contracts kept and zero broken. Package invariants, 16 skills,
roadmap consistency and offline lock validation also passed during this change. The root
just check still stops on 17 pre-existing D100/T201 findings in .tmp-eda-followup. A global
format scan separately found 13 older fixture files needing formatting, and global YAML
checks encounter older CRLF configs. Source YAML checks pass. No exclusion, baseline, policy
or budget was relaxed to hide them. All 11 original diagnostic source/evidence files keep
their captured SHA-256 hashes.

Changed production files (eight):

- runtime/agents/src/thymira/agents/runner.py
- runtime/agents/src/thymira/agents/delegation.py
- runtime/agents/src/thymira/agents/task_completion.py (new)
- runtime/tools/src/thymira/tools/manager.py
- runtime/thy/src/thymira/thy/nodes/execute.py
- runtime/thy/src/thymira/thy/artifact_recovery.py
- runtime/thy/src/thymira/thy/task_completion.py (new)
- runtime/thy/src/thymira/thy/result_report.py

Test files (eleven):

- tests/thymira/test_agents_runner_settlement.py (new)
- tests/thymira/test_thy_task_completion.py (new)
- tests/thymira/test_thy_result_report.py (new)
- tests/thymira/test_thy_execute.py
- tests/thymira/test_thy_graph.py
- tests/thymira/test_model_evaluation_continuation.py
- tests/thymira/test_agents_tool_bridge.py
- tests/thymira/test_thy_agent_coding_recovery.py
- tests/thymira/test_e2e_tool_approval.py
- tests/thymira/test_acceptance_demo_run.py
- tests/thymira/test_mira_checks_report.py

CHANGELOG.md records the change. EvaluationResult and the original settlement module remain
byte-for-byte unchanged from the captured pre-change source. Earlier unrelated workspace
edits, historical Runs and diagnostics remain intact.

Final full-suite command:

```bash
UV_OFFLINE=true uv run --no-sync python -m pytest --basetemp .pytest-tmp-settlement-full-20261002-reviewed --junitxml=.tmp-settlement-review/final-suite.xml --tb=short -q
```

Result: 4119 passed, 5 failed, 78 skipped, 61 warnings in 768.02 seconds. The only later
change restored the serialized-progress assertions in the plot continuation control; that
control and source checks were rerun successfully. No production code changed during the
full-suite run. Repeated focal executions are not added to the full-suite count.

Remaining failures:

- test_acceptance_demo_run.py::test_demo_run_records_what_the_model_saw_and_what_it_left_behind:
  experiment.system-prompt.expected.md drift, reproduced with the pre-change source. Its new
  FAILED/no-result/refusal assertions pass before that existing snapshot comparison.
- test_conversation_e2e.py::test_session_upload_reaches_real_eda_tool_and_survives_reconstruction:
  a literal row_count assertion in the follow-up prompt. The actual settled profile result
  and task are intact.
- test_conversation_e2e.py::test_final_governance_controls_response_visibility_and_followup_context
  [WARNING-None] and [REQUIRE_HUMAN_REVIEW-approve]: literal governance-canary assertions.
- test_conversation_e2e.py::test_real_agent_response_reaches_followup_provider_after_restart:
  a literal JSON row_count assertion on the human-facing report.

The four conversation failures have the same assertions with the captured pre-change
producer and scheduler. This is a verified scope limitation, not a passed repository gate.
The pre-change compatibility comparison used fresh synthetic fixtures, not historical Runs.
Captured baseline source and final JUnit evidence are retained in .tmp-settlement-review.
Only this task's newly created scratch source copies and .pytest-settlement-* fixtures were
removed; all earlier data and the .pytest-tmp-settlement-* full-suite logs remain intact.
