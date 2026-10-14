# Keep EDA intake, evidence explanations, and plot delivery coherent

## Problem

Session `session_bda1638f9f66414bbb829ea919e02c6b` contains one Run, not a Run per
interview answer. However, a false model continuation flag detached a natural confirmation
from the original EDA request. MIRA then judged scoped negative declarations as incomplete.
THY registered `data/eda_plots.png` while its plan required `eda_plots.png`; the exact completion
check failed. Two subsequent explanations omitted all citations and were correctly rejected.
The session inspector offered binary downloads instead of raster previews.

## Decision

- Bind an adjacent, complete, same-actor Auto clarification chain by default; require the
  human-selected Request work mode to replace it. A model replacement signal fails closed
  and requests that explicit choice; failed same-actor Auto interpretations preserve the chain. Preserve current-message
  scope, Ask boundaries, chain size limits, immutable publication, and retry identities.
  Assistant suggestions are never copied into the requested work. The classifier uses a
  unique available dataset without an extra confirmation and leaves risk intake to MIRA.
- Clarify that sufficiency judges whether a human supplied a scoped declaration, including
  a negative or contradictory one. Risk assessment remains independent. This semantic
  instruction is not a deterministic guarantee about future model answers.
- Keep MIRA question and answer events in the existing Run preflight. Label that boundary
  in the chat and state what accepting unresolved facts does at the interview-limit review.
- Render bounded raster previews from the existing scoped, digest-verified artifact API.
  Only released responses receive inline plots; failed Run artifacts remain inspectable
  evidence. Reject withheld, redacted, invalid, mismatched, and non-raster projections.
- Keep the exact artifact completion check. A missing required plot may get one correction
  task per original plan task through the existing Delegator/ToolManager/Gate. Its code-owned
  source-position marker persists in ThyProgress; PlanOutput rejects that marker from the model.
  A model-authored ID prefix cannot identify internal work. Failed correction remains a failure.
- Keep the read-only citation requirement. Use
  bounded, recorded corrective paths rather than silently accepting missing deliverables
  or uncited model claims. See the verification report for implementation and results.

## Alternatives considered

- Matching acknowledgement phrases or dataset-name substrings would be language-specific
  and would miss other natural replies. Binding the persisted pending request avoids that.
- Treating a confirmation as a new standalone request loses the task and its restrictions.
- Accepting a basename match would hide different paths or ambiguous files. Removing the
  deliverable requirement would hide a failed output contract.
- Publishing failed Run images as approved chat results would violate the release boundary.
- Removing interview or citation gates would conceal errors instead of making them recoverable.

## Consequences

Existing Run IDs, event envelopes, terminal outcomes, policies and approvals remain unchanged.
The replacement signal is private model interpretation and cannot authorize a scope reset.
Model semantics still require evaluation; an accepted declaration does not prove zero risk.
Historical sessions and artifacts are read only, with artifact verification performed on an
isolated copy. The backend must load the changed Python modules before live use.
