# Resume the exact plan after human approval

## Problem

A real EDA Run produced a valid plan, but the default policy required human review of
`plan.proposed`. THY represented the pending decision as an error and Core terminally failed the
Run before execution. The policy fallback was correct; the missing behavior was a resumable plan
review. An execution-start approval does not authorize a later plan or its tool calls.

## Decision

Keep the normalized candidate separate from the executable plan. Use the existing Gate-owned
`human.approval_requested` event as the single durable snapshot: its top-level `plan` contains the
candidate, and `plan_review` carries versioned metadata, digests and execution scope. The existing
web review can display that same top-level plan. Caller details cannot override Gate-owned keys.

THY returns a typed pending review without executing, validating final deliverables or publishing
a finished response. Core parks through RunController in PLANNING with `resume_from=plan`.
Core replay helpers validate the exact Run, decision, request, candidate and execution scope,
including an authenticated, explicitly nonautomatic human answer. Events remain authoritative
when the checkpoint lags. Approval resumes the same candidate; rejection blocks. A changed or
corrupt binding cannot execute under the earlier answer.

The stopped-checkpoint continuation anchors at `execution_gate`, whose outgoing edge re-enters
THY without minting another execution-start decision. Before promoting the candidate, the adapter
checks current selected task and skill availability. Inline and production lifecycle continuations
must use the same scheduling rule. Production writes remain bound to the live owner; claims,
version checks and RunController transition authority remain unchanged. A signed resume command
does not itself supply the human plan approval.

## Alternatives considered

- Passing every proposed plan bypasses the intended policy review.
- Seeding an unapproved candidate into the executable plan activates the existing seed shortcut
  without authorization.
- Replanning after approval changes what the human approved and may create another review.
- Checkpoint-only recovery loses the review binding after a durable request and checkpoint lag.
- A second snapshot event duplicates the candidate already persisted in the approval request.
- A new MIRA control or approval framework expands the repair beyond the lifecycle defect.

## Consequences

Pending plan review is incomplete work, not failed or completed work. Exact approval authorizes
only the recorded candidate. Tool authorization, execution constraints, MIRA audit and strict final
artifact checks continue to apply. THY and MIRA retain their independent import boundary.

Focused validation passed: the 80 Plan tests retain pending-candidate, no-execution and automatic
answer restrictions; nine binding and lifecycle tests cover changed candidates, request digests,
Run/work/policy scope, missing Gate decisions, request-before-park recovery, and stopped-checkpoint
scheduling inside the real live-owner context. The new binding tests pass scoped type and style
checks. Agent Notes validate. The scoped god-class comparison adds no class flags or methods;
existing flagged classes remain flagged. The 43 adapter tests pass with explicit plan-block
fixtures preserving genuine error/FAILED coverage; their scoped type and style checks pass.
The stale adapter expectations were updated while retaining explicit BLOCK/error coverage.

After the owner-control precheck, 93 focused Plan, API approval, binding and lifecycle cases pass,
including refusal of changed work while retaining WAITING and publishing no new RESUME. Full
type checking passes without a baseline update; scoped style and formatting checks pass. Plan's
pending/seeded guard was extracted without changing its ordering or authorization semantics.

Availability checks do not attest unchanged bytes of same-name agent specifications or skills.
The production worker validates immutable Run/work scope before RESUME and current policy at
adapter entry before any tool execution; the
public approval path validates policy before recording the answer or RESUME.
The prior real provider scenario failed before execution and never launched its follow-up; it does
not establish a successful two-turn run. A fresh real-provider check requires its ordinary scoped
approvals.

Final broad offline validation: 969 passed, 2 Windows symbolic-link capability skips, and
4 deprecation warnings (179.10 seconds). Typecheck ratchet is 0 against baseline 0; the four
import contracts, scoped Ruff/format and Agent Notes pass. In the real scenario, v15 uncertainty
approval is recorded at 275; classification 286 retains review with no missing facts and creates
no new question or profile. The Run is now waiting for execution.start review at 289 with
local_execution_only=true. The two-turn real-provider acceptance criterion remains pending its
ordinary exact human approvals.
