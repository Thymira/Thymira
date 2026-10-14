# Session read tools

## Problem

SessionChat could answer from injected conversation evidence or start analytical work, but it
could not inspect other visible project files or older Runs on demand. This limited iterative
answers while making it important that reads remain separate from Run creation, source mutation,
and artifact access changes.

## Decision

Reuse SessionChat's bounded structured-completion pathway and `ScriptedProvider`. An internal
typed lookup proposal can request only `list_files`, `read_file`, filename `glob`, or literal
`grep`; server code executes the request and returns bounded untrusted evidence to the next
proposal. Public discussion kinds remain answer, clarification, and action. Queries run through
the dedicated `runtime/tools/src/thymira/tools/read_queries.py` policy boundary, which evaluates
the injected project Policy using `evaluate_capability`, applies its constraints, and records
query policy and lifecycle in `SessionProviderLog` without a Run envelope.

The virtual tree is `project/...` and `runs/{actual-run-id}/...`. Project files reuse existing
bounded and redacting readers. Run paths are limited to actual Runs from the same project and
Session, including older turns; event traces are verified and projected through an operational
field allowlist. Registered artifact contents require the exact creation event in the approved
audit snapshot, successful approved-response binding, and matching manifest digest/size/validity
checks. A later invalidation blocks that exact artifact ID; a new approved revision with the same
name remains readable. Successful server records alone receive citation IDs. Project citations
carry a path without Run provenance, and the web client opens that path, including the project
root, in the existing explorer. Glob patterns containing `/` are relative to the explicit scope,
and grep reports whether a match excerpt clipped either side of its source line.

## Alternatives considered

Using ordinary `ToolManager.execute` would require a fabricated Run or a Run lock and event
envelope for a read-only Session query. Adding a second provider/native-call framework would
duplicate the structured request path and its budget controls. Both would weaken the boundary
between ordinary discussion and analytical work, so the fixed read-query function is a dedicated
policy seam consumed directly by Core.

## Consequences

Each discussion can attempt at most four lookup queries and at most six provider requests,
including one final answer and one citation repair. The inclusive token cap remains 16,000 and
can refuse earlier. Query errors are bounded, have no citation, and still consume a query
attempt. Citation repair uses the answer-only schema and cannot issue another lookup. The new
behavior is covered by `tests/thymira/test_session_read_tools.py`, existing Session repair and
budget tests, and the focused project-citation explorer test.

Validation: read tools passed 14 tests, existing Session suites 107, all web tests 68, and
project-citation checks passed. Ruff, formatting, type ratchet (0 diagnostics), imports (4 kept,
0 broken), package invariants, 16 skills checks, roadmap and notes validation passed. The split
no-slow suite totaled 4,391 passed, 8 failed and 56 skipped: the two collection-sensitive files
passed 96 tests in series; the remaining parallel group had 4,295 passed, 8 failed and 56 skipped.

The same eight failures reproduced in 69.54s on the current tree and 67.92s with all changed
Python sources restored from the pre-task snapshot in a temporary overlay and the original
discussion test helper. Module-location assertions confirmed `core.session_chat`,
`session_chat_budget` and `api.deps` loaded from that overlay, which retained the earlier
two-request cap. Automated comparison matched all eight node IDs and failing lines after
normalizing random `run_` and
`decision_` IDs. They remain unresolved: approval cases expect `COMPLETED` but receive `FAILED`;
removed-agent settlement emits no event; THY tool-budget refusal raises and its ledger case has an
empty result; both model-evaluation continuation cases raise `KeyError: artifact_id`; failed THY
work after `run_python` reports the error string `experiment failed: run_python failed: simulated
division by zero`. This evidence does not claim
those expectations were restored by this change. The isolated reproductions used:

    uv run --no-sync pytest -q \
      tests/thymira/test_e2e_tool_approval.py::test_a_rejection_resumes_and_the_agent_gets_a_factual_denial_without_reasking \
      'tests/thymira/test_e2e_tool_approval.py::test_the_production_factory_and_real_mira_accept_either_human_answer[False]' \
      tests/thymira/test_e2e_tool_approval.py::test_a_restored_plan_settles_a_later_task_whose_agent_was_removed \
      tests/thymira/test_core_graph_adapters.py::test_thy_adapter_records_its_real_tool_budget_refusal \
      tests/thymira/test_core_graph_adapters.py::test_the_usage_ledger_survives_the_parks_a_tool_review_causes \
      tests/thymira/test_model_evaluation_continuation.py \
      tests/thymira/test_thy_e2e.py::test_failed_run_reaches_summarize_after_a_run_python_failure_is_diagnosed

Raw `uv run --no-sync pytest -m 'not slow' -n 4` still has three collection errors from randomized
IDs in `test_api_contract.py` and `test_mira_runner.py`; those files passed 96 tests serially.
Failure logs are retained at
`/tmp/thymira-read-failures-current.txt`, `/tmp/thymira-read-failures-baseline.txt`,
`/tmp/thymira-chat-read-fast-parallel.txt` and `/tmp/thymira-chat-read-fast-serial.txt`. The
implementation snapshot is `/tmp/thymira-session-read-tools.hsTQVl`.
