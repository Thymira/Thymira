# Session source quotations and structured plan repair

## Problem

A Session source-code question was repeatedly clarified as pending work. Its user messages then entered an unrelated training request. Read-only evidence contained artifact names but no source bytes. A failed training Run also exposed an invalid planner contract: content matching was offered for all artifact kinds, execution-owned provenance appeared in the model schema, and structured retries omitted validation feedback. Historical review requests could appear without their later human responses.

## Decision

Keep source access in Core as a bounded, read-only operation. A typed proposal selects an already supplied artifact event and literal text search. Core requires the exact Session/project, an approved source snapshot, registered CODE identity, matching digest and size, and a supported text suffix. It renders numbered lines from verified bytes, never model-authored code. Missing, oversized, invalid, changed or unapproved revisions fail closed; credential-bearing text is not quoted. No tool execution, new Run or additional model call is introduced. Persist work/evidence clarification scope; evidence clarifications do not contribute to work intake. Legacy records retain the conservative work default.

Expose separate exact-file and metrics-content requirement variants to the planner. Hide runtime provenance and recovery properties from JSON Schema and reject nonempty model-authored values locally; empty legacy defaults remain compatible. Keep the existing single structured retry and measured usage charging, but include its validation feedback in the next prompt. Feedback contains only schema field names, positions and error codes, not invalid values, dynamic mapping keys or custom exception messages. Extract structured-message translation from the large provider adapter.

Pair historical review requests with recorded human responses by exact decision ID and Run before context truncation. These are explanatory facts, never reusable approval authority. The terminal diagnostic remains the closing cause.

## Alternatives considered

Increasing retry limits would spend more without correcting the request. Unescaping generated Python automatically would change executable arguments; this change does not do that. Giving SessionChat general filesystem or execution tools would expand authority unnecessarily. Asking an LLM to reconstruct exact code from filenames cannot provide source fidelity. Treating all clarifications as work preserves a false relation; removing work clarification binding would lose the original human scope. Accepting invalid content requirements or model-authored hashes would weaken deliverable and lineage checks. Reading historical stores through LocalArtifactStore could alter ACLs, so inspection uses direct UTF-8 and byte reads.

## Consequences

Existing source files become quotable with stable event citations, digest and line ranges. The first matching source region is bounded to 48 lines and 4,400 quote characters from at most 64 KiB registered source. Supported formats are Python, R, SQL and JavaScript; notebooks and unapproved Runs are not quoted by this seam. A source selection is still proposed by the model and can fail safely. Offline tests prove these boundaries and retry behavior, not universal conversational accuracy. Historical events, approvals, outputs and budgets are not rewritten.
