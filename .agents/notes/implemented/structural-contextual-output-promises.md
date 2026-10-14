# Compile contextual output promises before plan approval

## Problem

The first real EDA Run `run_c0dd948eb5b74380993cc014cf8c51b6` completed with verified artifacts.
The exact second turn and retry 1 failed before tool execution. Retry 1
(`run_50afe64853d7444facd1fe80ad77cf5a`) had 136 valid events and an authenticated start approval
at 116. Proposals 125 and 131 selected correct profile paths but omitted those paths from their
self-quoted task instruction; correction 131 also introduced escaped quote characters. Extra
prompt guidance and rejected-field feedback did not make the self-quotation contract reliable.

## Decision

For contextual planning, expose `outputs_to_publish` as the explicit promise to publish new
files, using the existing exact-path and metrics-content requirement types. Keep historical
inputs separately in `artifact_references`, bound to selected prior Run IDs. Before Gate review
or candidate hashing, compile each output unchanged into canonical `required_artifacts` and
append a deterministic publication block to the task instruction. The review therefore covers
the obligations Execute will enforce, even when the initial instruction omits its chosen paths.

Hide contextual legacy `required_artifacts` from the model schema, retain its raw wire values
only to provide the existing single bounded correction, and refuse nonempty legacy values.
The compiler independently refuses them rather than silently dropping output obligations.
Remove the basis/evidence self-quote types and validators: no named production consumer needs
them. Reintroduction needs a named consumer. Persisted approved plans already use canonical
AgentTask requirements and bypass new planning; standalone output wording and requirements
are preserved.

## Alternatives considered

- More prompt repair around self-quotes preserves the demonstrated fragile contract.
- Ignoring invalid requirements or relaxing completion would conceal missing promised files.
- Keeping unused self-quote machinery for hypothetical future consumers would retain complexity
  without a live boundary to protect.
- Adding a general provenance framework is unnecessary; runtime-owned artifact evidence remains
  separate from model-selected publication promises.

## Consequences

Focused offline validation reports 103 passing tests with scoped Ruff/format and typecheck
passing. Regressions inspect the actual pending plan's three output obligations, publication
instruction and Gate snapshot/hash; historical-input-only plans retain no output obligations;
legacy self-quotes remain refused after the bounded retry. Malformed new outputs also stop
with the bounded contextual error before Gate review. Final broad offline validation passes
2,106 tests, with two Windows symbolic-link skips and 67 warnings in 284.56 seconds.

The change remains within THY planning. It introduces no THY/MIRA cross-import, policy change,
approval transfer or artifact-completion relaxation. Strict paths, kinds, metrics and runtime
provenance validation remain in force. The old failed Runs and completed EDA stay unchanged.
The real OpenAI model-only diagnostic probe succeeds with one model request and a valid compiled
candidate/hash. It preserves the exact original second-turn conversation (ordinal 2, context
containing only the completed first Run), uses the production default THY catalog, and proposes
the three actual producer paths. It records zero tool calls, human approvals and Run transitions.
See `diagnostics/historical-artifact-followup/real-structural-plan-probe.json`. This proves the
planning seam only, not execution, audit or a clean two-turn success.

Normal retry 2 (`run_18d9a9bb215b4a7d82d66aa2107983a6`) now completes with approved human
review: 593 valid events and no manifest errors. Its sole planning model selection is at 56;
the exact candidate's manual approval is at 67. Data completes at 93 with three verified new
profile outputs, no missing requirements, unresolved calls or validation failures. Audit 582
passes A18 and all 14 evaluated deterministic controls, with 16 controls not applicable. The
24 model findings remain retained (16 HIGH, 8 MEDIUM, all support unknown); authenticated
nonautomatic findings approval 587 authorizes the exact review context before completion at
591–592. See `diagnostics/historical-artifact-followup/real-structural-continuation-final.json`.

This successful continuation is ordinal 4, with context containing the completed first EDA
and both failed attempts. It is not a fresh clean two-turn acceptance run. The original exact
second-turn probe proves planning only; the failed histories remain preserved. The first EDA
also remains COMPLETED with 693 valid events. Agent Notes and all four import contracts pass
(438 files, 1,804 dependencies). Global `just check` is not claimed: three unrelated existing
TEMP-DEBUG Ruff errors remain outside this change. No claim of unchanged runtime identity is made.
