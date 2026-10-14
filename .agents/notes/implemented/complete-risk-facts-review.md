# Keep human review separate from missing risk facts

## Problem

In the isolated real-provider check, profile v13's uncertain assessment was manually approved at
event 139 and resumed at 140. The separate execution classifier at 150 reported high risk,
confidence 0.95, no missing facts and required human review for sensitive attributes. Core treated
that review requirement as missing information and asked again about the declared population at
159. Repeated clarification produced profile v14 at 210; its assessment at 221 and approval
request at 225 correctly required a new answer. The Run remained WAITING at 226. The earlier
answer was not lost and cannot authorize the changed profile.

## Decision

Use the pure `classification_question_fields(risk, profile)` helper to check model-reported gaps
against the actual immutable profile. Normalize only the existing aliases: intended use to
purpose, decision role to decision effect, and deployment context to autonomy. Restrict known
classification questions to claimed, clarifiable fields without a declared value. Reuse
`has_declared_value`, including its distinction between an explicit empty category answer and an
undeclared placeholder. The selector and a persisted selection must remain within those fields.

A known classification with no actual question fields returns the unchanged RiskProfile,
including its reported gaps and `needs_human_review=True`. The execution-start Gate still decides
and requests its own exact review. Unknown level/category and classifier fallback retain the
existing bounded clarification path. Pending questions and exact approval scopes are preserved.

## Alternatives considered

- Clearing the review flag would turn complete facts into permission to execute sensitive work.
- Reusing the earlier assessment approval for a new profile would cross its exact scope.
- Repeating the interview whenever review is required creates unnecessary profile revisions.
- Changing the risk classifier, policy or THY/MIRA boundary is unnecessary for this Core guard.

## Consequences

The initial review-only guard was insufficient: classification event 238 claimed the declared
population, decision effect and intended use were missing. The initial guard trusted those model
claims and asked about decision effect at 247. The same meaning was answered at 257, producing
profile v15 and another MIRA assessment at 273. The final helper addresses that mismatch without
rewriting classification evidence or weakening human review. No imports between THY and MIRA,
RiskProfile contract changes, Gate rules or approval shortcuts are introduced.

After the final helper, the implementation agent reports the complete risk-interview file passing:
34 tests, repeated after formatting in 38.41 seconds. Cases cover false missing claims against
complete facts, aliases, a real oversight gap mixed with false claims, and unknown/fallback
clarification. This change supplies no approval and does not itself prove a completed two-turn
scenario.

Final broad offline validation: 969 passed, 2 Windows symbolic-link capability skips, and
4 deprecation warnings (179.10 seconds). Typecheck ratchet is 0 against baseline 0; the four
import contracts, scoped Ruff/format and Agent Notes pass. In the real scenario, v15 uncertainty
approval is recorded at 275; classification 286 retains review with no missing facts and creates
no new question or profile. The Run is now waiting for execution.start review at 289 with
local_execution_only=true. The two-turn real-provider acceptance criterion remains pending its
ordinary exact human approvals.
