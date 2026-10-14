# Session risk clarification, file editing and regulation retrieval

## Problem

Session users could not edit visible project files or answer the risk interview in chat. A
complete context-derived activity profile still produced missing-fact classifications because
the second classifier received presence flags without all the declared values. Review cards
emphasized policy IDs, and ordinary polling interrupted their controls. The reported Run
`run_d277a25b86e941d69ff6986077fcd021` failed because queries for Article 11, 12 and 14 did not
search the packaged regulation chunks' location field.

## Decision

Keep the event-backed Run, immutable activity profiles, Gate and required audit evidence. Expose
the interview in the session, carry exact question identities, and allow bounded clarification
of risk facts before execution. Clarification is a new intake checkpoint, not a human approval.
Provide all declared facts to classification as bounded untrusted context, while the current
message defines the requested work and the project purpose remains background context. Explain
review scope from recorded evidence and preserve the form during unchanged background reads.

Edit only existing visible bounded text files, with WRITE authorization, revision comparison
and atomic replacement. Reject symbolic links, reparse points and multiply linked regular files.
Index regulation locations as well as text; explicit article-number queries must match the
chunk's location, not a cross-reference in its prose. A genuinely empty result still fails
mandatory audit evidence.

Serialize plain resume with human-answer publication using the existing Run review lock;
dispatch starts after release. Keep continuation preparation in `run_resume.py`, while
`RunService` retains its public error imports and lifecycle entry points.

Keep immutable profile writes in `risk_profile_records.py` and interrupted-answer publication
in `risk_answer_publication.py`. Replay the recorded judgment, preserve the answer's actor and
announced profile identity, and bind retries to the current information wait and question chain.
A committed clarification transition retires its previous execution-start decision; checkpoint
recovery must evaluate the corrected profile instead of reviving that historical review.

## Alternatives considered

- Letting a risk dropdown override deterministic safeguards would replace governance with a
  client assertion. Correcting the facts gives the classifier useful evidence without authorizing.
- Automatically approving uncertainty would hide missing facts. Bounded questions give the user
  a concrete way to resolve ambiguity while preserving review when uncertainty remains.
- Treating any successful search invocation as audit evidence would accept empty results. Fix
  retrieval instead, retaining framework and fragment-hash validation.
- Blindly overwriting project files would lose external edits; a revision conflict preserves
  the local draft and requires a fresh read.

## Consequences

Dataset and artifact lifecycle remain unchanged. Project edits are shared; risk answers belong
to the current Run and survive reconstruction from its event log. Existing sensitive-data and
other deterministic policy reviews still apply. No production Run log, corpus content or
reference repository is rewritten. Scripted providers isolate tests from external model services.
The reconstructed graph test reaches completion through THY and MIRA with a new execution
decision and zero fabricated human approvals. Restart and interrupted-write tests exercise the
same local event-backed persistence used by the API.
