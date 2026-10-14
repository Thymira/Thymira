# EDA preflight, explanations, and plot delivery verification

## Historical incident

- Session: `session_bda1638f9f66414bbb829ea919e02c6b`.
- Title: `Quiero hacer un EDA, muestrame graficas`.
- Run: `run_1c08c45d0bbd43e1ad5f23828b9b2505`.
- The historical event chain verifies: **255 events**. An isolated artifact-store copy
  verifies with no errors: **11 manifest records, 8 active**, including two plot artifacts.
- The API process group began at **2026-09-29 16:28:29 UTC**. Historical model requests
  already contain the previous canonical fact/mitigation correction and updated diagnostic
  wording. The new failure cannot be dismissed as running before those changes.

## Reconstruction

The first two Session discussions are clarifications without `run_id`: the user requested
EDA/graphs, the assistant asked to confirm `german_credit`, the user said `Si`, and the
assistant asked again. The second reply was correctly marked as a continuation. On the third
discussion, `Si, te lo confirmo otra vez`, the model returned `kind=action` with
`continues_clarification=false`. The published Run therefore had an empty clarification chain.
Its current request consisted only of that confirmation; the EDA request was merely untrusted
conversation history. The activity intake's model-visible request lost the EDA scope.

MIRA asked `potential_consequences` questions at sequences **56, 74, 93**. Human answers at
**66, 85, 104** respectively declared no impact for test exploration. The model rejected them
as vague, demanding examples of effects. At **107**, GOV-006 requested review after three
questions for that field, followed by the distinct execution-start review at **112**.
All these are events of the **same Run**. No interview answer became another Session
discussion or Run. The misleading boundary was presentation plus the detached initial request.

The coding task required `eda_plots.png` but registered `data/eda_plots.png` at **242**.
The tool succeeded at **243** and the agent reported the actual registered path. The generated
script saved three different figures to that same path, retaining only the last one. Another
task had already registered the separate multi-panel `data/visualization_output.png`.
At **253–254**, the Run failed the exact required-path check. It did not reach a final MIRA
artifact audit; this was a THY deliverable failure, not a recorded regulatory violation.

Discussion IDs `turn_87bbbd412db4422e8dba8a2472d04c2b` and
`turn_91acf86ac7f14e45980fa7eca7981ca9` then returned `kind=answer`, `sources=[]`.
Their model-visible evidence offered 13 source IDs but omitted artifact creation names.
The answer validator rejected both citationless responses; the generic error obscured why.

## Implemented boundaries

See [the decision note](../.agents/notes/implemented/session-eda-preflight-and-plots.md).
The Session classifier no longer requires confirmation when there is one available dataset.
Natural replies retain their validated pending human request across restart and publication
retry, including failed Auto interpretations. Replacing pending work requires the existing
human-selected Request work mode and a complete replacement request. This is a state binding rather
than phrase matching. MIRA still owns preflight and Policy/Gate still own execution decisions.

The UI renders registered raster plots with released responses and previews them in the
artifact inspector. Preview validation checks scope, active revision, base64, length, raster
signature and SHA-256 when browser crypto is available; the API independently verifies bytes.
Failed Run artifacts are not promoted into approved conversation results. Six plots are shown
inline; the artifact collection remains available for the rest. A local browser fixture with
synthetic data verified both the gallery and inspector visibly render the chart.

## Verification

Focused automated verification uses `ScriptedProvider`; no new real-model EDA is claimed.

- Session clarification, cited discussion, and preflight-boundary tests: **49 passed**.
- Risk interview and API clarification tests: **59 passed**.
- Session/API diagnostics integration before the final scope guard: **47 passed** (overlapping
  the clarification suite; not an additional disjoint count).
- Core graph state/adapters, conversation E2E with scripted models, and parallel Execute:
  **63 passed**, with one `pydantic_graph` event-loop deprecation warning.
- THY Execute, graph, model/plan contract, Coding and Visualization tests: **128 passed**,
  with one `pydantic_graph` deprecation warning. This includes the earlier **68 passed**
  model/plan selection; the counts are not additive.
- Browser-side tests: **63 passed**. The gallery and inspector were also checked visually
  against a local synthetic chart fixture, then the fixture server and tab were closed.
- Ruff check/format passed for the changed core/schema files and the final THY recovery delta
  (seven files checked in the final coordinator pass). `ty check` also passed after that delta.
  The core conversation modules have no size/cohesion flags. THY Execute remains an existing
  oversized module (1,149 lines); its diff against HEAD removes 16 net lines. Recovery and
  task-settlement helpers and the THY models have no detector flags. Agent Notes and
  the package/invariant inventory are valid. Import contracts:
  **4 kept, 0 broken** (386 files, 1,544 dependencies).

The false-replacement regression failed before the code guard and passed afterwards. It verifies
that a model cannot discard the original EDA/no-training instruction, that failure creates no Run,
and that a subsequent valid reply retains the original scope after service reconstruction.

After explicit user authorization, three separate smoke-check calls used the configured
`gpt-4o-mini` provider with a wholly fictitious profile and generic synthetic-data declarations.
The two scoped negative answers were judged sufficient; `I do not know` was judged insufficient.
Usage: **3 requests, 1,774 input tokens, 68 output tokens, 0 tool calls**, reported cost
**USD 0.0003069**. The temporary model evidence chain verified (13 events). This small observation
does not establish universal semantic correctness or constitute an end-to-end EDA.
An earlier attempt using a historical-derived profile was rejected by automatic permission review
before execution; it was not sent. No historical profile or dataset was sent in the authorized check.

Independent focused review closed without blocking findings after fixing model-owned replacement
flags, recovery-ID/text spoofing, and first dispatch of a later correction after a gated pause.
A minor remaining projection limitation is that technical recovery AGENT_MESSAGE records share
MIRA's bounded recent-message window; a long correction sequence can displace older narrative.
This does not bypass the exact deliverable check, policies, or approvals.

Historical evidence has not been rewritten and the backend has not been restarted.

## Plot repair contract

The original exact artifact requirement remains enforced. Execute may append one correction
per completed source task with a missing or invalid required plot. The normal Delegator, tool
manager, policy, approval and usage paths apply. The correction identity and source position
travel in ThyProgress; model-authored PlanOutput cannot set the source-position marker.
The attempt record is code-owned evidence, independent of model-authored instruction text. A free-form
model task ID cannot masquerade as internal recovery. A correction that still fails remains a
missing-deliverable failure. Regression coverage includes a JSON checkpoint roundtrip, exact
review-gated resume, and two corrections where the first parks before the second starts.
The gated test fixtures share the same artifact-store instance between ToolContext and ThyGraph;
separate cached manifests on the same directory had initially invalidated the fixture. This is
checkpoint/reconstruction coverage, not a claim of a full external-process restart. Chart instructions preserve each requested chart (distinct paths or
a combined figure) and prefer PNG for inline preview, retaining explicitly requested SVG exports.

## Code and regression map

- Scope preservation: `runtime/core/src/thymira/core/session_chat_clarifications.py`,
  `session_chat.py`, `session_chat_model.py`, and `tests/thymira/test_session_clarifications.py`.
- Risk declaration sufficiency: `runtime/core/src/thymira/core/risk_interview_model.py`;
  `test_core_risk_interview.py`, `test_risk_clarification.py`, `test_api_risk_clarification.py`.
- Preflight ownership: `tests/thymira/test_session_preflight_boundary.py` exercises the real API
  and repository reconstruction, retaining one Run and the initial discussion.
- Explanation evidence and bounded repair: `session_chat_context.py`, `session_chat.py`,
  `session_chat_model.py`, and `tests/thymira/test_session_discussions.py`.
- Plot generation and repair: `runtime/thy/src/thymira/thy/artifact_recovery.py`,
  `task_settlement.py`, `nodes/execute.py`, `models.py`, `agents/visualization.py`, agent prompts,
  and `test_thy_execute.py`, `test_thy_plan.py`, `test_thy_agent_coding.py`,
  `test_thy_agent_visualization.py`.
- Plot previews and readable output: `apps/web/src/thymira/web/static/js/artifact-image.js`,
  `views/session-plots.js`, `views/artifact-inspector.js`, `views/conversation-output.js`,
  `views/session.js`, `views/session-interview.js`, `views/session-review.js`, and web tests.

Applied skills: python-debug, python-testing, python-testing-unit, python-testing-integration,
python-general, python-god-classes, ruff, ty, check-imports, changelog, computer-use.
