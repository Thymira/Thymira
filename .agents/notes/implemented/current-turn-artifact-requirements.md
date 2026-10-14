# Keep follow-up artifact requirements within the current request

## Problem

A follow-up asking for further dataset analysis received exact artifact requirements copied from
an earlier Run. The current profile tool produced a profile and PNG plots under different names;
completion correctly reported the planner's unmet filenames. The defect originated in planning,
not in tool publication or completion. Separately, constant numeric columns produced undefined
correlations that were serialized as NaN and rejected by strict JSON consumers.

## Decision

Use a planning-only contextual subtype to distinguish requested outputs from artifact references.
Each contextual output requirement declares a basis (`current_request`, `clarification` or
`task_derived`) and an exact quote validated against its source text. Separate artifact references
carry a name and a source Run ID from the selected conversation context; that membership does not
claim that the referenced bytes have been verified. Strip planning-only fields when compiling the
proposal into execution-owned `AgentTask` records.

Keep request framing, contextual proposal validation, compilation and the bounded model-call seam
in `planning_context.py`; the Plan node retains graph state, catalog wiring and the existing Gate.

Give the planner the current human message and its linked clarifications separately from bounded,
untrusted conversation history. Identify new deliverables as current requests, linked
clarifications, or outputs explicitly promised by a newly planned task. Historical artifacts
remain useful references. Validate contextual source spans structurally and
allow one bounded planning correction for an ambiguous legacy requirement. Do not silently discard
an ambiguous proposal or reinterpret a historical artifact as authorization.

Keep standalone requests and seeded resumes compatible. Preserve completion checks, artifact
publication, the Policy Engine, Gate, and the THY/MIRA boundary. Normalize undefined correlation
values to null at the profile producer, independently of the planning change.

## Alternatives considered

- Renaming or duplicating profile outputs to satisfy inherited filenames repairs the wrong layer
  and would make old user requests control unrelated future Runs.
- Dropping every requirement on a follow-up loses current explicit deliverables and intended
  continuation requests.
- Prompt guidance alone cannot reject malformed or ambiguous planning contracts.
- Literal filename matching in the current message confuses quoted references with requests and
  cannot represent indirect output requests in arbitrary languages.
- A general semantic validator or new cross-orchestrator state introduces a larger mechanism
  without independent evidence that it can reliably establish user intent.

## Consequences

The contract makes the planner's interpretation explicit and catches missing or structurally
invalid request anchors. A matching span proves the referenced text exists; it does not prove
the planner understood its semantics. Human authorization remains a separate deterministic Gate
decision. No artifact requirement is satisfied merely by changing its description or provenance.
The recorded follow-up's MIRA audit passed despite the semantic planning error and non-standard
profile JSON. This change adds no independent MIRA invariant for either defect; existing strict
completion correctly exposed the unmet deliverables and remains unchanged.

Scripted-provider regressions must demonstrate the inherited requirement failure and preserve
current filename requests, metrics, indirect requests, standalone planning and seeded resumes.
The NaN regression must prove strict JSON serialization. A manual real-provider check runs two
conversation turns over a copied German Credit dataset in a fresh temporary workspace, inspects
each exact approval scope, and verifies canonical event chains, manifests and terminal audit-policy
bindings. It stays outside pytest because it spends real provider budget.
