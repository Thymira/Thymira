# Verify undefined correlations using their strict JSON representation

## Problem

The real EDA Run `run_758b895f368d43b3ac38b705c40b4181` produced a valid strict-JSON profile
and two valid PNG artifacts. A18 independently recomputed 55 undefined correlation cells,
including cells involving `duration_months`, which has four null values. Its numeric matcher
rejected the producer's JSON null representation and accepted a raw numeric NaN instead.
A18 was the only CRITICAL finding; the recorded Run closed BLOCKED before any follow-up.

## Decision

At the correlation comparison only, require the claim to be `None` when the independently
recomputed Pearson correlation is NaN. Reject every other representation of an undefined
correlation. Keep finite comparisons, tolerances and the shared numeric matcher unchanged.
MIRA continues to recompute from the registered dataset and schema without importing THY
or the profile producer.

## Alternatives considered

- Accepting all missing or non-finite values would hide incorrect finite correlations.
- Changing the shared matcher would broaden unrelated statistical checks.
- Lowering A18 severity or overriding the terminal decision would conceal the verification bug.

## Consequences

The implementation's focused offline validation reports 53 passing tests, including adversarial
undefined representations and finite-value checks. Read-only reverification of the recorded
profile changes A18 from FAILED to PASSED; the event chain remains valid and artifact verification
reports no errors before or after. See
`diagnostics/historical-artifact-followup/real-profile-reverification.json`.

The existing terminal Run remains BLOCKED and its history is unchanged. A separate Core defect
mixes MIRA regulation lookups into descriptive execution scope; this change supplies no approval,
does not repair that attribution, and does not demonstrate a completed fresh two-turn scenario.
