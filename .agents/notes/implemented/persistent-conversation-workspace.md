# Persistent conversations over existing Runs

## Problem

Sessions already linked Runs but had no user-facing lifecycle, transcript or cross-Run context.
The web console could only start isolated prompts. A visual redesign alone could not provide
iterative work, and a second execution engine would bypass existing governance and evidence.

## Decision

Keep local repositories, lifecycle publication, RunController, THY/MIRA, artifacts and bearer
permissions. Add opt-in conversation Sessions and immutable per-Run conversation input metadata.
One ordered message creates one Run. Store a bounded snapshot of recent conversation context in
that Run's prompt, framed as untrusted data; preserve the original message separately. Persist
only the existing validated task outcomes/final THY summary as a provisional conversation response
event on the hash chain. Reconstruct the transcript and operational state from these records.
Release response text to the transcript and next prompt only when a terminal completion binds the
exact policy decision and typed audit snapshot covering that response. Require PASS/WARNING or an
exact authenticated human approval without rejection. Reuse the existing governance-binding and
human-approval helpers, including on graph resume, rather than trusting THY completion or the latest
projected policy decision. Keep provisional evidence inspectable through existing Run permissions.

Filter conversation Sessions in the repository before pagination; preserve the default unfiltered
query and stable cursors. Follow all pages in the web client with repeated-cursor detection.

Serialize message admission per Session across local processes and use lifecycle publication's
durable idempotency. An active, waiting or paused Run prevents another turn. Terminal failed,
blocked and cancelled Runs remain visible and permit a new turn; follow-up is a new governed
Run, never a resumed authorization. Existing standalone Runs remain independent. Conversation
Sessions must use the message endpoint, preventing the standalone endpoint bypassing admission.

The web remains a static ES-module API client. Adapt the redesign's three-pane workspace,
session navigation and inspector while preserving the existing Run tabs. A bounded read-only
project explorer excludes hidden state, credential paths and filesystem links. Inputs editing
and digest-verified artifact viewing keep their existing APIs.

## Alternatives considered

- Copying or merging the older redesign would discard changes on current main and still leave
  persistence unimplemented. Only layout and interaction ideas are reused.
- A browser transcript or a separate chat database would duplicate authoritative runtime state
  and fail after restart or client changes. Existing Run records/events own the transcript.
- Concurrent turns within one conversation would create ambiguous context snapshots. Reject
  them explicitly; independent sessions remain available for independent work.
- Replacing the runtime with OpenChamber/OpenCode would remove Thymira's policy and audit
  boundaries. OpenChamber supplies interaction ideas only.
- Publishing a THY summary before final review would let rejected evidence become future model
  context. A bare completed flag also lacks the exact policy/audit authorization; both were rejected.
- Filtering an already limited page or silently stopping after twenty pages hides valid Sessions.
  Repository-side filtering and complete cursor traversal preserve discoverability.

## Consequences

The Session is project-shared under the existing READ/WRITE/APPROVE model, not a new personal
login or privacy boundary. Context is bounded and older history remains inspectable. Run
workspaces, budgets, approvals and artifact ownership remain Run-scoped. The implementation
inherits the local lifecycle backend's recovery/dispatch limits; it does not promise a new
distributed scheduler, automatic approval or automatic replay of uncertain tool execution.
Historical response records lacking an authorized audit binding stay visible as Run evidence but
are withheld from final chat and reused context. Standalone Run contracts remain available.

Reference provenance: Thymira main `537943a7cdbdd1062188930976e5573b145c867c`, redesign
`e4d195d86cb41cea20cc9c924fc599ddca2e2d69`, OpenChamber
`60d836c4893b6b694610b5b0277be54111359428` (MIT, copyright 2025 Bohdan Triapitsyn).
No OpenChamber source code or assets are copied, and both references remain unchanged.
