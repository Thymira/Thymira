# Blocked session Run verification — 2026-09-28

## Diagnosis

The original Run `run_644884962018435a9caf50dd133960ee` has 654 valid chained events. It was
inspected read-only; its event log, artifacts and decisions were not changed or replayed.

- `tool.completed` at sequences 127 and 146: `run_python` could not find Docker, exit 125,
  confinement `unusable`.
- Sequence 642: critical A19 finding, "Confinement enforced", referencing both failures.
- Sequence 652: policy BLOCK under GOV-101; sequence 653 closes with that exact audit binding.
- A separate Experiment task attempted training although the current message requested a brief
  EDA. Python source also contained literal `\n` outside strings and failed syntax validation.

The new read-only projection was run against these original events and returned GOV-101,
A19 and `run_python: container runtime unavailable: docker was not found`.
The local captured projection is `.pytest-browser/blocked-run-diagnostic.json`.

## Implementation evidence

- `runtime/core/.../conversation_diagnostics.py` and the additive `terminal_diagnostic` field
  reconstruct explanations from the exact terminal evidence; old Runs need no migration.
- `runtime/tools/.../builtins/python_source.py` validates syntax without execution or rewriting
  source, bounded to 100,000 UTF-8 bytes and 10,000 lines.
- `runtime/tools/.../sandbox/readiness.py`, `tool_preflight.py` and `/tools/runtime-status`
  check missing Docker, daemon reachability, Linux mode and image presence before tool approval.
- `runtime/thy/.../nodes/plan_scope.py` removes unsolicited Experiment/ML tasks from explicit
  EDA plans using the current conversation message. It preserves explicit model work and
  ambiguous follow-ups; it does not inspect arbitrary Coding-task semantics.
- `apps/web/.../views/session-diagnostics.js` shows the cause, linked audit and stable runtime
  status. Session polling retains the composer draft and does not repeat Docker probes.

## Verification boundary

Tests use real local state, event chains, Gate and API with temporary workspaces and scripted
providers. Docker subprocess results are bounded test doubles; no live model or container EDA
was executed. The reported user Run was not resumed, approved or replaced.

Regressions were observed failing before fixes for the absent diagnostic, approval before a
missing runtime, invalid Python before review, unsolicited EDA training, and false-positive
model routing from descriptive metrics and data-quality evaluation.

Checks run:

- Tools, runtime readiness, terminal diagnostics, Session API, API contract and plan tests:
  **306 passed, 1 deselected** (`-m 'not slow'`), before the final four plan-scope cases.
  Log: `.pytest-browser/blocked-integration.log`.
- Final `test_thy_plan.py`: **50 passed** after those additional scope cases.
- API/Core/conversation regression (`test_api*.py`, `test_core*.py`, `test_conversation*.py`,
  `-m 'not slow'`): **640 passed, 5 skipped, 6 deselected**. The five skips are symbolic-link
  creation tests unavailable to this Windows account. Log: `.pytest-browser/blocked-core-api.log`.
- The six excluded slow graph tests were then run separately: **6 passed**, including the
  scripted conversation end-to-end scenarios. Log: `.pytest-browser/blocked-e2e.log`.
- Final runtime-preflight/A19 checks: **12 passed**. A refusal before code execution leaves
  A19 NOT_APPLICABLE and records neither tool startup nor a policy/approval decision.
- Web Node tests: **50 passed**, including diagnostic reload, stable warning, draft preservation
  and continued send availability when Docker is absent.
- Ruff check and format check: passed; ty: zero diagnostics; import contracts: **4 kept, 0 broken**.
- Independent Sol review: three findings corrected and re-reviewed; no remaining finding in
  this change. Agent Notes, skill validation and roadmap consistency checks also pass.

## Browser evidence

A disposable real API/web fixture on ports 8765/8766 was populated using the persisted blocked
turn integration scenario and its temporary credential. The diagnostic and Docker warning were
visible, the audit link opened the matching decision, and returning to the session and reloading
retained the same explanation. The fixture did not execute Python or contact a model.

Local screenshot: `.pytest-browser/session-blocked-diagnostic.png`.

## Remaining environment step

The user chose to install Docker and WSL themselves. After installing/starting Docker with
Linux containers, build the configured sandbox image from this repository; the default is
`docker build -t thymira:dev .` (`justfile`'s `docker-build` recipe). A custom
`THYMIRA_SANDBOX_IMAGE` needs its corresponding image. Restart the API to load the Python
changes, reload the web console, and use **Recheck Python runtime** before a new turn.
The existing blocked Run remains an immutable historical result; use a follow-up turn for a retry.

Skills applied: python-debug, python-testing, python-testing-unit, python-testing-integration,
python-general, python-god-classes, ruff, ty, check-imports, changelog, computer-use.
