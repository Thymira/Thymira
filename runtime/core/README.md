# thymira-core

Thymira Runtime: Run/Session services, LangGraph wiring, checkpoints, resume, the single place where ThyGraph and MiraGraph are composed.

| | |
|---|---|
| Import name | `thymira.core` |
| Owner (MVP roadmap) | P1 (Runtime / Tech Lead) |
| MVP priority | P0 |
| Location | `runtime/core/` |

## Status

Implemented and tested (`tests/thymira/test_core.py` and the `test_core_*` suites): provenance
capture, the analytical phase machine, project resolution, a repository-backed `SessionService`,
and an event-backed `RunService` that persists lifecycle changes through the state seams.
`RuntimeState` and the structural `Subgraph`/`SubgraphDeps` contract form the neutral
runtime-to-LangGraph boundary; the initial `RunGraphState` remains available for compatibility.
The run-scoped `UsageLedger` and the execution idempotency/lineage seam (`compute_execution_key`,
`IdempotencyJournal`, `cascade_invalidation`) are implemented, and `StateCheckpointer` persists the
latest LangGraph checkpoint through the backend-agnostic `CheckpointRepository` (`RA-CORE-07`).
The injected `ExecutionDispatcher` seam and synchronous `InlineDispatcher` (`RA-CORE-10`) now
compile and invoke the composed graph for a newly created Run. A queue worker remains a separate
FINAL task (`RA-CORE-11`); `background` is accepted as a reserved option until that dispatcher exists.

The bounded MIRA-to-Run control plane is also implemented
(`tests/thymira/test_core_control_plane.py`): `MiraControlPlane` routes an `ActionIntent` through
deterministic policy evaluation, `Gate` evidence and an optional scoped `Approval`, and
`RunController` is the sole persistent transition writer — no graph, agent, API adapter, policy
rule or approval handler writes Run lifecycle state directly (ADR-0008). It replays
`run.transitioned` events and persists through `LocalRunStore`, where `events.jsonl` is
authoritative and `run.json` is rebuilt from it; the local files are not a multi-file transaction.
THY does not consume MIRA context snapshots in this slice.

`RunController.advance()` now handles a Run's ordinary forward progress without policy bypasses;
`RunService` uses that controller and `LocalRunStore` for all lifecycle writes. The older
repository/`UnitOfWork` seams remain available as foundation code for future backend work, but are
not used by the Run lifecycle. `LocalRunStore` (ADR-0010) is the current persistence boundary.
Composition of THY and MIRA (`RA-CORE-06`) is implemented in
`thymira.core.graph.compose.build_runtime_graph`, which compiles the sequential
governance preflight -> THY -> MIRA evidence audit -> Gate graph. The preflight records inherent
risk and applicable packs before THY; MIRA evaluates produced evidence after THY in `IN_FLIGHT`
mode, so A2 remains a final-closeout control. A PostgreSQL backend (FINAL, behind a new ADR) still
follows the MVP roadmap (`docs/roadmap/product-final.md`, `docs/roadmap/mvp-minimum.md`).

The execution-start review is a Run-level gate before THY execution. A `REQUIRE_HUMAN_REVIEW`
decision parks the Run with an exact persisted `execution_start` resume origin; a reconstructed
dispatcher reads the recorded human answer. An approved start review enters THY without
repeating intake or minting a new decision, while rejection blocks the Run. Each tool receives its own
Policy Engine and ToolManager authorization. `ExecutionConstraints.requires_human_review` remains
an independent enforced restriction: each tool call needs a separate one-shot human approval.
The Run parks again on that exact tool decision, and the existing public approval and resume
methods continue the task after dependencies are reconstructed from disk. Start approval never
waives other restrictions or supplies a tool ticket. The pending start summary discloses these
separate reviews. The default demo A18/A19/A23 outcomes are unchanged.

Plan review parks separately in PLANNING. Its approval request stores the exact normalized plan
and binding metadata; Core validates the matching manual answer and current scope before resuming
that candidate. Checkpoint recovery does not replan or replace the approved decision. Inline and
owner-bound lifecycle continuations retain their existing transition and persistence authority.

Persistent Session conversations use `ConversationWorkspace` to create sequential Run-backed turns.
The Run stores the scrubbed user message, ordinal, client retry key and selected prior Run IDs;
conversation detail reconstructs assistant text only from verified `conversation.response` events
covered by an authorized terminal completion and its exact audit snapshot. THY records those events
as provisional before MIRA; blocked, pending or otherwise unauthorised text never enters the
transcript or follow-up prompt. Approved review resume records the same audit/decision binding as
ordinary completion and uses the canonical exact-subject human approval check.
No second copy of the Run transcript is introduced. Run publication is serialized per Session and
uses the existing staged Run publication path; retries return the linked Run, and pending
publications are recovered only for that Session before retry lookup. A recovered Run is not
dispatched implicitly; normal Run lifecycle controls remain responsible for continuing it.
Focused behavioral coverage lives in `tests/thymira/test_core_conversations.py`.

`SessionChat` adds read-only discussion alongside Run turns. A citationless or incorrectly cited
answer can receive one repair on the same route. A discussion can make at most four server-run
read queries before its answer and optional repair, with a six-request ceiling and 16,000 cumulative
tokens; token settlement may end it earlier. The repair cannot publish work or issue a lookup. A
routed, ledger-recorded model request interprets a message as an evidence question, clarification
or work request; explicit Ask mode cannot dispatch work. Explanations read only bounded verified
Session evidence and released results, carry server-resolved references, and are labelled as
unaudited interpretations. Work
still uses `ConversationWorkspace` and the existing THY/MIRA/Gate lifecycle. Discussion records
and model evidence belong to the Session, use separate OS locks, and survive reconstruction.
Before dispatch, each request is prepared from its actual messages and serialized schema, with an
explicit local-tokenizer margin and a provider-enforced output cap. Remote tokenization and schema
serialization can differ from that estimate. Durable dispatch counts and inclusive usage survive
reopen; cached input and reasoning output remain visible subsets. Unknown usage conservatively
reserves the remaining balance and ends the turn. Citation repair receives the full draft, the
current human message and complete selected source records, without datasets or general history.
The Run event envelope is unchanged. See `docs/contracts/conversation-sessions.md` and
`tests/thymira/test_session_discussions.py` for retry, interruption and permission behavior.

Session file questions recognize registered PNG filenames as plot metadata and instruct the model
to inspect omitted file locations through the existing read-only virtual tree before claiming
absence. Initial and compact requests share the lookup protocol: glob queries keep the literal
scope in `path` and filename wildcards in `pattern`; grep searches contents and listings cover
immediate children. Failed Run status does not erase existing file evidence.
These relevance and prompt rules do not change artifact release, tool policy or Run authorization.
For a complete simple location question naming a literal ASCII extension, Core may render exact
positive file paths from the current authorized read and its visible server citations, preventing
a later model answer from denying those observed paths. It preserves whole paths, labels limited
pages and declines ambiguous questions or linked clarification context.
After a read, compact continuations retain complete human restrictions, complete selected evidence
pages and exact source IDs. They reserve an answer-only request before another lookup; a page that
cannot fit is omitted whole with a safe context-limit record. The raw server evidence is retained.
Selection, reservation and dispatch reuse the exact same framed prompt and schema estimate.

The Session publishes work from the current human message and any explicitly linked human
clarification. The model's `work_kind` is only a proposal; it cannot authorize training or
evaluation. Brief replies continue only a persisted pending work clarification, while evidence
questions, quotations and unlinked confirmations remain outside the work chain. Training requests
do not require an existing model. Session intake asks for a missing training dataset or target;
ambiguous model requests are clarified before publication. Plan validation requires a recorded
source and inputs for evaluation and never substitutes training when they are absent. General
analysis such as EDA also excludes evaluation specialists and rejects tasks that depend on them.
When a human explicitly requests new training but the plan proposes evaluation, THY retargets that
task to the experiment specialist and clears its prior-model inputs.

Source-code requests can select an already cited artifact and literal search text. Core quotes
numbered lines only after checking the approved snapshot, project/Session scope, artifact
identity, digest, size and bounded text format. The model never reconstructs the quoted code;
this read creates no Run and adds no provider request. Evidence clarifications remain separate
from work intake. Review excerpts include the exact later human response, so an earlier request
for review does not imply that a terminal technical failure is still waiting for approval.

Runtime identity is captured once when the API or lifecycle worker composes its execution graph
and is reused for Runs handled by that process generation. Each `run.started` stores the immutable
generation id, deterministic fingerprint (when every declared layer can be verified), layer
digests/counts and explicit unavailable reasons. The bounded layers distinguish the installed
Thymira source snapshot, code objects and binding instances already loaded in memory, effective
Pydantic schemas, resolved catalog/spec/prompt snapshot, installed LiteLLM/OpenAI/Pydantic schema
transformer files, and Python runtime. Per-Run observations inspect loaded code and schemas only;
they do not re-hash the source tree. A `runtime.attached` LOG_ONLY event records the generation
that actually continues a Run after its graph factory resolves and links back to the original
`run.started` sequence and hash. Resume retains the creation identity.

These layers are deliberately scoped evidence. An installed source digest does not prove that
every file was loaded; installed transformer files do not attest bytes already loaded by a lazy
serializer. The local JSON Schema is a projection of the effective Pydantic classes, while the
adapter receives a Pydantic class as `response_format`; neither proves HTTP serialization or
remote acceptance. Module globals and dynamic settings are outside the loaded-code digest, and
runtime skill catalogs deferred to per-Run roots make the catalog layer unavailable. Secrets,
workspace data, artifacts, Run history and unrelated namespace packages are excluded. See
`.agents/notes/implemented/2026-10-03-runtime-identity.md` for the evidence boundary and tests.

## Rules

- Contracts live in `thymira.schemas`; do not redefine them here.
- Everything is English; Google-style docstrings; tests next to the feature in `tests/`.
- Follow `AGENTS.md` (conventions) and the import direction in `runtime/README.md`.
