# Task-scoped Python retry progress

## Problem

Python syntax and output-publication failures could be requested repeatedly when a task resumed,
used a new tool-call ID, or lost resolved message history during compaction. Message history did
not provide durable task-owned evidence. Publication errors exposed only prose, so the runner
could not tell an unchanged missing-output set from verified progress. Process failures and
publication failures also needed separate retry identities.

## Decision

Reconstruct retry stops from `tool_attempts_for_task` and terminal events. Group syntax failures by
the exact inline source or registered revision, relevant declared destination, and structured
`SyntaxError` fields, and stop after two unresolved identical failures. Revalidate the recorded
arguments to prove the syntax cause; do not count prose matches or unrelated preflight errors.

Group Python publication failures through the existing task-completion output declarations and
same-work/source-path identity. The first failed publication and one unchanged corrective failure
stop before another model request. A strict reduction in the best observed missing-output set
starts a new band with that failed attempt counted as its first failure, leaving one subsequent
correction. Growth or oscillation earns no additional band. A compact typed `publication_progress`
witness carries only validated missing and present `(path, kind)` identities through the manager;
the canonical result remains a manager-owned failure envelope. Full completion still requires
`assess_task_completion` and the existing validators.

## Alternatives considered

Mutable message-history counters lose counts across compaction and do not cover registered Python
`write_file` preflight failures. Parsing error prose cannot reliably distinguish a Python syntax
cause. Filesystem presence or model promises are not authoritative publication evidence. Preserving
the failed execution value as the canonical result would break the failure-envelope contract.
Output-manifest declarations remain conservative because their dynamic-output contract belongs to
the later work item.

## Consequences

Counts now survive resumed approvals, new call IDs, fresh runs for the same task, and reopening a
JSONL event log. A validated reduction in the best observed missing-output set grants only a finite
additional attempt; it is retry evidence, not a successful partial publication. Failed publication
cannot complete a task or unlock dependencies. Corrected sources and unrelated destinations remain
independent. Process exit failures do not consume the publication retry band.

**Final review.**
The principal review accepted the monotonic missing-set comparison and the round-trip validation of
constructed or copied publication witnesses. `AgentRunner` measures 507 lines against the 510-line
baseline, `ToolManager` 907 against 922, their modules 1134 against 1139 and 1292 against 1302, and
`results.py` retains its baseline 32 top-level definitions.

**Validation.**
The combined focused run of `test_tools_manager.py` and the retry, settlement, task-completion,
provider, approval, delegation, and THY controls passed 197 tests with one existing
`pydantic_graph` deprecation warning. It includes 21 Work04 regression test instances and 176 prior
control instances. The corrected serialization-injection test patches the success serializer in
`result_evidence.py`, where the manager now calls it.

The exact three original diagnostic nodes were rerun separately: 3 passed with one existing
`pydantic_graph` deprecation warning. Their JUnit report is
`.tmp-retries-review/work04-diagnostic-final.xml`. The initial reproduction recorded 2 failed and
1 passed. These diagnostic results are separate from, and not added to, the 197 ordinary test
instances above.

The full repository run completed before that test-only correction with 4150 passed, 6 failed, 78
skipped, and 61 warnings. The serialization-injection test was one of the six and now passes in the
combined focused run. Five other nodes remain failing: the acceptance demo's model-view assertion;
the session-upload EDA assertion; two final-governance response assertions; and the follow-up
provider assertion after restart. The full suite was not repeated after correcting the injection
point, so its recorded result remains 4150/6/78/61.

The primary review also passed source Ruff and formatting across 739 files, `ty ratchet` with zero
diagnostics, all four import contracts, the package invariant check, all 16 skill validations, the
roadmap check, offline lock validation for 251 entries, and the original 11 diagnostic evidence
hashes. The global `just check` still reports the same 17 baseline Ruff D100/T201 findings under
`.tmp-eda-followup`. Global `ruff format --check .` reports 750 files formatted and 13 fixture files
that would be reformatted. Global `yamllint .` reports CRLF findings across historical and newly
generated pytest fixture/config files; source YAML validation passed.

**Files.**
The 11 production files are `runtime/agents/src/thymira/agents/{runner.py,tool_bridge.py,
task_completion.py,failure_outcome.py,retry_policy.py}` and
`runtime/tools/src/thymira/tools/{manager.py,models.py,results.py,publication_progress.py,
result_evidence.py}`, plus `runtime/tools/src/thymira/tools/builtins/run_python.py`. The three test
files are `tests/thymira/test_agents_runner_settlement.py`,
`tests/thymira/test_agents_tool_bridge.py`, and `tests/thymira/test_tools_manager.py`. The two
documentation files are `CHANGELOG.md` and this note (16 files total).

**Limits.**
`output_manifest_path` remains conservative because dynamic output declarations belong to the later
work item. A missing-set reduction records bounded retry progress only; it does not report that any
part of an atomic publication succeeded. The complete repository suite still has the five listed
failures, and was not rerun after the test-only monkeypatch correction.
