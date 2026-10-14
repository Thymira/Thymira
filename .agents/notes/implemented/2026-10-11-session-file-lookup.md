## Problem

Session file questions could miss existing PNG artifacts, produce a glob without its required
pattern, or exhaust the unchanged token budget by repeatedly sending broad evidence. The reported
Run has a valid 775-event chain and five PNG artifacts with verified bytes, sizes and digests.
The original failed discussion used 12,798 inclusive tokens in two requests, leaving 3,202; the
next request required 7,484 projected tokens (6,844 input + 384 margin + 256 minimum output).
Bounded context omission did not establish that the files were absent.

## Decision

Recognize PNG filenames as plot relevance metadata. Instruct the interpreter to inspect omitted
Run file locations through existing policy-controlled reads, with literal paths and separate glob
patterns. Permit one correction per discussion only when a placeholder copy validates every other
field of an empty-pattern glob. Charge and retain the malformed response, never execute it, bind
the correction to the original model route, and exclude work actions. Other invalid shapes and
unknown usage remain terminal.

Share the lookup protocol between initial and compact systems: extension searches use filename
globs, grep searches literal file contents, listings cover immediate children, and failed Run
status or a later read error cannot erase positive artifact evidence.

After a read, use a reduced read-only schema and prompt with exact server-bound source IDs. Retain
complete selected human messages and restrictions; omit assistant prose and dataset inventory.
Select complete query pages and safe error records, preserving relevant earlier pages only when
the actual prompt and serialized schema fit. If a new page cannot fit even an answer, omit the
whole page from model context, retain its raw server records, and include a complete context-limit
error. Earlier omitted pages and an offset greater than zero never establish a full scan.

A lookup-capable continuation reserves the exact current answer-only input estimate plus the
384-token margin and 256-token minimum output. If continuation plus reserve cannot fit, dispatch
answer-only directly. Recheck actual settled usage and a remaining request slot before each read.
The ceilings remain four reads, six provider requests and 16,000 inclusive tokens; optional citation
repair cannot consume the mandatory final-answer reserve. Reservations are metadata, not usage.
Cache each candidate's exact framed prompt, schema and estimate locally so random frame nonces
cannot change token estimates between selection, reservation and dispatch.
Ordinary event projections retain the verified event owner's Run ID at top level, including when
compaction omits the Run header; payload-supplied Run IDs cannot override this binding.

For a complete simple Spanish or English location question with a literal ASCII extension, render
positive current-read file paths directly from exact visible server citations. Require positive
file rows, matching paths and Run owners, and credential-safe paths. Keep whole rows within 6,000
characters and 12 citations; label pages or subsets without claiming a complete inventory. A
linked clarification, ambiguous or compound question, omitted source, directory or zero-match
scope stays on ordinary QA. Completed history remains available to the model selecting the read
scope and does not itself disable rendering. Unicode transformations cannot change the human
extension or a filename suffix into a different literal extension.

Keep evidence-page selection and omission in session_chat_evidence_pages.py, separate from compact
prompt/schema and token fitting. Existing compaction imports are re-exported. Exact framed error
parsing prevents a quoted error ID in source data from substituting for a complete correction
record, and both initial and compact citation maps match their model-visible source sets.

## Alternatives considered

- Raising token or request ceilings was rejected because repeated broad context caused the failure.
- Partial-page selection was rejected because siblings repeat pagination metadata and a subset
  would misrepresent returned counts or scan completeness.
- Trusting model absence claims or re-running analysis to locate existing files was rejected
  because registered evidence is independently verifiable and new work requires human intent.
- Arbitrary file reads, policy bypasses and automatic findings approval were rejected because the
  normal Tool Manager, artifact release and MIRA/Gate boundaries remain required.

## Consequences

Same-Session/project ownership, verified hash chains, approved audit snapshots, exact artifact
identity/revision digests, invalidation checks, credential scrubbing and fail-closed redaction are
unchanged. Prompt guidance cannot guarantee model compliance or factual correctness. The reported
Run's independent A19 partial-confinement warning remains unchanged.

The pre-task Session baseline passed 93 tests. Independent review added malformed-envelope,
unknown excerpt-source, valid excerpt, complete framed error and visible-citation regressions.
Two auxiliary end-to-end expectations now preserve the reconstructed profiling tool registration
and check the planner's actual trusted evaluate-existing intent line. All five affected cases pass
with their original execution, release and negative authorization assertions retained.
The post-extraction compaction/budget characterization passed 31 tests. Strict ty reports zero
diagnostics and import contracts report four kept, zero broken. Canonical quality still reports
three pre-existing TEMP-DEBUG findings, preserved outside the task's functional scope.
The final-frame visibility fixture uses a smaller test-only framing bound independently of raw
selection sizes; all source-schema, visible-record and strict-truncation assertions remain. Its
focused regression and the complete 16-test evidence-continuity file pass.
The final location helper's 24 tests pass with 94% combined coverage after the mechanical
typed offset binding; public no-history and completed-history location cases also pass.
The measured compaction module fell from 711 lines to 375, with 392 in the evidence-page module;
both detector reports have no flags.
The final rendered fast Session suite passes 228 tests with one Windows symlink-privilege skip.
The rebuilt slow/integrated lane passes eight cases; the integrated case fails before training
completes because the container cannot open its generated script (Errno 5, Input/output error).
A shorter-root serial run completes training and evaluation, exposing a stale callback read of
the successful result envelope. The corrected strict oracle now reaches THY's evaluation
publication check, which still rejects the successful metrics publication. The final serial
integrated test fails; the other eight slow/integration cases pass. Combined coverage is 87%,
below the unchanged 90% gate.

The remaining wider-suite failures reproduced against pre-task source comprise eight fast cases
and one demo slow case. Two prior container fast failures and one audit-worker slow failure pass
after rebuilding the sandbox image to match the host sources; they are not remaining failures.
A transient Docker setup timeout passed unchanged on retry. The six unrelated fast skips require
Windows symlink privileges; the unrelated slow skips include five image quota cases and one
lifecycle case. Evidence is retained in tmp/session-lookup-fast-parallel.log,
tmp/session-lookup-broad-baseline-repro.log, tmp/session-lookup-container-baseline.log,
tmp/session-lookup-slow-baseline-repro.log and tmp/session-lookup-fast-retry.log.

A real EDA execution using the unchanged example overlay produced six verified artifacts,
including two PNGs and analysis.md; its 354-event chain is valid and deterministic audit reports
13 passed, zero failed and 17 not applicable. Release remains pending human review of four HIGH
model-authored A18 findings. A later live EDA attempt accepted the original request and dataset
reply, but waits at GOV-006 in planning after repeated risk questions (117 valid events, no tools
or artifacts). No review was bypassed. Evidence is under tmp/session-lookup-full-live-eda-overlay/
and tmp/session-lookup-final-live-eda/.

Prompt-only real PNG checks produced both a semantic success and a false absence despite valid
chains and budgets. The bounded server renderer addresses that observed contradiction without
claiming general model reliability. The final real location check completed two provider requests
and two globs in 11,145 inclusive tokens, returning five exact PNG paths and Run-bound citations
from the original reported Run in Spanish. A second real execution used two requests and 11,170
tokens to return two existing profile PNGs from a later failed Run, correctly identifying that
different query scope. Chains, artifacts and credential-absence checks passed for both. The
renderer does not select the Run or claim an exhaustive inventory; the model selects an
authorized scope with the recorded conversation. These checks validate the covered location path,
not a release of new analysis. Diagnostic evidence remains preserved under tmp/.

The integrated acceptance test now checks seed 7 against the original tool invocation, the
Experiment's dedicated seed field and the matching MODEL_TRAINED seed, split and estimator
random states. Earlier real training passes that boundary. An earlier evaluation worker failure
was reproduced against pre-task production sources; its evidence remains in
tmp/session-lookup-integrated-evaluation-baseline.log and tmp/ib7/. After rebuilding the sandbox
image, the parallel integrated run instead records experiment.completed sequence 71 and
tool.completed sequence 72 as FAILED: Python cannot open the generated script under /workspace
(Errno 5, Input/output error). Its later callback has no successful experiment to consume.
Evidence is in tmp/session-lookup-session-slow-rebuilt.log and tmp/si18/; the underlying filesystem
error's internal cause is unproven. The shorter-root serial evaluation succeeds and publishes a
metrics artifact, exposing a stale callback lookup: artifact_id belongs to value.value under the
canonical success envelope. The callback now also checks success kind, exact tool-call identity
and membership in the completion's artifact IDs. Existing manifest, provenance, metrics and
training assertions remain intact. Its red evidence is in tmp/session-lookup-integrated-final.log;
the corrected integrated rerun reaches the production publication guard and still fails with
"evaluation metrics id is not a successful task-owned evaluate_model publication". Its final
evidence is in tmp/session-lookup-integrated-closed.log and tmp/io23/. The real evaluator tool
completes successfully, but this is not a green integrated acceptance result. The bridge exposes
typed value.text, while the metrics artifact ID remains in structured evidence; the auxiliary
oracle checks the complete text projection and keeps the matching runtime call, arguments and
artifact-ID membership checks. No production task-completion guard was changed.

Final combined coverage retains the original 90% threshold and reports 87% in
tmp/session-lookup-coverage-final.log. The ordered design, risks, real-provider results and
remaining validation failures are summarized in tmp/session-lookup-validation.md. The review
agent reached its usage limit during the final auxiliary check; the root agent completed that
contract review and rerun. No commit or pull request was created.
