# Session discussion and risk clarification verification

Verified locally on 2026-09-28. Product changes are confined to Thymira; the reference repositories
and the user's recorded Runs were not modified, replayed or approved.

## Observed failure

Session `session_856fb9355a914a6cb1e4bc340b82e0b7` contains a completed EDA Run
(`run_26781283455d48b89770e58d15d4d046`) and a failed Run started by the follow-up “que es eso”
(`run_9da1488cec2549da8ea07220c6965bc7`). Their event chains were verified before diagnosis.
The first Run asked repeatedly about decision effects despite explicit test-only answers.
The limit review followed three attempts on that field, not twelve total questions. The second
message entered the action-only session API and repeated intake/execution instead of discussing
the existing result.

## Implemented behavior

- The web composer offers Auto, Ask and Request work. Auto uses one structured model call to
  distinguish a question, an ambiguous message and a request for new work. Ask cannot dispatch
  work. Explicit work uses the existing Run publication, policy and lifecycle controls.
- Read-only discussion persists across navigation and application reconstruction, with exact
  actor/message/mode retry binding, cross-process publication locks and a separate hash-linked
  provider record. It uses the existing model router, immutable Session allowlist and RequestLedger.
- Explanations use bounded verified Run facts, approved summaries and prior discussion. Citations
  must name supplied evidence. Provisional results, raw tool arguments and other sessions' data
  are excluded. No tools or artifact contents are exposed to this explanation lane.
- MIRA uncertainty asks for a relevant concrete fact using the assessment, full current profile,
  user's request and previous answers. Existing offline/test-only declarations stay in context.
  Questions retain exact assessment/profile bindings and bounded retries. Human review remains
  necessary when no useful question is available, the model fails or the bound is exhausted.
- Review cards explain the exhausted field/count, previous question/answer, known facts and
  consequences. Approval cannot silently authorize later tool calls or lower the recorded risk.

## Implementation map

- Session contracts: `packages/schemas/src/thymira/schemas/session_chat.py`.
- Persistent discussion and retry coordination: `runtime/core/src/thymira/core/session_chat.py`
  and `runtime/state/src/thymira/state/session_discussions.py`.
- Bounded model interpretation and verified context: `runtime/core/src/thymira/core/session_chat_model.py`
  and `runtime/core/src/thymira/core/session_chat_context.py`.
- Additive API and guarded Run dispatch: `apps/api/src/thymira/api/routes/sessions.py`
  and `apps/api/src/thymira/api/routes/session_dispatch.py`.
- Risk question selection, answer recovery and review records: `runtime/core/src/thymira/core/`
  `risk_uncertainty_questions.py`, `risk_answer_publication.py`, `risk_question_records.py`
  and `risk_review_requests.py`.
- Chat and meaningful reviews: `apps/web/src/thymira/web/static/js/views/session.js`,
  `views/session-review.js` and `review-model.js`.

## Executed evidence

| Check | Result |
|---|---|
| `uv run pytest tests/thymira/test_session_discussions.py -q --basetemp=.pytest-tmp/session-chat-final-title` | 23 passed |
| Session/API/core/ledger/LLM compatibility selection | 155 passed; one existing asyncio deprecation warning |
| `uv run pytest tests/thymira/test_conversation_e2e.py -q --basetemp=.pytest-tmp/session-discussion-e2e-final2` | 6 passed |
| `node --test apps/web/tests/*.mjs` | 56 passed |
| `uv run pytest tests/thymira/test_api_contract.py -q --basetemp=.pytest-tmp/session-chat-contract-final` | 35 passed |
| Final risk interview/clarification, composed graph, API and approval/resume concurrency selection | 131 passed; one asyncio deprecation warning |
| `uv run ruff check .` / `uv run ruff format --check .` | Passed; 687 files formatted |
| `uv run python scripts/ty_ratchet.py` | 0 diagnostics; baseline 0 |
| `uv run python scripts/lint_imports.py` | 4 contracts kept, 0 broken |
| Package invariant inventory, Agent Notes, skills and roadmap validators | Passed |
| `uv run yamllint .` | Passed |
| `uv sync --locked --all-groups --all-packages` / `uv lock --check` | Passed; dependency versions unchanged |
| `uv build --all-packages` / `uv run python scripts/check_wheel.py` | Built all packages; wheel inventory passed (12 checked members) |

The compatibility selection comprised `test_session_discussions.py`, `test_api_sessions.py`,
`test_core_conversations.py`, `test_agents_request_ledger.py` and `test_agents_llm.py`.
The final risk selection used `test_risk_clarification.py`, `test_core_risk_interview.py`,
`test_core_graph_adapters.py`, `test_core_composition.py`, `test_api_risk_clarification.py`,
`test_core_approval_concurrency.py` and `test_core_resume_concurrency.py`, with
`--basetemp=.pytest-tmp/session-risk-integrated-final2`.
The latest focused discussion run includes both provider-binding regressions found during review:
an injected provider must match the authorized route, and provider construction must pin the
previously recorded model choice even if settings change in between.
The provider ledger's Python 3.12-compatible TypeVar defaults now declare `typing-extensions`
directly in `runtime/agents/pyproject.toml`, with the corresponding `uv.lock` relationship.

The discussion tests also cover active-Run questions, action routing, clarification, reload,
provider interruption without automatic repeat, exact retries, concurrent sends, foreign project
and session isolation, actor binding, WRITE permissions, usage limits, credential scrubbing,
pending action recovery, provisional-output exclusion and bounded citation selection.
Question-only sessions also acquire a persisted list title under the existing Session publication
lock; no empty-title update can overwrite a concurrent Run association.

The full non-slow suite (`uv run pytest -m 'not slow' -q
--basetemp=.pytest-tmp/session-discussion-fast-final`) initially reported 3,901 passed,
7 skipped, 51 deselected and four failures. The failures were the pinned OpenAPI route table,
the risk-review assertion comparing a proposal instead of its recorded assessment, the context
compaction fixture and the missing-container readiness expectation. Follow-up verification of
their corrections is recorded below; the initial full-suite result is not presented as a pass.

All four initially failing tests were rerun together after correction with
`--basetemp=.pytest-tmp/session-chat-four-regressions-final`: **4 passed**. Their exact node IDs are:

- `test_api_contract.py::test_openapi_surface_matches_the_pinned_route_table`
- `test_core_graph_adapters.py::test_mira_preflight_escalates_repeated_risk_uncertainty_to_human_review`
- `test_thy_context_recovery.py::test_running_graph_keeps_authorized_tool_pairs_intact_across_compaction`
- `test_tools_sandbox_enforcement_matrix_integration.py::test_tool_manager_refuses_an_unavailable_container_without_host_fallback`

The compaction trace recorded 305 remaining tokens against a 300-token fixture budget. The
fixture now permits 320, keeps the same three-turn limit, executes a freshly reviewed tool call
and checks the authorization group across every compaction. The unavailable-container test
expects a refusal before dispatch: no invented process exit code or execution metadata, no
approval/tool-start events, and a runner spy plus workspace marker prove no child/fallback ran.

## Independent review

Sol reviewed persistence, authorization, retry/concurrency and the risk increment independently.
Two session findings were corrected: rejecting an injected provider whose model differs from the
authorized choice, and pinning the selected model during provider construction. Two risk findings
were corrected: applying the same actual-provider check to uncertainty questions, and preventing
an insufficient third answer from generating a fourth question on either the normal or recovery
path. The exhausted question closes, intake does not claim that twelve questions were asked,
and MIRA publishes the exact uncertainty review. Focused regressions exercise these cases.
The reviewer also reproduced the intersection with the per-field limit: three uncertainty
questions on the same field could still enter a generic review through an invalid transition.
That case is corrected and parametrized alongside distinct fields for both ordinary and recovered
answers. The final independent review reports no outstanding findings.

The integration also extracts persisted question projections and review publication/folds into
small modules. Existing class/module size findings remain, but this extraction reduces the measured
RiskInterviewService and MiraSubgraph classes to 722 and 373 lines respectively; it does not add a
second lifecycle authority or change the Gate decision path.

## Browser observation

A disposable local API/web instance used the real persistence and HTTP/UI paths, one completed
test EDA Run and a ScriptedProvider. Sending “que es eso” produced an explanation with an existing
Run citation. Reload retained it. A second question, “¿Se ha entrenado algún modelo?”, produced
another explanation; another reload retained the discussion. Navigating to Runs still showed
exactly one Run. The narrow layout was adjusted after visual inspection so the composer leaves
usable space for the conversation.

Local screenshot: `.pytest-browser/session-discussion-proof.png` (ignored test artifact).
This exercise did not call a live model or execute a new container analysis.
It was repeated after the final provider-route fixes; the saved screenshot comes from that final
run. The disposable servers and browser tab were stopped afterward.

## Limits

Automated models are scripted; no claim is made that arbitrary natural-language intent or risk
questions are always classified correctly. Ask provides an explicit no-execution choice.
Discussion cites selected trace facts and approved summaries; it cannot inspect complete artifact
contents or unlimited history. An explanation is labelled as interpretation, not a new audited
analytical result. Local filesystem persistence is covered; no new distributed execution queue or
database deployment is introduced. Existing running application processes need to load the changed
code before they exhibit this behavior.
