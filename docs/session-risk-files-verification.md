# Session risk, review and Files verification

This record covers the follow-up corrections on 2026-09-27. Product changes are in `Thymira`
on `main`; neither reference repository was modified. Existing working-tree changes were retained.

## Behavior and evidence

- `views/session-interview.js` presents the pending risk question inside the active session,
  preserves its draft during polling, and submits the displayed question identity. The context
  editor requests correction of a declared fact against the exact pending review identity.
- `risk_clarification.py`, `risk_interview.py`, the controller and graph keep execution stopped,
  version the corrected profile and rerun classification. Clarification does not fabricate a
  human approval. The classifier receives all declared facts and the current requested work.
- `risk_answer_publication.py` recovers interrupted profile/follow-up writes from the saved
  answer judgment. `execution_review.py` retires superseded reviews when a clarification
  transition commits. A reconstructed graph reaches completion with a new execution decision,
  both THY and MIRA invoked, and zero fabricated `human.approval` events.
- `review-model.js` and `views/session-review.js` explain the request, action, recorded effects,
  constraints and approve/reject consequences. Tool arguments and code have their own disclosure.
  An unchanged background read preserves the verified review controls and optional note.
- `project_files.py`, `project_file_lock.py` and the Files API support authorized bounded UTF-8
  edits with revision conflicts and atomic replacement. Links, hidden files, binary content and
  redacted previews are refused. Concurrent API writers share an OS lock.
- MIRA's local store searches regulation locations and text. Explicit article references match
  the article location. Cross-references and empty searches cannot substitute unrelated evidence.

## Reported Run diagnosis

The reported Run `run_d277a25b86e941d69ff6986077fcd021` had a complete context-derived profile
but no user interview questions. The downstream classifier lacked several actual field values.
Its final audit searches for Articles 11, 12 and 14 returned no results because the article
numbers occur in packaged chunk locations, which the old search ignored. The mandatory audit
evidence guard correctly refused completion. That guard and the corpus content remain intact.

The original 450-event log was inspected without mutation. Its SHA-256 was rechecked after the
browser and review fixes and remains
`fae025914872dd2c350c38e54c8fd8853f151bb7326528b005aed3f09892bfc1`.

## Browser verification

A disposable workspace on local ports 8765/8766 used the real web/API, Gate and filesystem
persistence with a scripted provider and deterministic dispatcher. It did not use production
credentials, modify the reported Run or call an external model.

1. Answered an interview question from the session and reached its start review.
2. Edited `analysis.py` in Files and saved with Ctrl+S; verified the saved bytes on disk.
3. Restarted the fixture server against the same runtime directory. The same session, answer,
   pending review and saved file reappeared.
4. Used **Review risk context** and **Clarify this fact in chat**. The old approval card was
   replaced by a concrete decision-effect question while execution stayed stopped.
5. Recorded the correction. The same Run acquired profile version 4 and a new review decision;
   its event log still contained zero `human.approval` records.
6. Kept an approval note focused through polling; the note and enabled controls stayed stable,
   without the former refreshing/paused banner.

The screenshot is a local ignored test artifact at
`.pytest-browser/session-risk-interview-editor.png`. The fixture deliberately requests review
after classification; it proves browser/state integration, not external-model EDA execution.

## Checks

- Final Core/API/conversation integration — **646 passed, 5 skipped, 6 deselected** in 254.40s:

  ```bash
  uv run pytest tests/thymira/test_core_*.py tests/thymira/test_api_*.py \
    tests/thymira/test_conversation*.py tests/thymira/test_session_pagination.py \
    tests/thymira/test_agents_risk.py tests/thymira/test_risk_clarification.py \
    -q -m 'not slow' --basetemp=.pytest-tmp/session-final-integration --disable-warnings
  ```

- Final focused regression — **132 passed, 3 skipped** in 38.40s:

  ```bash
  uv run pytest tests/thymira/test_risk_clarification.py \
    tests/thymira/test_core_control_plane.py tests/thymira/test_core_run_state.py \
    tests/thymira/test_core_risk_interview.py tests/thymira/test_core_project_files.py \
    tests/thymira/test_api_project_files.py tests/thymira/test_mira_kb.py \
    tests/thymira/test_mira_agents_euaiact.py \
    -q --basetemp=.pytest-tmp/session-final-focus-fixed --disable-warnings
  ```

  The three focused skips require Windows symbolic-link privileges; the hardlink and
  mismatched-open-handle regressions executed. The final matrix includes the orphan-clarification
  retry by another writer and stale-answer rejection before and after the follow-up reparks.
- Browser JavaScript tests: `node --test apps/web/tests/*.mjs` — **48 passed**.
- Earlier broad MIRA and classifier regression: **940 passed, 2 skipped**. The final focused
  run above rechecked the changed KB and required EU AI Act audit-agent behavior. This includes
  real tool dispatch using scripted providers and refusal of absent or unrelated evidence.
- `test_core_resume_concurrency.py` pauses answer publication before and after the profile
  write. A second store's plain resume conflicts in both cases; exactly one dispatch occurs
  after releasing the review lock.
- Final quality checks: `uv run ruff check .`, `uv run ruff format --check .` (667 files),
  `uv run python scripts/ty_ratchet.py` (0 diagnostics, baseline 0), `uv run yamllint .`, and
  `git diff --check` passed. No type baseline was relaxed.
- `scripts/lint_imports.py`: **4 kept, 0 broken**. Package invariants, roadmap, Agent Notes and
  all 16 skill definitions validated successfully.

Logs are local ignored test artifacts in `.pytest-browser/session-final-integration.log`,
`.pytest-browser/session-final-focus-fixed.log` and `.pytest-browser/session-ui-final.log`.
The broad and focused runs overlap; their counts must not be added as unique tests.

Independent review found and drove regressions for filesystem hardlinks, mismatched preview
handles, regulation cross-references, stale clarification retries and interrupted answer
publication. The final persistence, concurrency, authorization and supersession review closed
without actionable findings. Resume/answer serialization was reviewed after its barrier tests.

The measured pre-extraction snapshot had `RiskInterviewService` at 759 lines and `RunService`
at 768. After the recovery work and cohesive helper extraction they measure 730 and 714 lines;
their modules decreased from 1,059/952 to 997/882 lines. They remain above the repository's
size thresholds, while the new publication, profile-record and resume helpers are unflagged.

## Limits

The existing deterministic rules can still require human review, including for sensitive data.
Declaring facts cannot override these policies. Files edits change the shared project source;
they do not rewrite registered dataset copies or prior Run artifacts. External editors do not
take the API writer lock, so an external edit in the final comparison/replacement interval is
not excluded. Path and open-handle checks mitigate swaps during preview; this local filesystem
API is not an isolation boundary against an adversarial process continually replacing project
directories. The previously failed production Run was not replayed with a live model.
This verification did not run the entire repository's slow suite. Existing API processes must
be restarted and the browser reloaded to use the changed Python and JavaScript files.

Applied skills: python-debug, python-testing, python-general, python-testing-unit,
python-testing-integration, python-god-classes, changelog, ruff, ty, check-imports,
computer-use:computer-use.
