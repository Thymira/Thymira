# Give contextual planners actionable output-origin repair guidance

This note records an intermediate repair. Real retry 1 showed that self-quoted output origins
remain unreliable despite the operational guidance. The structural output-promise replacement
is recorded in `structural-contextual-output-promises.md`; this note's offline results do not
establish real success.

## Problem

The real first EDA Run `run_c0dd948eb5b74380993cc014cf8c51b6` completed with 693 valid events,
verified artifacts and A18 PASSED. Its exact second message created
`run_7781e29e9fd3456dbe1d8ddd63ead41e`, which failed at planning after 72 events. Proposal 61
used a generic human request as evidence for an invented output path; correction 67 repeated
the invalid basis and invented a profile PNG path. The validator correctly refused both before
tool execution, but its feedback lacked the rejected fields and a concrete repair example.

## Decision

Explain that a planner-chosen deliverable needs a concrete task instruction promising to
publish the exact path, with an exact `task_derived` quote. Human request and clarification
bases still require exact human quotes containing the path. Historical artifacts remain
references; logical dataset names remain inputs. The one bounded retry receives the rejected
task, output, basis, evidence and reason inside an untrusted frame capped at 8,000 characters.
It must preserve outputs still promised by the corrected task.

Share deterministic profile path templates and the minimum correlation-column count between
producer and guidance. Explain the conditional correlation chart rather than promising an
unavailable PNG. Preserve standalone planning wording, exact origin validation, runtime
provenance ownership, Gate decisions and strict artifact completion.

## Alternatives considered

- Silently dropping invalid deliverables would hide a promised-output failure.
- Accepting a generic human quote would misattribute planner-selected paths to the user.
- Adding retries or weakening artifact completion would mask the guidance gap.
- Hardcoding example-dataset paths would drift from the deterministic producer.

## Consequences

Two focused regressions cover initial operational guidance and a rejected proposal corrected
to a task-derived publication promise while retaining its prior-run reference. Focused offline
validation passes 161 tests (43 warnings, 13.11 seconds); scoped Ruff, zero-baseline typecheck
and all four import contracts pass. No new real continuation success is claimed.

The diagnostic runner can retry the same follow-up using `--followup-attempt N` with an existing
`--resume-root`. Stable client identities permit idempotent recovery; prior attempts must exist
and be terminal, and the first EDA must be completed. Canonical request hashes, Run and Session
identity, ordinal and selected context are checked. The normal API chooses history, so retry 1
is a third turn including the completed first and failed second turns, not a clean two-turn
acceptance result. No approval is prefilled and neither terminal Run is reopened.

Recovery hashes the unchanged canonical immutable request fields without normalizing or
discarding message and conversation metadata. Diagnostic fixtures must decode the recorded
UTF-8 files explicitly, matching the actual API and event-log readers.

The offline recovery check against saved authenticated API summaries returns the existing
Session, completed first EDA and no retry-1 Run yet. A changed immutable message is rejected
by the canonical identity guard; the check makes zero network calls and changes no Run history.
Negative attempt and retry-without-root CLI guards refuse before startup; help and compilation
pass. See `.tmp/contextual-repair-recovery-final.txt` and the corresponding CLI guard outputs.

Final broad offline validation passes 2,101 tests, with two Windows symbolic-link skips and
67 warnings in 227.50 seconds. The separately collected MIRA runner passes 61 tests. All four
import contracts remain kept, and the typecheck has zero diagnostics.

Real retry 1 (`run_50afe64853d7444facd1fe80ad77cf5a`) subsequently failed before tool execution
after authenticated start approval 116. Its 136-event chain and artifact manifest verify.
Proposals 125 and 131 still lacked output paths in the task instruction; the second also used
escaped quote characters in its evidence. The operational guidance alone is insufficient for
this provider. No successful real continuation is claimed; the structural replacement is
documented separately.
