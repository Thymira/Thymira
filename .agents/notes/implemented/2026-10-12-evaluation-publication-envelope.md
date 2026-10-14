## Problem

The six-stage Session acceptance test completed real training and model evaluation, but THY
rejected the metrics publication. The current-state reproduction failed after 97.46 seconds in
tmp/metrics-publication-integrated-red.log. Independent inspection of its four Runs confirmed
valid event chains and artifact manifests; the successful evaluator's nested artifact ID belongs
to both its completion artifact list and the active manifest.

Tool completions persist the canonical discriminated envelope
`{"kind": "success", "value": {"artifact_id": "...", ...}}`. Task evidence deliberately retains
that envelope. THY's evaluation completion check and the agent evaluation-repair publication
check instead read artifact_id from its outer level. The original focused fixture also fabricated
a flat result, hiding the production mismatch. Two adjacent continuation-test callbacks had the
same obsolete flat read and failed with KeyError before reaching the production guard.

## Decision

Decode only a canonical success envelope with exactly kind/value keys and a schema-valid
ModelEvaluationValue. Share that decoder between the evaluation completion and failed-call repair
consumers in the agent layer; THY imports downward. Keep ToolAttemptEvidence.value and durable
events unchanged. Continue checking task ownership, successful tool status, unique producer,
completion artifact membership, active integrity-verified metrics and normalized plan requirements.

Use canonical_result with typed producer values in the focused fixtures. Preserve their existing
acceptance and rejection scenarios and add invalid-envelope, failure, ownership, invalidation and
duplicate-publication regressions. Update continuation callbacks to the actual envelope, explicitly
requiring success and completion artifact-ID membership before returning the recorded ID.

## Alternatives considered

- Accepting flat and nested payloads was rejected: current producers emit the discriminated
  envelope, and no required legacy consumer was demonstrated. Flat evidence remains rejected.
- Unwrapping every tool value in the task collector was rejected because it would change the
  shared raw-evidence contract beyond the two confirmed evaluation consumers.
- Skipping the publication guard or trusting an agent's metrics ID was rejected because neither
  proves that a successful task-owned evaluator published an active verified artifact.
- Duplicating the decoder was rejected after the repair consumer was identified. Both named
  consumers use the same narrow, typed interpretation in the lower agent layer.

## Consequences

No model output grants authorization. Policy/Gate, credential redaction, hash-chain writing,
artifact identity, plan scope, schemas and budgets are unchanged. The first step's focused
regression went red with the exact publication error and then passed all 21 cases. The repair
regression went red with one failed and three passed cases, then all four passed after the second
change. Both focal modules pass all 73 cases. The final snapshot passed independent read-only
review: no prior assertions were removed, and the raw-evidence and authorization boundaries hold.

Final Session validation passes: 9 slow/integration tests, including the unchanged six-stage
acceptance test, and 228 fast tests with one Windows symlink skip. The related evaluation and
continuation bundle passes 158 tests; the additional non-object envelope boundary passes separately
(one passed, 21 deselected). Independent inspection of the integrated test's four Runs confirms
valid hash chains, clean artifact verification and a successful evaluation publication whose ID
matches both the completion artifact list and active manifest.

Changed-file Ruff and format checks pass, strict workspace ty reports zero diagnostics, and the
four import contracts are kept with zero broken. The canonical repository quality check still
reports three existing TEMP-DEBUG findings outside this follow-up. Combined coverage remains 87%,
below the unchanged 90% global gate; obsolete positions for the two changed production files were
purged from earlier coverage before combining current data. The wider workspace suite was not
rerun in this follow-up. Earlier unrelated failures are not claimed resolved.

The earlier Session lookup report records the historical integrated failure; this follow-up
resolves that failure and its two adjacent continuation regressions. No new real-model smoke run
was performed for this narrow envelope fix; the earlier recorded real PNG and EDA checks remain
the provider evidence. Tests use ScriptedProvider and real runtime/container execution as required
by repository testing rules.

Current-state evidence is kept under tmp/metrics-publication-*. Baseline snapshots for the
follow-up are in tmp/metrics-publication-baseline/; they preserve earlier unrelated workspace work.
The final validation summary is tmp/metrics-publication-validation.md and the isolated review diff
is tmp/metrics-publication.patch. No commit or pull request was created.
