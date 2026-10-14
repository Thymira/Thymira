# Session inputs and human review

## Problem

Session users could not supply a missing declared dataset and had to leave the conversation to
answer an unexplained human-review button. A real failed Run reached Inspect after approval with
`german_credit` declared at the absent `data/applications.csv`.

## Decision

Reuse project dataset APIs and the existing input controls in the session inspector. Reject new
turn publication when declared sources are missing or cannot be checked safely; preserve accepted
retry identities. Render review context from verified events, including recorded risk/missing
information, tool intent or findings and answer consequences. Send the displayed decision ID to
the existing approval endpoint. Serialize competing review answers per Run using a local OS lock.

## Alternatives considered

- Per-session dataset storage loses the existing project/Inspect contract and has no requested
  isolation consumer; reuse beats duplicating source registration.
- Silently inserting a demo dataset would analyze different data without the user's decision;
  naming the missing source and exposing replacement keeps the input choice explicit.
- Generic approval text cannot support a meaningful decision. Generating explanations with an
  LLM adds unsupported claims; deterministic rendering of recorded evidence is auditable.
- A frontend-only busy flag cannot serialize other clients or processes; bind decisions and
  protect the backend critical section as well.

## Consequences

Standalone Run APIs retain their shape; decision identity is optional for legacy clients. Dataset
files remain project-shared and are registered by Inspect, so the readiness check cannot prevent
later external filesystem changes. No failed production Run or user dataset is rewritten. Real
profile-tool tests prove CSV upload, source hashing, persisted output and follow-up reconstruction;
Node tests cover upload controls, routing, review evidence and stale states. Browser checks use
a disposable local project and a recording dispatcher, separately from the real graph E2E test.
