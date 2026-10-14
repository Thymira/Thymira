# Python output publication and cumulative request budgets

## Problem

In run_4ed45afb6a384bbab1be24695c396b66, Python successfully wrote 17 PNGs, but
model-authored output_artifacts also named categorical-column plots that the numeric-only
script did not create. Atomic publication failed and the terse missing-file error led to
repeated script rewrites, missing-PNG reads and approval cycles. Registered Python writes with
literal backslash-n separators also reached human review before syntax validation. Separately,
AgentRunner constructed a fresh request limit on each approval resume.

## Decision

Allow one bounded output_manifest_path written by the script from the same saving loop as its
outputs. Validate the fresh path before approval and again at dispatch; validate descriptors
and file contents through the existing publisher; register outputs and the manifest atomically.
Expose the exact published paths. Retain the explicit fixed-output contract and required plan
deliverables. Distinguish publication failure after exit zero from Python execution failure.
Update Coding and Visualization guidance to use the manifest for data-dependent output sets.

Validate registered .py writes before review, without changing bytes or interpreting escapes.
Compute remaining per-task model requests from owned model.request_recorded events. Approvals,
runner reconstruction and compacted message surfaces do not replenish that recorded budget.

## Alternatives considered

Raising turn limits or suppressing TOOL_FAILED would preserve the incorrect output contract.
Registering only the files that happen to exist would hide missing required outputs. Guessing
outputs with a workspace glob could publish unrelated files. Automatic backslash decoding can
change valid Python strings. Counting current conversation messages loses usage after compaction.
These alternatives were rejected in favor of producer-written declarations, atomic validation,
exact source preservation and the durable dispatch ledger.

## Consequences

Manifest callers need a fresh JSON path on corrected executions and append descriptors after
successful saves. Existing explicit callers remain supported; no approvals or artifact checks
are bypassed. A manifest does not prove the creation time of each referenced file, and no live
model behavior is guaranteed by offline tests. The cumulative limit counts recorded requests;
legacy or injected providers without request-ledger evidence cannot reconstruct unrecorded use.
The cancelled Run had 33 Coding requests, below its configured 40-turn limit: the budget fix
closes a separate defect rather than explaining that Run's entire observed loop.

Regression evidence and limitations: docs/session-python-publication-verification.md.
