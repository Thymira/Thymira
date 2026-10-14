# Conversation workspace verification — 2026-09-26

Implementation was made only in `Thymira` on `main`, starting at
`537943a7cdbdd1062188930976e5573b145c867c`. All three working trees were initially clean.
No branch merge, commit, push or deployment was performed.

## What changed

- A three-pane web workspace combines persistent session navigation, iterative chat, a
  read-only project explorer and a Run-scoped artifact inspector. Existing Run tabs, standalone
  creation, cancellation, approvals, interview, plan, inputs, audit and events remain available.
- Every conversation message creates an ordinary Run associated with the same Session.
  `ConversationInput` preserves the original message and retry identity. Explicit response
  events supply the next turn's bounded, framed context only after authorized audited completion;
  intermediate reasoning is excluded.
- Existing local Session/Run storage, lifecycle publication and hash-chained events provide
  persistence. Session locks serialize sends; retries return the same Run; scoped recovery
  repairs interrupted publication. Existing bearer permissions and project scope apply.

The [contract](contracts/conversation-sessions.md) specifies limits and failure behavior.
Key implementation files are `runtime/core/src/thymira/core/conversations.py`,
`runtime/core/src/thymira/core/conversation_response.py`,
`runtime/state/src/thymira/state/conversation_lock.py`,
`apps/api/src/thymira/api/routes/sessions.py`, and
`apps/web/src/thymira/web/static/js/views/session.js`.

## Follow-up verification: authorized responses and Session pagination

The review identified two defects in the initial implementation: THY response evidence was
projected before final governance, and filtering an already paginated Session list could hide
conversations. Both were reproduced before source changes. The initial governance reproduction
exposed the canary for BLOCK and pending review (2 failures); the initial web reproduction returned
only 1,000 of 1,001 Sessions. Five additional UI tests initially rendered unapproved text.

`conversation_response.py` now releases a response only when the verified event chain establishes
a terminal completion bound to the exact findings decision and typed MIRA snapshot containing that
response. PASS/WARNING or an exact authenticated human approval permits release. Rejection,
pending review, failure, cancellation, an obsolete rework pass, a response after the audit snapshot
and missing/mismatched bindings withhold it from both chat and future prompts. Raw Run evidence
remains inspectable under existing permissions; this is not a new confidentiality boundary.

The real approval-resume test exposed a missing terminal binding in `graph/compose.py`'s completion
node. It now reuses the existing bound completion path. An independent GPT-6 Sol xhigh review
identified a mismatch between graph approval matching and the canonical exact-subject/rejection
rules. Three cases reproduced it before correction (missing approval, wrong subject and rejection
followed by approval). The graph now uses the canonical helper too. A final bounded independent
review confirmed the findings resolved; the reviewer did not run tests or edit files.

Conversation filtering now happens before pagination in local, in-memory and PostgreSQL Session
repositories. Default unfiltered queries retain their cursor behavior. The web traverses all
pages and errors on repeated cursors rather than returning a silently partial list.

Fresh checks after these corrections:

| Check | Observed result |
|---|---|
| Focused Python command below | 110 passed, 1 skipped, 1 warning |
| Compatibility Python command below | 225 passed, 1 warning |
| Final `test_conversation_e2e.py` rerun, strengthened to reconstruct the API before resolving review | 5 passed |
| `node --test apps/web/tests/test_sessions_ui.mjs apps/web/tests/test_session_pagination.mjs` | 18 passed |
| Whole-repository Ruff check / format | Passed / 653 files already formatted |
| `uv run python scripts/ty_ratchet.py` | 0 diagnostics, baseline 0 |
| Import-linter | 4 contracts kept, 0 broken |
| YAML, Agent Notes, package invariants, roadmap and `git diff --check` | Passed |

```bash
uv run pytest tests/thymira/test_conversation_response.py \
  tests/thymira/test_conversation_e2e.py tests/thymira/test_api_sessions.py \
  tests/thymira/test_core_conversations.py tests/thymira/test_session_pagination.py \
  tests/thymira/test_api_session_pagination.py tests/thymira/test_core_project_files.py \
  tests/thymira/test_thy_summarize.py tests/thymira/test_web.py \
  -q --basetemp=.pytest-tmp/conversation-fix-focused

uv run pytest tests/thymira/test_core_composition.py \
  tests/thymira/test_core_execution_review.py tests/thymira/test_core_audit_freshness.py \
  tests/thymira/test_core_rework_assurance.py tests/thymira/test_core_graph_adapters.py \
  tests/thymira/test_api_runs.py tests/thymira/test_api_contract.py \
  tests/thymira/test_api_auth.py tests/thymira/test_api_app.py \
  tests/thymira/test_core_sessions.py tests/thymira/test_state_repositories.py \
  -q --basetemp=.pytest-tmp/governance-compatibility

uv run pytest tests/thymira/test_conversation_e2e.py \
  -q --basetemp=.pytest-tmp/governance-restart-final
```

The last five-test rerun repeats the E2E subset; it is not five additional distinct tests.
It proves authorized follow-up continuity after reconstruction, withheld blocked/pending/rejected
text, and release after approval through the real API, graph, disk stores and scripted model.
PASS and WARNING release also have focused event/Gate tests. PostgreSQL filtering was checked at
the compiled query/paginator boundary, without a live database. The single skip still requires
Windows symlink privileges. The warning is an existing dependency event-loop deprecation.

The full fast lane, demo snapshot and real browser walkthrough below were run during the initial
implementation, not repeated for this follow-up. Their results are retained separately rather
than presented as fresh verification. No live LLM or PostgreSQL service was used in the follow-up.

## Initial implementation automated evidence

Commands ran from the repository root in Bash using the locked workspace dependencies.
Each concurrent pytest invocation used its own `--basetemp` directory.

| Check | Observed result |
|---|---|
| Baseline `test_api_runs.py test_web.py test_core_sessions.py` before implementation | 69 passed |
| Final focused command below | 86 passed, 1 skipped, 1 warning |
| `node --test apps/web/tests/test_sessions_ui.mjs` | 11 passed |
| Compatibility: `test_api_contract.py test_api_auth.py test_api_app.py test_schemas.py test_core_graph_adapters.py test_thy_summarize.py` | 121 passed, 1 warning |
| Full fast lane: `uv run pytest -m 'not slow' -q --basetemp=.pytest-tmp/workspace-regression-sequential` | 3,696 passed, 54 skipped, 46 deselected; 1 existing failure |
| Existing demo acceptance, run with the focused integration group | Snapshot failure reproduced on unchanged main; the rest of that group passed (26 tests) |
| Ruff check and format, YAML lint | Passed |
| `uv run python scripts/ty_ratchet.py` | 0 diagnostics, baseline 0 |
| Import contracts / package invariants | 4 kept, 0 broken / passed |
| Skills, roadmap and Agent Notes validators; `uv lock --check` | Passed |
| `git diff --check` | Passed |

Initial implementation focused command:

```bash
uv run pytest tests/thymira/test_api_sessions.py \
  tests/thymira/test_conversation_e2e.py \
  tests/thymira/test_core_conversations.py \
  tests/thymira/test_core_project_files.py \
  tests/thymira/test_thy_summarize.py tests/thymira/test_web.py \
  -q --basetemp=.pytest-tmp/final-focused
node --test apps/web/tests/test_sessions_ui.mjs
```

`test_real_agent_response_reaches_followup_provider_after_restart` uses the real API,
production graph factory, THY, agent runner, lifecycle stores, audit/report transitions and
event chains. It reconstructs all dependencies from the same disk directory and asserts that
the second model request contains the first message and its actual `row_count: 27` response.
The LLM is `ScriptedProvider`; a no-tool catalog and test policy isolate continuity. Optional
intake interview and MIRA model fan-out remain covered by existing suites. This is not a live
model-provider or Docker execution test.

Additional tests cover simultaneous sends (one publication, one conflict), idempotency after
restart, changed-body/actor retry conflicts, failed/cancelled turns, interrupted Session linking,
corrupt response history, project separation, viewer permissions, redaction, explorer limits,
stale UI replies (including reordered reads of the same session), initial loading,
uncertain-send retries and all existing Run tab routes.
The one focused skip is a filesystem symlink test requiring Windows privileges unavailable to
this test account. No live PostgreSQL service was used; its compatible recovery adapter was
type/import checked, while conversation persistence was exercised on the default local backend.

## Existing failures independently reproduced

A temporary `git archive HEAD` copy loaded its own source directories, verified by the imported
`thymira.core` path, using the same Python dependencies. Both failures below also occur at the
unchanged starting commit. Their fixtures and runtime behavior were not changed to hide them.

- `test_thy_context_recovery.py::test_running_graph_keeps_authorized_tool_pairs_intact_across_compaction`:
  the task ends with `agent exceeded max_turns` where the test expects `COMPLETED`.
- `test_acceptance_demo_run.py::test_demo_run_records_what_the_model_saw_and_what_it_left_behind`:
  the expected snapshot has a 366-byte `.thymira/config.yaml`; the unmodified example is 353
  bytes with SHA-256 `0eba07033db86500548a5765b22a56948f83435efc386a7d0ebb09f0e8f69953`.
  Earlier execution, approvals, event verification and artifact assertions complete before this
  final snapshot comparison.

An initial xdist attempt could not collect consistently because existing parametrizations create
random IDs during collection. The general regression run above therefore used sequential pytest.
The complete slow lane was not run. Existing dependency deprecation and model feature-name
warnings remain in the general test output.

## Real browser verification

The actual static web client and API ran on loopback ports 8766/8765 against disposable local
state, a real ephemeral bearer credential and the same deterministic graph composition.

1. Created `session_2a58c377a2864c71a400b94f6b93ad1f` in the sidebar.
2. Sent “Inspect the sample and keep its row count”; Run
   `run_20eff4171c704600a25b888bd90a708b` completed with `{"row_count":27,"columns":["sample"]}`.
3. Sent “Use that previous row count and explain the sample”; Run
   `run_f99442df980740dc8931b7acaec48a20` completed as Turn 2 in the same transcript.
4. Reloaded and navigated between session and Run views; both turns remained in order.
   A separate earlier smoke session also verified creating another session and returning to the
   first without mixing histories.
5. Opened the existing Approvals and Interview tabs and returned through “Back to session”.
6. Read `readme.md` in the explorer; opened an independently seeded `summary.md` test artifact
   belonging to the first Run in the inspector while the chat remained visible. The artifact
   was seeded through `LocalArtifactStore`, not claimed as model-generated output.
7. Stopped the API/web process, started a fresh process on the same state directory, reconnected
   with the newly minted process credential, and reopened both turns and the same artifact.

The desktop layout was visually inspected at 1280 × 720. This proves the local deterministic
workflow, not live-provider quality or a production deployment.

## Independent review and remaining boundaries

A read-only GPT-6 Sol medium review inspected persistence, retry/concurrency rules, authorization,
path confinement, compatibility and UI state. Its two findings were fixed: existing Run subroutes
were restored in the hash router, and conversation summaries now include only completed task
outputs. Focused tests cover both corrections, including actual entrypoint hash navigation.

History for the next prompt is limited to twelve prior turns and 16,000 framed characters;
full persisted history remains inspectable. Artifacts stay with their producing Run and are
not automatically mounted into every later sandbox. The explorer is read-only and previews
bounded UTF-8 text. No account/login product or cross-project access was added.

A process exit after publication but before execution dispatch can leave a visible created
Run waiting. Retrying does not blindly execute it twice; existing cancellation and supported
resume controls apply. This change does not add a durable dispatch queue or promise automatic
reexecution after a crash.

Reference provenance: the old redesign was consulted at
`e4d195d86cb41cea20cc9c924fc599ddca2e2d69`; OpenChamber at
`60d836c4893b6b694610b5b0277be54111359428` uses the MIT license, copyright 2025 Bohdan
Triapitsyn. Interaction ideas were adapted; no OpenChamber code/assets were copied. Both
reference working trees remain untouched.
