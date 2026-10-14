# Repeated invalid Python calls and lifecycle evidence

## Problem

The EDA run repeatedly submitted the same Python source with literal `\n` text where actual
newlines were required. The host parser rejected it before Gate approval or sandbox execution,
but the model received no actionable explanation and could spend more requests submitting the
same bytes. Historical evidence showed 21 callbacks for this deterministic syntax failure; the
recorded usage was 294,145 input tokens (67,840 cached) and 9,087 output tokens. The log did not
provide a reliable USD total.

## Decision

Preserve the submitted source bytes and report a bounded parser error that points out the likely
literal `\n` escape without rewriting the code. Within one delegation, count only matching
`run_python` preflight syntax failures for the exact same source, rebuilding the count from the
verified tool transcript after resume. After two such failures, terminate that delegation before
requesting another model response and propagate the terminal reason into the task result. A
successful tool return, another task or run, a corrected source, and sandbox/runtime failures do
not count toward this limit.

Move step-key generation and usage, delegation, and resumed-execution payload projections into
`thymira.agents.lifecycle_evidence`. They are pure transformations imported back into `runner.py`
under the same names, preserving its existing call sites and behavior. The helper imports only
`thymira.events` at runtime; domain models are annotation-only, so it does not import the runner
or create a runtime cycle.

## Alternatives considered

- Keep retrying the identical syntax failure, which permits the repeated request and spend.
- Unescape every `\n` automatically, which can alter valid Python strings and approved tool
  arguments.
- Add a second persistent failure ledger, which duplicates the verified transcript and can drift
  across pauses.
- Leave lifecycle projections in the already oversized runner module, despite their shared,
  pure evidence-projection responsibility.

## Consequences

The limit blocks repeated deterministic parser failures while leaving corrected source and other
failure classes available. It does not change Gate approvals or sandbox execution. The source is
never silently normalized, and successful output cannot seed the retry count merely by containing
similar error text. Extracting lifecycle projections reduces `runner.py` without changing event
payloads or step identity. Historical token totals are available, but USD cost is not.
