# Repeated risk questions and an unscoped regulation search

Investigation date: 2026-09-29. Product edits remain in the existing Thymira `main`
checkout, preserving preexisting work. No historical Run or Session record was changed.

## Recorded incident

The supplied Run ID identifies `run_5ba3b9bef1ab4c669bde7c3842b5eaa8`, in Session
`session_611aa5ec4d054a5dbe287d315b00bb6d`. Its persisted title is
`En esta sesion quiero hacer un eda, sacame graficas`.

- Sequence 47 classifies the activity as high-risk model development, with no missing fields
  but human review required. Sequence 56 asks for mitigation measures under the
  `potential_consequences` field. The human explicitly describes a test EDA with no effect on
  persons at sequences 66 and 85. Both answers are judged insufficient for not listing
  mitigation measures. Sequences 74 and 93 repeat the same demand in different words.
  The third answer is rejected at sequence 104. The per-field limit then leads to GOV-006
  review at sequence 107, followed by the separate GOV-007 execution-start review.
- The sufficiency requests actually sent to the provider label the generated mitigation
  question as the required fact question. The field is consequences; the generated wording
  has displaced that requirement with a demand for controls.
- Sequence 296 starts `search_regulation` for `euaiact` with query
  `record-keeping and event logging`, `framework=null`, and `k=5`. Sequence 297 records a
  successful lookup returning CREDIT_RISK, GDPR and EU_AI_ACT chunks. The required-evidence
  validator rejects that mixed result for the EU_AI_ACT auditor at sequence 304. Sequence
  305 records terminal `failed`, and sequence 306 records `run.failed`.
- This is a technical audit failure, not a final findings-policy block. No completed MIRA
  model audit or compliance conclusion follows the failed lookup. The conversation's
  diagnostic draft incorrectly called every failed/blocked outcome "blocking findings";
  the draft now asks neutrally about the terminal outcome and distinguishes these cases.

## Historical evidence checks

- `verify_events`: **307 events, valid chain**.
- Copied the original artifact directory to a new OS temporary directory before constructing
  `LocalArtifactStore`. **14 manifest entries; verification returned no errors**.
- Recomputed deterministic controls with the copied store and recorded policy hash, over the
  246-event prefix immediately before the audit control-evaluation record, in `IN_FLIGHT`
  mode: **A19 MEDIUM only**. The full 307-event trace in `FINAL` mode gives the same finding.
  These read-only recomputations are not new persisted audits or model runs.
- The recorded audit prompt includes the declared profile, all six approval references and
  `IN_FLIGHT` context. The earlier context-priority correction was present in this request.
- The running API process was started at **2026-09-29 13:32:03 UTC**. Recorded intake and audit
  prompts contain the previous scope/context corrections. This does not prove that future
  file edits are loaded; the backend must load this increment before another live trial.

## Additional scope limitation

The dataset reply was `El de german_credit`. The recorded interpreter explicitly returned
`continues_clarification=false`, and the Run's `clarification_context` is empty. The prior
EDA request is present only in untrusted discussion history. The existing fallback deliberately
recognizes exact declared dataset names, so it does not repair this natural-language variant.
No phrase-specific dataset rule is added here. This remains a separate intent-binding limitation;
neither successful tests nor a corrected regulation lookup prove it resolved.

## Regulation lookup correction

`runtime/mira/src/thymira/mira/agents/runner.py` fills an absent/null framework only when
`search_regulation` is required by the audit agent specification. This happens before
`ToolManager.execute`, so policy, recorded arguments and the intent digest describe the actual
call. Explicit framework choices remain visible to the independent validator; an explicit GDPR
search by the EU AI Act auditor still fails. Credit-risk auditors retain permitted GDPR searches.
Optional searches and direct uses of the generic tool remain unscoped by default.

The exact recorded query against the packaged corpus reproduced the same exception before the
change. With the default applied, it returns only EU_AI_ACT chunks, including Article 12.
The selected MIRA suites (`test_mira_agents_euaiact.py`, `test_mira_agents_credit_risk.py`,
`test_mira_kb.py`) passed **50 tests**, with one existing event-loop deprecation warning.
Regressions cover null and omitted frameworks, explicit incompatible filters, permitted GDPR,
optional broad queries, direct generic queries, and the recorded argument digest.

Coordinator integration of the runner and agent-dispatch boundaries passed **59 tests**
(`test_mira_runner.py` and `test_mira_agent_dispatch.py`, one existing event-loop warning):

```bash
uv run --no-sync pytest tests/thymira/test_mira_runner.py \
  tests/thymira/test_mira_agent_dispatch.py -q --basetemp=.pytest-search-integration-0929
```

The unchanged UI suite passed **56 tests** after the diagnostic-draft wording correction:
`node --test apps/web/tests/*.mjs`.

## Interview correction and independent review

`risk_interview_model.py` supplies a canonical semantic definition for each existing activity
field. A generated question is now explicitly elicitation context rather than a replacement
requirement. `risk_uncertainty_questions.py` supplies those same definitions to both MIRA and
execution-classification question selection. Consequences describe inherent impact; controls
and mitigation measures are separate concerns. The classification and authorization rules are
unchanged, and a test declaration is not an automatic exemption.

`risk_question_records.py` projects up to three earlier answered questions for the same Run,
field, profile ID and version, excluding the current question. Question, answer and judgment
reason excerpts each have a 1,200-character limit. The follow-up prompt labels these as excerpts
and says omissions are not evidence of absence. Existing stored judgments and publication
recovery remain authoritative; this is additional context, not new stored state.

The regression first failed because the actual provider prompt lacked the canonical field
definition and instead presented mitigation wording as the required fact. Scripted sufficient
responses exercise recording the human answer as a new immutable profile version. This is a
prompt/persistence regression, not a replay of the real model's semantic judgment.

An independent Sol medium review found no actionable issue after the history-bound correction
and final integration. The question projection extraction keeps `RiskInterviewService` at
**706 lines and 25 methods**, compared with the previously recorded **707 lines and 25 methods**.
Its existing size/cohesion debt remains; the final module has 933 lines. The other changed risk
helpers and MIRA runner are below the module/class thresholds.

Ruff lint and format passed for the ten changed Python/test files. Workspace `ty check` passed,
as did the **four import contracts**, Agent Notes validation and package/invariant inventory.

Final risk/API/graph/conversation integration passed **108 tests in 127.30 seconds**, with
one existing `pydantic_graph` event-loop deprecation warning:

```bash
uv run --no-sync pytest tests/thymira/test_core_risk_interview.py \
  tests/thymira/test_risk_clarification.py tests/thymira/test_api_risk_clarification.py \
  tests/thymira/test_core_graph_adapters.py tests/thymira/test_conversation_e2e.py \
  -q --tb=short --basetemp=.pytest-risk-facts-integrated-0929
```

An intermediate integration exposed a missing import from the question-count extraction;
the alias was restored. Three new-test fixture/assertion mistakes were also corrected before
the final pass: the reproduction must actually request review, wrapped system-prompt whitespace
is immaterial, and a truncation assertion needs a distinct omitted-tail marker. Earlier focused
runs overlap this selection and are not additional unique passing tests. The entire workspace
test suite was not rerun. `git diff --check` passed, with existing CRLF conversion notices.

Skills applied: python-debug, python-testing, python-testing-unit, python-testing-integration,
python-general, python-god-classes, check-imports, ruff, ty, changelog.

## Verification boundary

Automated checks use scripted providers, with real local persistence where applicable. The
original Run remains terminal and is not resumed or rewritten. No new live-model EDA,
Docker/WSL installation, push, merge or deployment is part of this investigation.
Insufficient-answer follow-ups still rely on model judgment and the existing question limits;
they do not acquire a new structured no-useful-follow-up exit. The separate uncertainty-question
selector already supports a persisted null selection. No claim is made that all possible
semantic repetitions or natural-language intent errors are eliminated.
