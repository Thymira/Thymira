# Resumable plan review repair plan

## Problem

Run run_9ccf751e4b004f55b8d03dd145d80a58 successfully generated a structured plan at event 283. The Gate required human review at 284 and recorded its request at 285. THY represented this pending review as an error; Core persisted terminal FAILED at 288/289. The policy fallback is correct. See openai-diagnosis.md for the recorded evidence and existing offline characterization tests.

## Decision

Keep an unapproved candidate separate from the executable plan. Persist its exact normalized content inside the existing human.approval_requested event, bind the approval to that request and decision, and park through RunController in PLANNING. Resume precisely that candidate after Core validates the authenticated human answer. No replanning, replacement decision, automatic approval authority, or reuse of execution.start approval.

### THY and Gate

1. runtime/thy/src/thymira/thy/models.py: add a small ThyPlanReview record and optional pending-plan-review fields on ThyState and ThyOutput. It contains the exact normalized candidate PlanOutput, PolicyDecision and the approval-request event reference/hash. Keep ThyProgress for existing tool-call continuations. Do not put the candidate into state.plan before approval. Pending-review state takes precedence over the seeded-plan shortcut; contradictory executable/pending states fail closed.
2. runtime/policies/src/thymira/policies/gate.py: expose optional request details on check_action and forward them to the existing _record(details=...) seam. For plan.proposed, request details contain action_type='plan.proposed', the single normalized candidate snapshot at top-level plan (also rendered by the existing review UI), and a versioned plan_review object containing canonical candidate SHA256, immutable scope fields and execution-scope SHA256. Do not duplicate the candidate inside plan_review. Gate-owned keys override caller details. The existing request event is the single durable snapshot. No new EventType, schemas-wide model or GoalBoard plan.published event is needed. task_count alone is insufficient binding.
3. runtime/thy/src/thymira/thy/nodes/plan.py: normalize candidate before Gate. PASS/WARNING continue normally; REQUIRE_HUMAN_REVIEW returns typed pending review without error and without executing. BLOCK/invalid plans retain refusal/error behavior. Capture the exact request event written by this Gate call, rather than an arbitrary latest pending approval.
4. runtime/thy/src/thymira/thy/graph.py: pending plan ends the graph before the Execute boundary callback. Return the typed incomplete result. Defer missing-deliverable validation only during this pending pass; final completion and existing tool-resume checks remain strict. Do not synthesize a completed response.

### Focused Core helpers and parking

5. Add runtime/core/src/thymira/core/plan_review.py with pure/replay helpers to find the plan review request, reconstruct the candidate, verify canonical SHA256, match decision/run/request/scope, and classify pending/approved/rejected/stale. Keep extended behavior outside the already-large adapters module and _RuntimeGraphNodes class. Preserve or reduce god-class baseline flags.
6. runtime/core/src/thymira/core/graph/state.py carries an optional typed plan review as a checkpoint projection. Events remain authoritative.
7. graph/adapters.py projects pending THY review without raising ThyExecutionError and without recording a finished conversation response. On continuation, call the Core helper, then seed exactly the authorized candidate into ThyState.plan. Never seed an unapproved candidate merely because it is nonempty. Preserve existing tool progress and rework behavior; reject contradictory continuation states.
8. graph/compose.py handles pending-plan review BEFORE its compatibility _begin_execution fallback. Call RunController.park_for_review(..., resume_from='plan') while still PLANNING, then end through the existing persisted WAITING/APPROVAL routing. Only existing tool progress uses _begin_execution and the tool-review park. Redelivery accepts only the same decision/request binding.

### Approval and resume

9. execution_review.py adds a named plan target to the known resume-target set. Reuse human_approval_outcome, requiring authenticated explicitly nonautomatic human events for the exact decision/subject/run. Automatic approver events do not authorize continuation.
10. human_review.py validates the manual human resolver for plan reviews too, and validates candidate/request freshness BEFORE recording an answer. A changed expected decision or binding is a review conflict; preserve WAITING and execute nothing. Approval persists RESUME with the plan target. Rejection uses the existing BLOCK branch and exact policy binding without invoking THY.
11. run_resume.py performs the same binding and approval checks for out-of-band/restarted continuation before RESUME. An unresolved plan remains waiting; rejected plans block; corrupt/stale bindings never execute.
12. dispatch.py maps resume_from='plan' to execution_gate as the checkpoint anchor. Its outgoing edge re-enters THY and skips the execution.start Gate body. The adapter then promotes the authorized candidate; Plan's existing seed shortcut skips model/Gate. Do not route the approved plan straight to MIRA or the final Gate.
13. Check the production lifecycle route too: lifecycle_adapter.py control.apply currently invokes graph(None) directly. If approval continuations use that route, apply the same stopped-checkpoint scheduling/recovery helper there. Do not fix InlineDispatcher alone. Preserve owner-bound writes and CAS/claim authority. Settling successful handling of a parked work item is distinct from completing the Run.

### Binding and recovery

Canonical candidate hash covers normalized PlanOutput task order, IDs, instructions, dependencies, required artifacts, skills and model-work metadata. Scope identifies Run/project IDs, immutable prompt/conversation work, execution-start decision/constraints and policy SHA256; never hash the entire mutable Run.model_dump. At public approval and explicit Run resume, Core recomputes candidate/request/run-work/policy/persisted-constraints binding with the collaborators already available. The production owner consumer validates the current Run work before RESUME and the adapter checks current policy before any task executes. Do not add an approval-time catalog factory. At resumed ThySubgraph entry, before promoting the candidate into executable state.plan, check current selected agent, prompt, output schema, tool and skill availability. Same-name specification or skill byte changes are not fingerprinted by this repair. The persisted scope fields are evidence, not authority. This split uses each layer’s existing dependencies and fails closed before execution.

The Gate writes decision then request; Core writes park then checkpoint projection. From the durable request onward, recover the candidate/request/decision from verified events whenever the checkpoint lags. Do not replan or mint another review for the same pending candidate. A candidate/request without a matching decision cannot execute; an approval without its exact candidate cannot execute. Crash after approval but before checkpoint must recover the same authorized candidate. A material change needs a new review, never continuation under the old approval.

A crash BEFORE the request is durable has no approved or pending plan review. Exactly-once provider behavior at that earlier boundary is not promised by this repair; use the existing request ledger where applicable. No extra candidate snapshot event is necessary.

Do not overload GoalBoard plan.published or create another board/queue authority. THY and MIRA retain their import boundary. MIRA observes shared evidence after execution; no new MIRA control is required for this repair.

## Alternatives considered

A universal plan PASS rule hides the lifecycle defect. Putting unapproved candidates into ThyProgress.plan activates the seeded bypass without authorization. Replanning after approval changes what the human approved. Treating a plan review like findings review resumes at the wrong node. Checkpoint-only state loses the exact review after checkpoint lag. A separate candidate event duplicates the snapshot already persisted atomically in the approval request. Broad approval-framework or MIRA/schema redesign exceeds this repair.

## Consequences

Default policies produce WAITING/APPROVAL in PLANNING. Exact manual approval executes the same candidate and enters execution once. Tool permissions and final artifact checks remain active. Human rejection blocks. Stale review is refused before approval/execution. Optional new checkpoint fields preserve existing tool continuations. Changed plans require distinct reviews.

### Required evidence before claiming completion

- Offline default-policy lifecycle: one candidate; WAITING in PLANNING; no delegated tool work, run.failed or completed conversation response; request contains exact candidate/digests.
- Exact manual approval executes the candidate with no extra planning selection/request or plan.proposed decision. Final output requirements remain enforced.
- Rejection blocks. Wrong Run/subject/decision, execution.start approval, unauthenticated actor and automatic answers cannot release the candidate.
- Changed candidate, policy, execution scope or selected task/skill availability fails closed. Unchanged candidate survives process restart.
- Crash/redelivery after request, park and checkpoint preserves the same binding. Restart after approval but before checkpoint executes through existing task idempotency.
- Preserve direct PASS/WARNING, BLOCK/invalid plans, authorized seeded recipes, tool approval resumes and rework. Missing/invalid final deliverables still fail.
- Verify InlineDispatcher and configured production lifecycle control.apply behavior; a parked pass must not complete the Run.
- Update existing tests that intentionally expect plan-review ERROR/FAILED. Add offline regression before implementing; characterization success alone is not a working pause/resume proof.
- Run targeted THY, Core approval/dispatch/checkpoint/API lifecycle checks, import/type/style gates and god-class comparison, then broader required gates and a real fresh-workspace two-turn scenario.

Skills applied: python-debug, python-general. This design task edited no runtime or test files.
