# Tool-capable structured validation feedback

## Problem

The provider path with callable tools validated a returned output schema, then converted any
`ValidationError` into a generic `ModelRetry`. The next provider request said the output was
invalid but omitted the field and error code, so a model could not repair common failures such as
a missing `CodeResult.exit_code`. The shared feedback helper also copied arbitrary Pydantic error
codes and recognized schema field names globally, which could expose custom validator codes or a
dynamic mapping key that happened to match a field elsewhere in the schema.

## Decision

Reuse `validation_feedback()` in the tool-capable validation catch while retaining its generic
schema prefix and retry boundary. Feedback contains only schema-positioned field paths, at most
eight errors, bounded locations, and allowlisted Pydantic error codes; unknown/custom codes become
`validation_error`. Local JSON Schema traversal preserves nested model fields and sequence
indices, resolves generated local references, and renders every mapping key as `<key>`. Credential
redaction runs before path truncation. Raw provider responses remain separate request-ledger
evidence.

The scripted `CodeResult` repair request now includes the safe excerpt:

```text
Schema validation errors: exit_code: missing
```

The same contract is exercised with the shipped `EvaluationResult` and the no-tool planner path.
Repeated invalid results still exhaust the agent's existing retry budget, settle as `FAILED`, and
charge every returned provider response.

## Alternatives considered

- Keep the generic retry text: rejected because the provider request does not identify the field
  it needs to correct.
- Include Pydantic's complete error records or exception strings: rejected because they can carry
  invalid model values, arbitrary mapping keys, validation context, or custom validator messages.
- Remove or rewrite the raw provider response in the request ledger: rejected because that record
  is independent provenance for what the provider returned; repair feedback and source evidence
  have different purposes.
- Keep one global set of every schema field name: rejected because an input-controlled mapping key
  can collide with a declared field elsewhere and appear as a trusted path.

## Consequences

Tool-capable and structured provider retries now receive comparable bounded field feedback. The
provider response is charged before the existing retry proceeds, and the existing PydanticAI and
AgentRunner limits still determine exhaustion. Unknown schema branches and arbitrary mapping keys
are reported conservatively without their input text.

**Validation and review.** The outgoing tool-capable retry request was inspected. For a missing
`CodeResult.exit_code`, it included:

```text
Validation feedback:
the model returned invalid CodeResult output

Schema validation errors: exit_code: missing

Fix the errors and try again.
```

The corrected `exit_code=0` completed in two provider requests. The original diagnostic passed (1
passed), and the focused selection across validation feedback, tool retries, model binding, the
planner, cumulative turns and provider failures passed 107 tests: 11 new cases and 96 existing
controls. After the test-only explicit-model typing correction, all six helper cases were
independently rechecked and passed; the test construction preserves ten missing fields, eight
reported errors and credential redaction before truncation. The final ty ratchet reported zero
diagnostics against a zero baseline.

The full suite reported 4,130 passed, 78 skipped and 5 failed (4,213 JUnit cases, 61 warnings,
771.45 seconds). Its five failing nodes matched the previously recorded failure set; four
concerned conversation row_count/governance-canary assertions and one was the acceptance-demo
expected-prompt snapshot. The snapshot was left unchanged. Root review accepted the implementation
after verifying the actual request, tool-capable exhaustion and charging, the Pydantic `ErrorType`
code catalog, numeric mapping paths through nested child fields, and the explicit test model.

Production remained frozen during the full suite; the test-only explicit-model fixture correction
occurred during that run and its six helper cases and zero-diagnostic type gate were verified
separately afterward.

The seven task files are `model_binding.py`, `validation_feedback.py`,
`test_agents_validation_feedback.py`, `test_agents_tool_validation_feedback.py`,
`test_thy_plan.py`, `CHANGELOG.md`, and this note. Focused Ruff check/format, source Ruff and
format gates, imports, package/invariant inventory, skill and note validators, roadmap checks,
source YAML checks, offline lock check and `git diff --check` passed. Repository-wide `just check`
still reports 17 pre-existing violations in `.tmp-eda-followup`; repository-wide formatting and
YAML checks also retain the previously recorded fixture/CRLF findings. No paid or live external
model requests, historical run resumptions, deployments, commits or pushes were performed.
