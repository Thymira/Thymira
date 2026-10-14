# Session discussion and relevant risk questions

## Problem

In Session `session_856fb9355a914a6cb1e4bc340b82e0b7`, the follow-up “que es eso” created
`run_9da1488cec2549da8ea07220c6965bc7`, repeated risk intake and attempted execution. The existing
session mechanism only chained analytical Runs. Risk uncertainty generated a static decision-effect
question, followed by questions presupposing credit decisions even after explicit test-only answers.
Three unsuccessful attempts on that field exhausted its limit, but the review card disclosed neither
the field/count nor a concrete consequence of human approval.

## Decision

Add a runtime-owned read-only discussion lane. An additive chat API routes explicit work into the
existing Run service; questions produce bounded explanations of scoped verified evidence without
tools or an analytical lifecycle. Keep old action APIs unchanged. Persist private retry/actor bindings
and public discussion projections atomically; reuse the canonical model router and RequestLedger
against an explicit Session evidence envelope. Never fake a Run for a session inference or append
explanatory calls to an unrelated analytical audit chain. Limit inference to one request per message,
save before dispatch, and make interrupted outcomes visible rather than silently charging again.

Allow the risk-interview model to choose a concrete fact/question from the complete current profile,
assessment, user request and prior answers. Preserve exact assessment/profile binding and replay.
Keep finite questioning bounds and deterministic risk/policy floors. A model's explanation or
question is not an approval. Show concrete review effects and the facts/answers behind exhaustion.

## Alternatives considered

- Keyword-only intent detection would misclassify short contextual questions and multilingual work.
- Treating every message as an execution Run reproduces the reported risk/approval loop.
- Calling a provider directly from the browser or without the existing ledger/allowlist would lose
  authority, persistence and provenance guarantees.
- Reusing a real Run's log for unrelated discussion would mutate its audit evidence after closure.
- Unlimited interview retries or silently lowering risk would hide irreducible uncertainty rather
  than resolve it. A bounded, informed human decision remains necessary when useful facts run out.

## Consequences

The combined timeline contains two explicit record types: governed work and explanatory discussion.
Discussion is labelled as read-only interpretation, not a new audited result; reference validation
does not prove every LLM sentence true. Context is bounded, excludes provisional results/raw tool
arguments and does not open artifact contents. A dead provider call can leave an unknown outcome;
the same identity is never automatically charged again. Existing local filesystem and project-wide
permissions apply, without introducing authentication or a replacement backend.

Run-backed `ConversationWorkspace` remains below the size threshold at 205 lines. RequestLedger remains 403 lines;
LedgerProvider remains 211; the flagged ledger module shrinks from 948 to 943 lines through extraction
of its scope-neutral request identity/types. Risk question projections and review publication/folds
were extracted into `risk_question_records.py` and `risk_review_requests.py`: the integration
measurement falls from 876 to 722 lines for RiskInterviewService, and from 426 to 373 for
MiraSubgraph; their final modules are 951 and 2,021 lines. The turn-start risk sizes were not captured,
so these are integration measurements, not a claim about an unavailable initial snapshot.
Verification is recorded in the accompanying report.
