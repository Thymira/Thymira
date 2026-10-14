# EDA scope, interview and blocked audit investigation

Investigation date: 2026-09-29. Product edits are confined to Thymira on its existing `main`
checkout. Preexisting changes and the reference repositories are preserved. The reported Run and
its persisted decisions were read, not approved, replayed or rewritten.

## Recorded incident

The trace matching the reported error is `run_ac252bfd659a443499700ec43caf2163`, in session
`session_10a99bed291a4a67b4b8492cf35615a8`. Its initial conversation was:

1. `Quiero hacer un EDA`.
2. Assistant: `¿Sobre qué dataset quieres realizar el EDA?`.
3. User: `german_credit`.

The interpreter proposed an action with `continues_clarification=false`. Run publication preserved
only the dataset name as the current request, with an empty `clarification_context`. The planner
then added an experiment task; `run_experiment` completed at sequence 415. This was broader work
than the user's EDA request.

All nine intake fields were extracted from `.thymira/context.md` before classification, so there
were no unanswered intake questions. The recorded context described offline research, no live
applicant decisions or deployment, and human review before downstream use. MIRA preflight returned
medium risk with 0.80 confidence. The execution classifier subsequently returned model development,
high risk and 0.75 confidence. Deterministic sensitive-attribute rules required review; this path
previously attempted clarification only for explicitly named missing fields. Its first human
approval is sequence 51.

There are 13 recorded human approvals, including start and tool decisions. This establishes that
approval evidence existed; it does not establish that all regulatory human-oversight requirements
were satisfied. The final critical model finding at sequence 878 alleged absent approval evidence
but cited sequence 3 (budget decision) and sequence 4 (model selection). Other model findings in
the same report claimed oversight was present. Several model findings also expected a terminal
Run event even though the audit ran before the final Gate decision.

The pandas correlation failure at sequence 323 was followed by a successful `run_python` execution
at sequence 364. A19 separately reported partial confinement at sequences 323, 364 and 415 because
`rlimits` and `workspace_quota` were recorded as unenforced. A19 was MEDIUM; the final `GOV-101`
block was triggered by the CRITICAL model finding. Neither the historical computation error nor
the partial-confinement warning alone explains that critical rule match.

## Independent checks of saved evidence

- `verify_events`: **895 events, valid chain**.
- Copied the artifact directory into an isolated `.pytest-eda-recorded-*` directory before
  constructing `LocalArtifactStore`, so native permission verification cannot alter the original.
  `store.verify()` returned **no errors**, for **28 manifest entries**.
- Recomputed `audit_run` against the same first 435 events used by the audit, with
  `AuditMode.IN_FLIGHT`: the only deterministic finding was **A19 MEDIUM**.
- Projected the new diagnostic directly from the saved Run and terminal state: it retains
  `GOV-101`, exposes four recorded recommendations, describes unenforced isolation limits and
  does not attribute the recovered pandas error to the confinement finding. It does not change
  the historical model findings or terminal outcome.

## Implemented session and intake corrections

- Recover only an exact declared dataset reply following an adjacent, same-actor/project Auto
  clarification with a validated complete parent chain. Freeze human messages before publication.
- Use that current scoped request for profile extraction and risk classification. Project-wide
  future capabilities do not become the Run's requested work.
- After uncertain execution classification, ask a bounded model-generated question when useful.
  Store the selection against the exact classification event, including a no-question result, so
  retries do not repeatedly call the provider. Reuse pending questions after reconstruction;
  no useful answerable question leaves the existing review requirement intact.
- Expose bounded recommendations from the bound audit report. The session action prepares an Ask
  draft for discussing corrections; it neither overwrites an existing draft nor executes work.
  A revised work request becomes a new governed turn; a blocked Run stays immutable.

## Executed checks for session and intake

- Dataset-reply regression failed with empty context before the fix; the bounded agent's final
  clarification selection passed **18 tests**.
- Added intake-source regression failed before using `conversation.requested_work`.
- Diagnostic regressions failed for absent recommendations and misattributed pandas errors.
- Session/API/risk/native-lock integration: **126 passed in 103.82 seconds**:
  `uv run --no-sync pytest tests/thymira/test_api_sessions.py
  tests/thymira/test_conversation_diagnostics.py tests/thymira/test_core_risk_interview.py
  tests/thymira/test_risk_clarification.py tests/thymira/test_api_risk_clarification.py
  tests/thymira/test_state_lifecycle_lock.py tests/thymira/test_session_clarifications.py
  tests/thymira/test_session_discussions.py -q --basetemp=.pytest-eda-session-integrated-0929`.
- The latest intake-source fix, graph composition, planning and complete conversation scenarios:
  **118 passed in 78.71 seconds**, one existing asyncio deprecation warning:
  `uv run --no-sync pytest tests/thymira/test_core_risk_interview.py
  tests/thymira/test_core_composition.py tests/thymira/test_thy_plan.py
  tests/thymira/test_conversation_e2e.py -q --basetemp=.pytest-eda-graphs-scope-0929`.
- All web tests: **56 passed** (`node --test apps/web/tests/*.mjs`).
- Final risk-interview selection, including interrupted selection publication and restoration:
  **28 passed in 19.19 seconds** (`tests/thymira/test_core_risk_interview.py`).
- Recorded recommendations in the bounded read-only chat context were absent in the initial
  regression (**2 failed**). After correction, the API/session-discussion selection passed
  **41 tests in 29.82 seconds** (`tests/thymira/test_api_sessions.py` and
  `tests/thymira/test_session_discussions.py`). These selections overlap with the integration
  above; their counts are not additional unique tests.
- Independent Sol medium review found no actionable issues in the session/intake/diagnostic
  increment. It independently confirmed the native lock race described below.

## Additional concurrency regression found during integration

An initial focused selection had **79 passed, 1 failed** in the concurrent approval/clarification
test. Its stack ended in the existing lock-file sentinel `flush()`, before lock acquisition.
A native test holding byte zero of an empty file reproduced the same `PermissionError` directly.
Removing the unnecessary sentinel write fixes the race; empty and existing populated files remain
compatible. Native lock tests and the original concurrency test then passed: **4 passed**.
The subsequent 126-test integration above includes that correction.

Windows permits byte-range locks beyond EOF, as documented by Microsoft for
[_locking](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170).
No machine configuration or permission relaxation is part of this correction.

## Audit correction and independent review

Audit producers now receive the declared activity profile, recorded approval references and the
explicit `IN_FLIGHT`/`FINAL` boundary. The deterministic audit also propagates that mode directly.
The new field preserves legacy JSON omission when it was not originally supplied, so historical
assurance bundles retain their exact report contents and digest. No historical event is rewritten.

Run citations are checked against the final bounded, escaped prompt. A visible, hash-verified
artifact excerpt can support its exact registered artifact reference without making its hidden
source event citable. Discovery producers receive bounded summaries of prior candidates separately
from the long technical report; the independent critic still receives the original evidence
snapshot and deterministic report. Genuine deterministic findings and policy rules are preserved.
The instruction to avoid positive compliance statements as findings is model guidance, not a
deterministic semantic filter.

The independent Sol xhigh review reproduced and checked corrections for legacy report/bundle
serialization, direct audit-mode propagation, invalid profile references, escaped visible
references, artifact-excerpt grounding and truncated discovery feedback. It found no regression
in native locking or critic independence. The audit agent's final focused selection passed
**289 tests** before the full coordinator integration.

The coordinator's broader selection passed **1,123 tests, 2 skipped, 28 warnings in 206.98 seconds**:

```bash
uv run --no-sync pytest tests/thymira/test_mira*.py \
  tests/thymira/test_core_graph_adapters.py tests/thymira/test_core_audit_freshness.py \
  tests/thymira/test_api_audit_persistence.py tests/thymira/test_api_audit_experiments.py \
  tests/thymira/test_api_contract.py tests/thymira/test_api_runs.py \
  tests/thymira/test_conversation_e2e.py tests/thymira/test_core_risk_interview.py \
  tests/thymira/test_session_clarifications.py tests/thymira/test_api_sessions.py \
  -q --basetemp=.pytest-eda-final-integration-0929
```

The two optional PostgreSQL integration tests skipped because the `pgvector/pgvector:pg16`
container image is not available locally; no image was installed. A focused `-rs` check confirmed
that reason (**10 passed, 2 skipped** across the pgvector/SQL files). The warnings came from the
existing asyncio event-loop access and NumPy edge-case datasets in profile-verifier tests.

That broad green suite was not enough to close the incident. Replaying the new projection against
the real 435-event snapshot, using the copied artifacts and default 12,000-character budget,
found **zero of 13 approval references and no profile reference visible**. The eight bounded
narrative messages still occupied 14,791 serialized characters and sorted before the 1,732-character
audit context. The real excerpt and artifact-name sections occupied 3,640 and 348 characters.
This exposed a missing projection-priority regression after the broad run.

The final projection gives explicit top-level priority to audit context and verified excerpts,
with narrative messages last. Each section retains deterministic value serialization and the
same framed total budget. Rechecking the exact recorded snapshot now gives:

```json
{
  "verified_events": 895,
  "audit_snapshot_events": 435,
  "deterministic_findings": [["A19", "MEDIUM"]],
  "audit_mode": "IN_FLIGHT",
  "recorded_approvals": 13,
  "approval_references_visible": 13,
  "profile_reference_visible": true,
  "visible_verified_artifact_excerpts": 2,
  "projection_chars": 12000
}
```

The synthetic regression contains a profile, 13 approvals, two large excerpts and eight large
narrative messages; it also verifies that proposed citations to those approvals remain grounded.
The final runner selection passed **55 tests**. The independent reviewer found no new issue in
the priority change or the preserved grounding boundaries. This checks what the real model would
receive, without making a live model request or predicting its response.

After that final priority change, integration with the audit agents, flow, Core adapters and
complete conversation lifecycle passed **108 tests in 68.28 seconds**, with the existing asyncio
deprecation warning:

```bash
uv run --no-sync pytest tests/thymira/test_mira_flow.py \
  tests/thymira/test_mira_agent_dispatch.py tests/thymira/test_mira_agents_*.py \
  tests/thymira/test_core_graph_adapters.py tests/thymira/test_conversation_e2e.py \
  -q --basetemp=.pytest-eda-projection-final-0929
```

Final workspace static checks passed: Ruff lint, Ruff format (**692 files**), `ty check`,
import contracts (**4 kept, 0 broken**), package/invariant inventory and Agent Notes validation.
The projection extraction leaves the MIRA runner below the 600-line module threshold; the
smaller risk-interview service still has its documented preexisting size/cohesion debt.

## Implementation locations

| Concern | Files |
|---|---|
| Dataset reply and human request binding | `runtime/core/src/thymira/core/session_chat.py`, `session_chat_clarifications.py` |
| Scoped intake and persistent execution-risk questions | `runtime/core/src/thymira/core/risk_interview.py`, `risk_interview_model.py`, `risk_classification.py`, `risk_uncertainty_questions.py` |
| Bound diagnostics and recommendations | `runtime/core/src/thymira/core/conversation_diagnostics.py`, `packages/schemas/src/thymira/schemas/conversation.py` |
| Read-only explanation evidence | `runtime/core/src/thymira/core/session_chat_context.py`, `session_chat_model.py` |
| Session remediation draft and UI | `apps/web/src/thymira/web/static/js/views/session-diagnostics.js`, `session.js` |
| Native lifecycle lock contention | `runtime/state/src/thymira/state/_lifecycle_lock.py` |
| Audit projection, citations and instructions | `runtime/mira/src/thymira/mira/agents/evidence_projection.py`, `runner.py` |
| Audit mode, legacy representation and discovery | `runtime/mira/src/thymira/mira/audit_io.py`, `checks/models.py`, `checks/controls.py`, `flow.py`, `orchestrator.py` |

The related lifecycle and diagnostic contracts are documented in
[`conversation-sessions.md`](contracts/conversation-sessions.md).

## Limits

Model providers are scripted in automated tests; filesystem persistence and native Windows locks
are real. No new live-model EDA or regulatory-compliance conclusion is claimed. The original Run
remains blocked. The backend must load the updated source before new messages use the corrections.
The selected suites above were run; the entire workspace suite was not rerun in this increment.
