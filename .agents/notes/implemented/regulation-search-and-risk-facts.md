# Keep required searches scoped and interview facts stable

## Problem

Run `run_5ba3b9bef1ab4c669bde7c3842b5eaa8` failed when the EU AI Act auditor omitted the
regulation framework, obtained a mixed-framework search result and failed its required-evidence
check. Earlier, a generated question requested mitigation measures for `potential_consequences`.
The answer evaluator treated that question as the required fact and repeatedly rejected scoped
answers about the absence of effects on persons.

## Decision

Default only absent/null framework arguments on mandatory regulation lookups to the framework
declared by the audit specification. Apply this before Tool Manager validation, policy and event
recording. Preserve explicit filters, generic/optional searches and the independent required-tool
and citation validators. Do not silently filter or relabel a completed result.

Give each existing interview field a stable semantic definition. Supply that definition separately
from the generated human-facing question to question generation, uncertainty selection, sufficiency
judgment and follow-up phrasing. Generated questions cannot redefine the fact or turn inherent
consequences into a requirement for mitigation controls. Existing routing, judgment persistence,
question bounds and human/policy review remain authoritative. The follow-up prompt receives bounded
previous answers for the same field rather than only the last answer.

Make the UI's diagnostic discussion draft neutral about the terminal outcome, separating technical
failure, policy block and findings without automatically sending a message or starting a Run.

## Alternatives considered

- Relaxing the mandatory-framework validator would accept evidence outside the auditor's scope.
- Post-filtering a recorded result would misrepresent the evidence returned by the tool.
- Defaulting every search would break intentionally broad searches by optional audit agents.
- A prompt-only search instruction would leave an omitted filter able to reproduce the failure.
- Treating any claim that work is a test as sufficient or low risk would bypass substantive
  fact evaluation and the existing governance rules.
- Matching particular dataset names, Spanish phrases or mitigation-question wording would not
  correct the general confusion between a required fact and generated elicitation text.
- A new interview termination protocol would require separate persisted recovery semantics;
  this increment keeps the existing bounded review path.

## Consequences

The lookup fix is deterministic for absent/null frameworks and does not weaken evidence validation.
Question interpretation remains model-dependent; scripted tests verify the actual supplied context
and persistence, not the accuracy of a future live model response. Insufficient-answer follow-ups
still use the existing per-field/total limits; they do not gain a new no-useful-question exit.
The separate natural-language dataset-reply binding limitation remains documented in
`docs/session-regulation-search-verification.md`. Historical traces, artifacts and terminal outcomes
are preserved. The backend must load the changed source before new work can exercise it.
