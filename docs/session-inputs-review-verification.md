# Session datasets and human-review verification — 2026-09-27

## Reported failure

The existing local log for `run_849e4d13a4104bf297caeb82ca710a05` verifies as a hash chain.
It records an execution-start review for high risk and six missing context fields, followed by
human approval, resume and failure because `german_credit` points to absent `data/applications.csv`.
The failure was not caused by losing the Session link. The production Run, config and source data
were inspected only; none was rewritten or replaced with sample data.

## Changes and behavioral evidence

- `views/session.js`, `views/session-datasets.js`, `views/inputs.js` and `main.js` expose the existing
  dataset upload/replacement/target controls inside the session inspector. The router preserves
  `/datasets` across navigation/reload. The composer reports missing declarations and pauses sends
  while its upload is being validated. Project datasets remain shared across sessions.
- `core/conversation_inputs.py`, `core/conversations.py` and API `routes/sessions.py` reject a new
  turn before Run publication when declared files are missing, unreadable or resolve outside the
  project. Already accepted retries resolve before this check. Safe conflicts preserve the draft.
- `views/session-review.js`, `review-model.js` and `foldApprovals` explain the exact pending
  review from verified, redacted events. Execution-start risk and missing facts, tool arguments,
  plan or findings are rendered according to review type. Missing effects are called unknown.
  Polling preserves the note; stale/evidence errors disable answers. The existing Run Approvals
  tab reuses this context and retains the backend's legacy latest-pending fallback.
- `core/human_review.py` and API approve/reject accept optional `expected_decision_id`, rejecting
  stale answers without an approval event. Existing clients may omit it. Approval permissions and
  authenticated actor binding remain the existing ones.
- `state/review_lock.py`, `LocalRunStore.review_lock` and `RunService.resolve_approval` serialize
  competing answers using the same local per-Run OS lock across service instances/processes.
  Fresh state, Gate answer and transition occur inside the lock; dispatch occurs afterward.
  `RunService` shrank from 761 to 723 lines without adding a method. Independent Sol review of
  the integrated backend/UI and a focused concurrency review found no remaining introduced
  approval issue after the fixes, including legacy review fallback.
- `test_conversation_e2e.py::test_session_upload_reaches_real_eda_tool_and_survives_reconstruction`
  first reproduces the missing-source conflict, uploads a three-row CSV, runs the actual
  `profile_dataset` tool through the graph and Gate, and verifies its report, missing-value count,
  source hash and event chain. Rebuilt API/runtime dependencies reopen the same conversation;
  a follow-up provider request contains the prior result and retains the dataset.
- `test_api_session_inputs.py` covers upload/replacement, no-Run-on-failure, retry after source
  removal, follow-up/reconstruction, malformed-config redaction, containment and foreign scope.
  `test_api_runs.py` covers stale decision identity and unchanged legacy approval bodies.
- `apps/web/tests/*.mjs` exercises actual view/router code: file-form upload payload, readiness,
  in-chat resolution, failed/stale evidence, late responses, note retention and Run tabs.

## Browser check

A disposable API/web fixture at ports 8765/8766 used synthetic CSV data, authenticated test
credentials and a recording dispatcher. No live model or production workspace was used. The
browser selected and uploaded `uploaded_sample.csv` from the Session Datasets panel and displayed
`3 rows, 3 columns`. A real Gate execution-start decision displayed high risk, the six missing
fields, policy reason, approve/reject consequences and the note field. Answering it in the chat
advanced the Run; reload showed the persisted planning state and both datasets. The recording
dispatcher intentionally does not execute analysis; actual EDA is proved separately above.

Both the narrow stacked layout and desktop three-column layout were inspected. This caught a
missing router case for `/datasets` (fixed with a failing/passing router regression) and cramped
review space on narrow screens (fixed with a minimum conversation row height).

Local, ignored screenshots: `.pytest-browser/session-review.png`, `session-upload.png` and
`session-approved.png`. They contain only the synthetic fixture. The temporary browser tab was
closed and its viewport override reset; the test server is stopped (both ports refuse connections).

## Executed checks

- `uv run pytest tests/thymira/test_api_session_inputs.py tests/thymira/test_api_sessions.py
  tests/thymira/test_api_project.py tests/thymira/test_core_conversations.py
  tests/thymira/test_core_project_inputs.py tests/thymira/test_policies_approval.py
  tests/thymira/test_policies_execution_review.py tests/thymira/test_api_auth.py
  tests/thymira/test_web.py -q --basetemp=.pytest-tmp/session-corrections-resumed`:
  **145 passed, 1 skipped** (real symlink creation unavailable).
- `node --test apps/web/tests/*.mjs`: **33 passed**.
- `uv run pytest tests/thymira/test_api_contract.py tests/thymira/test_api_events.py -q
  --basetemp=.pytest-tmp/session-contract-final`: **43 passed**.
- `uv run pytest tests/thymira/test_core_approval_concurrency.py tests/thymira/test_api_runs.py
  tests/thymira/test_conversation_e2e.py -q --basetemp=.pytest-tmp/approval-final`:
  **36 passed**, including approval contention between local processes and real EDA continuation.
- The focused `test_core_execution_review.py` suite: **40 passed**, covering existing approval
  and resume paths after extracting the lock-protected resolution helper.
- Total for the four nonoverlapping Python groups: **264 passed, 1 skipped**; UI: **33 passed**.
- `uv run ruff check .` and `uv run ruff format --check .`: passed, **658 files formatted**.
- `uv run python scripts/ty_ratchet.py`: **0 diagnostics**; `scripts/lint_imports.py`:
  **4 contracts kept, 0 broken**; `scripts/check_package_invariants.py`: passed.
- `uv run yamllint .`, `scripts/validate_agent_notes.py`, `scripts/check_roadmap.py`,
  `scripts/validate_skills.py` (each Python script through `uv run python`), and `git diff --check`:
  passed. The two reference repositories remain clean.

## Limits

Readiness is not an atomic dataset snapshot. A file removed or replaced externally after a send
passes validation can still affect Inspect. Existing source registration and immutable Run copies
remain unchanged. Actual filesystem symlink creation is unavailable on this Windows host; the
portable resolved-path boundary test covers refusal, while the real-symlink test is skipped.

No live-provider or production EDA run was attempted. The user must upload their intended CSV
under `german_credit` (or remove its obsolete declaration) and send a new message; the historical
failed Run remains failed. A running production API process needs to be restarted to load the
backend changes. No deployment, push or reference-repository changes were made.
The full fast/slow suite was not rerun for this correction; the executed checks above are the
focused regression and workflow suites, and do not claim a live PostgreSQL or remote-model run.
