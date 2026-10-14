# Session EDA failure investigation

Investigation date: 2026-09-29. Product edits are confined to Thymira. The user's failed Run was
read for diagnosis; it was not replayed, approved or rewritten.

## Recorded incident

Session `session_bc469c5152aa48e8a43db7313ffc6d49`, titled
`Quiero realizar un EDA del dataset`, contains this sequence:

1. User requests an EDA; assistant asks which dataset.
2. User supplies `german_credit`; assistant asks whether to perform an EDA on it.
3. User answers `Si`; assistant asks whether this confirms the EDA.
4. User answers `Si` again; chat publishes `run_f81ed581733d4fcdb36cd446cf0e5365`.

The Run's current conversation message is only `Si`; previous EDA/dataset messages appear in
discussion history. Its 18-event chain verifies. The closing transition at sequence 16 and
`run.failed` at sequence 17 record `inline execution failed: Windows ACL is not restrictive`
for the projection staging file `.run-n6hz7ad9.tmp`. Earlier events record initial activity facts.
There is no completed EDA tool call or requested human approval in this trace.

Read-only ACL inspection found the staging file had inherited entries (`D:AI`, `ID` ACE flags)
instead of the expected protected explicit entries (`D:PAI`). The Run directory and `run.json`
matched their expected protected ACLs. This incident is a persistence failure before analysis,
not evidence of a missing dataset or a failed EDA computation.

## Clarified request correction

`session_chat_model.py` now supplies dataset names/availability and typed prior discussion, and
instructs the interpreter to finish a work request once its missing input is supplied. This is
still a bounded model inference; no live-model accuracy claim is made from scripted tests.

`session_chat_clarifications.py` selects only complete human messages in the same actor's
explicitly linked Auto clarification chain. It excludes assistant text, stops at unrelated questions
or work, and rejects oversized or ambiguous legacy chains. The context is persisted before Run publication, bound to
retries, and copied into additive `Run.conversation.clarification_context`. The latest literal
message remains unchanged. `requested_work` supplies the combined request to THY's plan scope
and MIRA's risk classification/interview, without treating old Runs as current authorization.

## Windows persistence correction

An isolated synchronized test reproduced the incident before the fix; an additional deterministic
test showed parent securing overwrote an existing inherited child ACL. Directory updates now use
`SetSecurityInfo` with a `MAXIMUM_ALLOWED` handle to suppress child propagation. The protected
`D:PAI` verification, exact user/SYSTEM/Administrators permissions, reparse refusal and securing
before writing remain in place. File updates retain their existing native call. This uses the
[documented handle behavior](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo)
and needs no per-process retry or global lock.

## Executed focused checks

Implementation files:

- Native persistence guard: `packages/events/src/thymira/events/file_access.py`.
- Additive contracts: `packages/schemas/src/thymira/schemas/conversation.py` and `session_chat.py`.
- Interpretation/publication: `runtime/core/src/thymira/core/session_chat.py`,
  `session_chat_model.py`, `session_chat_context.py`, `session_chat_clarifications.py`, `conversations.py`.
- Risk context: `runtime/core/src/thymira/core/risk_interview.py` and `risk_uncertainty_questions.py`.
- Planning scope: `runtime/thy/src/thymira/thy/nodes/plan.py` and `plan_scope.py`.
- Regression coverage: `tests/thymira/test_events_file_access.py`, `test_session_clarifications.py`,
  `test_thy_plan.py` and `test_core_risk_interview.py`.

- The initial new clarification tests failed in four cases before implementation.
- Session discussion, API sessions, core conversation and initial clarification selection:
  **52 passed** (`--basetemp=.pytest-session-clarify-green-0929`).
- Expanded clarification and THY planning selection: **60 passed**, one existing asyncio
  deprecation warning (`--basetemp=.pytest-clarify-plan-0929`).
- Risk interview, risk clarification and API contracts: **82 passed**
  (`--basetemp=.pytest-clarify-risk-0929`).
- Final discussion/API/core conversation and six complete conversation graph scenarios:
  **54 passed** (`--basetemp=.pytest-clarify-integration-0929`).
- Final THY planning checks after scope-review corrections: **54 passed**, one existing asyncio
  warning (`--basetemp=.pytest-clarify-plan-final-0929`).
- Six conversation graph scenarios after final native ACL and linked-clarification changes:
  **6 passed** (`--basetemp=.pytest-eda-graphs-final-0929`).
- Native Windows private-file and LocalRunStore tests: **21 passed**. A separately attempted
  credential-root symlink test skipped because the account cannot create symlinks.
- Full non-slow suite: **3,928 passed, 7 skipped, 51 deselected, 1 setup error**, with 61 warnings,
  in 684.69 seconds (`uv run pytest -m 'not slow' -q
  --basetemp=.pytest-eda-full-final-0929`). The error was the five-second `docker image ls`
  prerequisite timeout for
  `test_tools_git_container_integration.py::test_container_git_reads_preserve_repository_bytes_and_record_enforcement[GitStatus-sample.txt]`.
  It occurred before the tested Git tool ran. An isolated diagnostic rerun of that exact node
  passed in 3.96 seconds (`--basetemp=.pytest-eda-docker-diagnostic-0929`). No timeout, test
  expectation or production code was changed to hide it. The initial full invocation is not
  represented as a clean pass.
- Ruff, ty, all four import contracts, package invariant inventory and Agent Notes validation
  passed during integration.
- Final workspace Ruff, formatting (689 files), ty, four import contracts and Agent Notes checks
  passed. `git diff --check` passed; Git reported existing line-ending conversion notices.

Coverage includes the dataset-only reply and short confirmation, process reconstruction, retries,
new-request and Ask boundaries, an intervening legacy Run, oversized history refusal, no dataset
contents in chat prompts, frozen pending action context, and suppression of unsolicited training
after a clarified EDA. Providers and execution in these checks are scripted; actual Windows
filesystem persistence is used.

## Independent review and limits

Sol xhigh reviewed this security/persistence increment independently. Review exposed adjacency
reviving an abandoned request and older model scope overriding a later EDA restriction. The final
implementation uses explicit parent links and ordered human messages. The newest explicit scope
prevails, including an intermediate correction followed by `Si`; bare registered dataset names
such as `models` or `classifier` do not request training. Legacy records without a parent binding
ask for a complete task restatement. These findings have dedicated regression coverage.

Final independent verification ran `test_events_file_access.py` and
`test_session_clarifications.py` in a separate temporary directory: **15 passed**. Four additional
pure scope reproductions passed. The final review reports no remaining actionable findings.

The user's Run remains failed as originally recorded. Tests do not replay it, call a live LLM,
or demonstrate a new real-model EDA. They verify the corrected runtime behavior with scripted
providers and real local persistence. A running backend must restart to load Python source changes.

## Follow-up: initial dataset question rejected (2026-09-29)

The new session `session_fee18ab89da04b089cb9c1f8f535fb72`, titled `Quiero realizar un EDA`,
has no Runs. Its first two discussions, `turn_1ca24257022f414485e7bf58a18a80d2` and
`turn_292226e2455c4035b57af49e1612092b`, both failed with
`The earlier clarification cannot be safely continued`. Each user message was
`Quiero realizar un EDA`. Read-only verification confirmed both seven-event model evidence
chains. In both responses the model asked which dataset to use, but incorrectly set
`continues_clarification=true` despite there being no earlier usable clarification.

This was a regression in the clarification change above: the backend discarded a useful first
question when it enforced a nonexistent parent. The model instructions now distinguish asking
a new question from answering an earlier recorded one. The interpreter also normalizes that
impossible initial continuation flag, without changing the proposed kind or message, only when
the full stored history contains no Run, pending or completed discussion, or selected action.
Earlier failed attempts remain excluded from work context. Established histories still use
strict parent validation, and Ask mode still cannot publish work. Raw provider evidence is
unchanged.

Verification for this follow-up:

- Before implementation, the three new regression cases failed with the reported error.
- Initial clarification/discussion checks: **37 passed**.
- Final selection, including an additional Ask boundary case: **63 passed in 44.77 seconds**:
  `uv run --no-sync pytest tests/thymira/test_session_clarifications.py
  tests/thymira/test_session_discussions.py tests/thymira/test_api_sessions.py
  tests/thymira/test_core_conversations.py -q --basetemp=.pytest-chat-root-final-0929`.
- Tests cover the exact initial dataset question, a new attempt after failed-only history,
  persistence after service reconstruction, idempotent retries, the subsequent dataset answer,
  exclusion of failed requests from scope, and an initial complete action in Auto versus Ask.
- Ruff and formatting checks passed for all four changed Python files; workspace ty reported
  no diagnostics; all four import contracts and Agent Notes validation passed.
- Independent Sol medium review reported no actionable findings. The reviewer did not rerun tests.

These tests use scripted providers and real temporary local persistence. This follow-up did not
repeat the full suite or a live-model EDA. The user's saved discussions and their evidence were
not rewritten or retried. A backend restart loads the correction; a new message in the same
session can start the clarification. Reusing a failed request identity preserves its recorded
failure rather than automatically invoking a provider again.
