# Verified model continuity and analytical completion

## Problem

A Session follow-up lost the trained model's logical name and identity. THY retrained for a
metrics request; an accuracy-only JSON could not satisfy precision, recall, F1 and ROC-AUC.
The final failure named a planner-invented path, obscuring the analytical shortfall.

## Decision

Core freezes bounded ModelInput records from approved conversation results and verified source
events. Source Run/model IDs, artifact hashes, dataset/schema and training-event identity are
separate fields. The newest approved source is the default; multiple candidates or contradictory
explicit selectors fail with a clarification diagnostic. Failed and unaudited Runs do not become
reusable inputs. A logical name is not a physical path, and equal bytes are not equal artifact IDs.

After the plan boundary, Core imports only selected IDs into the consuming Run. It rechecks
Session/project/snapshot membership, source approval, current registration and bytes without
opening the historical LocalArtifactStore. Publication uses one batch with current-Run lineage,
Core ownership and explicit source identity events. Replay validates ownership, lineage and bytes.

SessionChat and THY carry general/evaluate_existing/train_new as a persisted work proposal.
Evaluation normalizes to a dedicated specialist with only evaluate_model. A prior-model request
cannot become training solely because either LLM proposes train_new: a conservative positive
English/Spanish human training/building guard is also required. Unknown wording clarifies.
This semantic guard is not universal language understanding and never replaces the Gate.

Metrics requirements distinguish explicit exact paths from planner-selected content requirements.
Content completion checks finite requested numbers, source Run/model identity, model/data hashes,
methodology and verified artifact bytes. Requirements for one result are merged without dropping
metric keys. Unsupported additional deliverables produce an explicit error, not silent removal.
Existing exact-path checks remain in force; no basename fallback was added. Evaluation prose
is persisted as a REPORT so independent MIRA lineage/fidelity checks can inspect it, just as
with the existing training summary path.

The default holdout evaluator only supports captured default-training provenance. Arbitrary custom
splits/models cannot be represented as a reproduced holdout. all_rows is an explicit tool choice,
not a fallback, and cannot satisfy a recorded_holdout requirement. Undefined ROC-AUC remains
unavailable and a required numeric ROC-AUC prevents successful completion.

## Alternatives considered

- Adding glob/read_file to SessionChat would not establish identity, scope or authority.
- Reusing run_experiment would silently replace the model and measure a different scientific task.
- Accepting a matching basename or JSON media type would hide absent requested metrics.
- Importing every historical model during graph construction needlessly affects unrelated work.
- A new cross-Run store abstraction needs a named consumer beyond this bounded continuity path;
  source reading and publication stay local to Core.
- Retrying every missing artifact is not a content or methodology contract.

## Consequences

Old persisted records default to general and empty inputs. Standalone Runs remain supported.
Source evidence remains read-only. Copied model bytes are never deserialized in Core; evaluation
uses the existing sandbox and Tool Manager/Gate. No policies or MIRA controls are bypassed.
Late source mutation, invalidation or missing provenance fails closed. Context is bounded to
sixteen model references; explicit references outside that snapshot require a different request.

The extraction of artifact delivery and result reporting reduces responsibilities in THY graph
and summary code. Catalog helpers were extracted from the oversized Core adapter module; its
pre-existing MiraSubgraph/size flags remain. The conversation loader was extracted from the
flagged workspace class. See the linked verification report for measured checks and limitations.

Verification:

See [model continuity verification](../../../docs/session-model-continuity-verification.md).
