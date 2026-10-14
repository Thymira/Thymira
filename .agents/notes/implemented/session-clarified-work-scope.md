# Preserve clarified Session work scope

## Problem

Session `session_bc469c5152aa48e8a43db7313ffc6d49` asked twice for confirmation after the user
supplied `german_credit` for an EDA. Its eventual Run used only `Si` as its current request.
Earlier messages were present in untrusted discussion history, but risk and model-scope checks
intentionally read only current work. Thus a clarified EDA lost its explicit scope at those checks.

## Decision

Supply bounded dataset names/availability and typed discussion history to the existing routed
interpreter. Instruct it to finish clarified work without repeated confirmations, distinguishing
continuations from new requests. On a continuation, freeze complete prior human messages from the
same actor's explicitly linked Auto clarifications, stopping at prior work or question boundaries.
Independent clarifications reset the chain; legacy records without parent bindings ask the user to
restate the complete request. The latest explicit human scope overrides earlier scope, and a bare
registered dataset name does not count as a request for model work.
Reject an unavailable or oversized chain rather than partially copying restrictions. Preserve the
literal final message and add a backward-compatible clarification context to Run metadata. Risk,
planning and the current Run prompt use the combined human request. Persist before publication and
bind retries to that context, preserving the Gate and standalone contracts.

A later live regression showed a first dataset question marked `continues_clarification=true`,
which caused the backend to discard a useful question for lack of a parent. Correct the prompt's
ambiguous wording and normalize that flag only when there are no Run turns and all prior records
are failed interpretations without selected/published action. Use full stored records rather than
bounded rendered history to establish this fact. Preserve the model's kind, message and raw ledger
evidence; established or pending dialogue still follows strict parent checks. A failed-only session
can therefore restart the discussion without inheriting failed requests as work context.

## Alternatives considered

- Treating every short answer as work could execute a reply to an explanation or a cancelled request.
- Continuing to use only `Si` loses task scope even if the model can see it in general history.
- A model-written replacement instruction could add work the human never requested.
- Replaying all past user messages as current scope could revive earlier training requests.
- Keyword-only detection and fixed confirmation questions do not resolve multilingual intent.
- A prompt-only first-question fix would still let an impossible model continuation flag break
  an empty session. Conversely, ignoring invalid parents for all messages could revive old work.

## Consequences

The normal interpreter remains a single ledger-recorded model call, with no tool capability.
Model intent accuracy remains probabilistic; scripted regression tests verify context, boundaries,
retry and downstream behavior, not real-model semantic accuracy. No live user Run is retried or
modified. A pure retry lookup seam was extracted from ConversationWorkspace while extending its
binding: measured during integration at 215 lines before extraction and 193 after. RiskInterviewService
stays at 722 lines; only its current-request accessor changed. The unrelated Windows persistence
failure in the same Run is documented and corrected separately.
