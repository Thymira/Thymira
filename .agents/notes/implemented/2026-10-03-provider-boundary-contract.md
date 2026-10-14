# Provider boundary contract

## Problem

Provider failures were collapsed into generic terminal evidence even when safe HTTP diagnostics
were available, while retaining a remote exception as a cause could expose its body through a
traceback. Historical BadRequestError runs retained request/schema evidence but not the exact
rejected parameter or the remote body's diagnosis, so the suspected schema incompatibility remains
an inference. A structured-output capability refusal raised before dispatch also entered the model
retry loop. Finally, returned malformed tool-call JSON was persisted as a parse failure but its
response usage reservation was cancelled, so the agent classified a repairable response as a
provider error without charging it.

## Decision

Extract only allowlisted status, fixed provider error codes, validated parameter paths beneath
known API roots, and provider-style request IDs from direct exception attributes and known
response/header/body fields. Scrub known credentials before validation and length checks; discard
overlong values rather than truncating them. Request IDs must match a `req-`/`req_` provider form
or UUID shape. Carry this small diagnostic object on the in-memory `LLMCallError`;
`failure_outcome` revalidates it before adding a bounded summary to task and completion evidence.
Gateway exceptions are raised without an observable raw cause, and generic provider
classification remains when no safe fields survive.

Treat response-less structured-output capability errors as pre-dispatch failures: cancel their
reservation and terminate once. Treat malformed JSON or non-object tool arguments as returned
responses: keep their existing parse-failure ledger record and digest, settle the response usage,
and use the existing bounded PydanticAI retry with a fixed repair message. No tool is called for
the malformed arguments. The real Coding catalog's `run_python`, file and `final_result` tools stay
Chat Completions non-strict; the captured request confirms no `strict` field is added. Structured-
only requests continue passing the Pydantic model class to LiteLLM; an offline test separately
verifies the installed LiteLLM serializer's strict JSON Schema conversion for `CodeResult`,
`EvaluationResult`, `PlanOutput` and `FindingVerdict`. The request ledger's local schema
representation is evidence of the adapter argument, not a claim about final HTTP serialization.

## Alternatives considered

Persisting provider bodies, `str(exception)`, arbitrary response text, or traceback causes would
make useful diagnostics depend on untrusted remote content and risk credential disclosure. A new
persisted diagnostic schema would add event-contract surface when an in-memory validated summary
is sufficient. Retrying a refusal that occurs before gateway dispatch cannot correct a request and
would spend the existing retry budget needlessly. Treating returned malformed JSON as a transport
failure would discard returned-response usage and misclassify its existing parse evidence. A tool
schema sanitizer or gateway migration would broaden this boundary change beyond the supported
LiteLLM contract.

## Consequences

Remote failures can retain a short safe diagnosis while omitting arbitrary body/message content.
Pre-dispatch capability failures terminate without repeated dispatch, and malformed returned tool
arguments receive only the existing bounded repair budget with every response charged. Offline
tests exercise the actual pinned LiteLLM serializer, representative real catalog schemas, verifier
consumer behavior for `true`, `false`, `null` and missing verdicts, and late output-reuse validation
for kind, digest and stored-byte mismatches. The fake requests select `gpt-test`/`test-model` and establish the local adapter and publication
contracts; they do not establish acceptance by the configured remote service. No paid smoke call
was run. Existing route policy, Gate, source validation, output argument validation and
publication implementation remain unchanged.

### Provider contract references and remaining uncertainty

The official [OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)
distinguishes Chat Completions' non-strict default from strict function schemas. The
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
requires closed objects and required properties in strict mode, with nullable unions preserving
optional values. [LiteLLM's structured-output documentation](https://docs.litellm.ai/docs/completion/json_mode)
describes its Pydantic-model response_format and capability check. The installed serializer, rather
than these documents alone, is the offline evidence for the pinned dependency behavior. No global
schema sanitizer, mode migration, route-policy widening or model change was introduced.

The historical five early route refusals demonstrate a disagreement between the selected route
and the pinned session allowlist, including an unconfigured route. Their historical origin remains
unknown. Existing route-policy tests check precise pre-dispatch denial and configured policy
snapshots; the catalog boundary test explicitly permits its synthetic route and reaches the fake
gateway. These local configuration refusals are separate from the historical remote BadRequestError.

With separate paid-call authorization, the minimal acceptance smoke is one bounded synthetic call
per supported mode against the actual permitted route: a Chat Completions request using the current
Coding catalog and a structured-only FindingVerdict request. Use no private data, a small output-token
budget, and direct adapter calls that never execute returned tools. Capture the credential-scrubbed
request/schema and bounded failure fields through the existing ledger; check schema acceptance and
local parsing, including a nullable verdict. A live success would establish acceptance for that
route/model/mode at that time, not recover the missing historical rejection diagnosis.

Implementation used the actual subagent selector `gpt-6-luna` with `xhigh`. Fast was not exposed,
and the principal agent's model/reasoning selector could not be inspected or changed with the
available tools; no verified principal-model selection is claimed. Independent review follows
implementation completion with the implementer inactive.

### Independent review and local validation

The principal reviewed the incremental diffs against preserved working-tree baselines with the
Luna implementer inactive. The review accepted terminal pre-dispatch refusal, returned-response
charging and bounded repair, consumer revalidation of safe provider diagnostics, and the mode-
specific schema checks. It first reproduced and then required correction of token-shaped arbitrary
request IDs shadowing a safe header. No tools, MIRA, route-policy or authorization implementation
was changed. All 163 frozen tools/MIRA source and original diagnostic files matched their baseline
hashes; the five changed production files were hash-frozen before final validation.

The first consolidated run had 268 passing and two failing existing controls. One returned-invalid-
response fixture omitted its response, which now correctly means pre-dispatch refusal; it was
adapted to carry a measured response and prove exactly one charge. The other required an observable
raw remote cause, contrary to the safe boundary; it now checks suppressed traceback content while
retaining a single gateway attempt and the ledger's provider_error outcome, code and digest. These
changes preserve their behavioral purpose rather than relax a failed product contract.

The final consolidated command uses `uv run --offline --no-sync pytest -q` with
`UV_OFFLINE=1`, `UV_NO_SYNC=1`, `THYMIRA_SNAPSHOT=replay` and
`LITELLM_LOCAL_MODEL_COST_MAP=True`, a unique external temporary directory, and JUnit output.
The 19 selected modules cover provider contracts, gateway errors, model binding, ledger, validation
feedback, route policy, Python source/publication, approval, cumulative requests and budgets.
Result: **270 passed, one existing deprecation warning**, in 43.50 seconds; evidence is
`.tmp-provider-review/root-final-focused-green.{log,xml}`. This is 19 new parameterized cases and
251 existing controls, counted once; the existing catalog-schema control was expanded and two
existing boundary controls were adapted. New cases comprise six diagnostic cases, five provider
failure/repair cases, one serializer contract, four FindingVerdict cases and three late-publication
reuse mismatches. Earlier baseline, red and repeated executions are not added to this total.

Final source checks passed: `ruff check` and `ruff format --check` over packages/runtime/apps/
adapters/tests/scripts/.agents/skills (747 formatted files), `scripts/ty_ratchet.py --strict`
(zero diagnostics), `scripts/lint_imports.py` (four kept, zero broken), source YAML, package
invariants, 16 valid skills, agreeing roadmaps, and `uv lock --offline --check` (251 packages).
The latter invariant/skill/roadmap/lock checks were run earlier in this work and are not claimed as
repeated runs. `git diff --check` passed over the full working tree. Final note validation passed after recording complete-suite evidence.

The literal `just check` was attempted in Git Bash with offline/no-sync variables and `--no-dotenv`;
it stops at global Ruff: 156 findings in temporary evidence (17 .tmp-eda-followup, 60
.tmp-publication-implementation, 70 .tmp-retries-review and nine .tmp-provider-review fixtures
created during this work). A separate global format check reports 42 temporary files requiring
formatting; global YAML reports 1,895 files, primarily CRLF fixture configs in historical temporary
trees and diagnostics. Source-only checks are green. Evidence was neither deleted nor altered,
and no global exclusion, baseline relaxation or policy exception was added. The global gate remains
pending. Logs are `.tmp-provider-review/root-{just-check,global-format,global-yaml}.log`.

### Changed files

Production: `runtime/agents/src/thymira/agents/llm/base.py`,
`runtime/agents/src/thymira/agents/llm/litellm_provider.py`, new
`runtime/agents/src/thymira/agents/llm/provider_diagnostics.py`,
`runtime/agents/src/thymira/agents/model_binding.py`, and
`runtime/agents/src/thymira/agents/failure_outcome.py`. The helper has two named consumers: the
LiteLLM exception adapter and terminal failure extraction. Its diagnostic is in memory; no persisted
schema/event contract was added.

Tests: new `test_agents_provider_contract.py` and `test_agents_provider_diagnostics.py`, expanded
`test_agents_provider_failure.py`, `test_mira_verification.py` and
`test_tools_python_output_manifest.py`, and adapted existing `test_agents_model_binding.py` and
`test_agents_request_ledger.py`, all under `tests/thymira/`. Documentation changes are this note
and the `[Unreleased]` provider-boundary entry in `CHANGELOG.md`. Pre-existing working-tree
changes are preserved and are not included as implementation work for prompt 06.

### Complete serial suite and final review outcome

The complete serial `uv run --offline --no-sync pytest -q` run, using the same four offline/replay
environment variables, a fresh external basetemp and JUnit output, finished with **4,205 passed,
five failed, 78 skipped and 61 warnings** in 822.96 seconds (13:42). Logs and JUnit are
`.tmp-provider-review/root-final-all.{log,xml}`. Its 4,288 collected cases include the 19 added cases;
this is a complete rerun, not the earlier historical suite with a count adjusted arithmetically.
Production and test hashes remained unchanged throughout the run.

All five failed node IDs match `.tmp-publication-review/root-final-all.xml` (the previous work
05 suite, not reexecuted here), and the same failing assertions are present in both reports:

- `test_acceptance_demo_run.py::test_demo_run_records_what_the_model_saw_and_what_it_left_behind`: unchanged
  `experiment.system-prompt.expected.md` drift; replay mode did not refresh the snapshot.
- `test_conversation_e2e.py::test_session_upload_reaches_real_eda_tool_and_survives_reconstruction`:
  `row_count` missing from follow-up planner context.
- `test_conversation_e2e.py::test_final_governance_controls_response_visibility_and_followup_context[WARNING-None]`:
  `governance-canary` missing from the response.
- `test_conversation_e2e.py::test_final_governance_controls_response_visibility_and_followup_context[REQUIRE_HUMAN_REVIEW-approve]`:
  `governance-canary` missing from follow-up context.
- `test_conversation_e2e.py::test_real_agent_response_reaches_followup_provider_after_restart`:
  expected `row_count:27` missing from the persisted response.

The machine-readable comparison is `.tmp-provider-review/full-failure-comparison.json`. These
failures remain outside prompt 06, with conversation continuity and snapshot acceptance reserved
for their own requested work. Their persistence does not establish sufficient conversation context
or a green complete gate. The principal's independent review accepts this bounded provider-boundary
implementation with Luna inactive; broader stabilization, the global gate and remote acceptance
remain pending. No paid provider call, commit, push, deployment or existing-server restart was
performed.
