# SessionChat evidence continuity

## Problem

The last two artifact events omitted older models, and citation repair could name invisible
sources. Long prior summaries could exhaust the artifact evidence budget.

## Decision

SessionChat builds its citation allowlist from complete JSON records that remain visible in the
final framed evidence block. The same frame is passed to the provider and its source IDs constrain
the structured response schema. Artifact-created events use a bounded catalogue across the recent
Runs, ranked by overlap with the user's question, while other operational event types retain their
recent-record limits.

If both a proposed answer and its citation repair fail validation, SessionChat may publish a
deterministic answer only when a recent failed or blocked Run has a server-recorded diagnostic and
the exact Run summary remains citable. That response contains only the recorded status and
diagnostic. Otherwise the discussion remains failed with no model-authored answer.

SessionChat proposals classify actionable work as `general`, `evaluate_existing`, or `train_new`.
The selected value is persisted in the private discussion record and passed into the governed
ConversationInput; older records default to `general`.

## Alternatives considered

This replaces the last-two artifact heuristic and an unconstrained citation list. Accepting
invalid citations or publishing the failed model answer would hide an evidence error.
Adding filesystem tools would not establish artifact identity or fix framing truncation.

## Consequences

Explicit Run selectors constrain evidence and deterministic fallback. Summary space is bounded
using escaped character lengths so a relevant model name remains visible under pressure.
Ask stays read-only; default work kinds preserve older discussion records.

Validation: `tests/thymira/test_session_discussions.py` and
`tests/thymira/test_session_chat_evidence_continuity.py` pass with scripted providers and an
isolated pytest basetemp. Ruff check and formatting pass for the modified SessionChat files.
