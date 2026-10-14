# Conversation sessions

Conversation sessions extend the existing local Run model and project-scoped bearer permissions.
They do not introduce accounts or change standalone `POST /runs` execution.

| Route | Permission | Result |
|---|---|---|
| `POST /sessions` | WRITE | Empty Session; optional `title` and `client` |
| `GET /sessions?limit=50&cursor=...` | READ | `{items, next_cursor}`; conversation Sessions only |
| `GET /sessions/{id}` | READ | `{session, turns, active_run_id, discussions}` |
| `POST /sessions/{id}/messages` | WRITE | A turn; body `{message, client_message_id}` |
| `POST /sessions/{id}/chat` | WRITE | Discussion; body `{message, client_message_id, mode?}` |
| `POST /sessions/{id}/chat/{discussion_id}/retry` | WRITE | Same actor's stored discussion; no body |
| `GET /project/files?path=...` | READ | One directory's `{path, entries, truncated}` |
| `GET /project/file?path=...` | READ | Redacted text `{path, content, truncated}` |
| `GET /tools/runtime-status` | READ | Configured Python execution prerequisites; no execution |

A turn contains `id` (the Run id), `ordinal`, `user_message`, nullable `assistant_message`,
`run`, composite `state`, and nullable `terminal_diagnostic`. Run controls, interviews, reviews, artifacts, audit and events
retain their existing `/runs/{id}/...` contracts. A missing assistant message means no authorized
final response is available; clients must not infer success from an HTTP creation response.

The web composer uses `/chat`. `mode` is `auto` (default), `ask`, or `action`. The internal bounded,
routed proposal may request one server-executed read lookup per response, within the four-query
turn limit; user-facing discussion kinds remain `answer`, `clarification`, and `action`.
One read-only citation repair is permitted for an answer
with missing or invalid references. `ask` forbids action regardless of model output. In Auto or
Request work mode, the human message controls work authorization;
`work_kind` is only a model proposal. SessionChat and the planner resolve the canonical kind from
the current human message and its explicitly linked human clarification chain before publishing or
planning work. Explanations, quoted instructions and an unlinked `yes` cannot authorize a Run.
The legacy `/messages` route continues to mean an action, and standalone Run endpoints retain
their contracts.

The interpreter receives a bounded inventory of declared dataset names, availability and configured
target columns, without paths or dataset contents. For training, the intake prompt asks for a
dataset or target when absent and may use one available declared dataset when unambiguous. That
question is an LLM intake proposal; code still resolves whether the human requested training and
persists a clarification and its linked human replies before any resulting Run. The model is
instructed not to reconfirm explicit work or confirm a confirmation. Its
`continues_clarification` proposal identifies a reply; `replaces_clarification` proposes a
replacement but cannot authorize discarding the pending task. Evaluation requires an approved
existing model source and its registered model inputs; if they are missing, the planner asks for
them instead of starting training. Risk and mitigation questions belong to MIRA's Run preflight.
For a human `general` request, Plan removes Experiment, ML and Evaluation tasks and rejects
remaining tasks that depend on them. For explicit new training, an evaluator proposal can be
retargeted to Experiment while retaining its task identity and dependencies; prior model input IDs
and source-bound evaluation requirements are removed.
For a continuation, code freezes at most eight complete, same-actor user messages (8,000 characters)
from explicitly linked Auto clarifications. A new independent clarification starts a new chain;
Ask/answer/action records, actor changes and a newer Run end it. An oversized or unavailable chain
refuses publication. Legacy clarifications without explicit links ask for a complete restatement
of the task and dataset instead of guessing what a short confirmation meant.
Assistant suggestions never become authoritative work text.

Proposals and stored discussions carry `clarification_scope` (`work` or `evidence`, default
`work` for legacy records). Only work clarifications contribute user messages to work intake.
An evidence clarification clears work continuation/replacement flags and ends the pending work
chain; asking which source to inspect cannot silently become part of a later training request.
Explanations, quoted instructions and orphan confirmations use this evidence scope, so a later
`yes` cannot turn them into work.

When the model omits or clears its continuation flag, a natural reply still binds to the adjacent
completed Auto work clarification by the same actor in the same project. A proposed replacement
fails closed and asks the human to select Request work with the complete replacement task. Only
that explicit user mode starts independent work. Failed same-actor/project Auto interpretations
without an action or Run do not break the pending chain; Ask, other actors/projects, completed
answers, actions, and new Runs remain boundaries. Conflicting relation flags fail closed. This binding
does not match phrases or require an exact dataset name. The complete explicit parent chain is
validated before publication. A bare registered dataset name without that valid chain does not
create work; the binding cannot cross an Ask, an action or another Run.

The interpreter's continuation flag cannot require a nonexistent first parent: when no Run or
usable prior dialogue was established, code treats the reply as a new root. Earlier failed-only
interpretations do not count as dialogue and are not copied into work scope. Completed/pending
records and prior Run turns keep the strict parent checks, even if absent from bounded model
context. The reply's kind and current user text remain unchanged; Ask still cannot publish work.

An additive `Run.conversation.clarification_context` tuple preserves those user messages, while
`message` remains the latest literal input. The runtime's `requested_work` view combines them for
the Run prompt and risk interview/classification. Old records default to empty context. THY uses
the latest explicit human scope in that tuple and final message; exact registered dataset names
alone do not request model work. This snapshot is persisted before publication and included in
retry binding; later chat
messages cannot alter a pending action. It does not authorize execution or replace any Gate.

Discussion records contain `id`, `ordinal`, `created_at`, `user_message`, `mode`, `kind`,
`status` (`pending`, `completed`, `failed`), nullable `assistant_message`, nullable `run_id`,
`citations` (nullable `run_id`, nullable event `seq`, optional virtual `path`, `label`), and
nullable safe `error`. A citation must reference a real Run or a server-resolved virtual path.
Action records
reference normal Run turns; clients hide the duplicate discussion once its Run appears.
Discussion ordinals order discussions only; the combined timeline uses creation timestamps.

A read-only explanation is **not a newly audited analytical result**. It has no write or execution
tools, Gate authority, risk override or Run transitions. Its initial context selects at most four
recent session Runs, approved summaries, bounded operational diagnostics/review events and artifact
IDs/names, with 16,000 evidence characters and 8,000 discussion-history characters. For iterative
inspection it may request only four fixed, policy-checked operations: `list_files`, `read_file`,
`glob` (filename matching) and literal `grep`. The server executes each lookup and sends back a
bounded result as untrusted evidence. The virtual tree is `project/...` and
`runs/{actual-session-run-id}/...`. Project reads use the existing redacted, link-safe bounded
reader. Run reads are limited to Runs that belong to this exact Session and project, including
older turns outside the initial four-Run context. Status, safe operational trace and registered
artifact metadata remain available as operational facts. A Run result or artifact bytes are
available only when the exact artifact creation is inside the approved audit snapshot and its
registered revision still matches the digest; a later invalidation of that revision blocks its
contents. A pending, failed or invalidated source never releases provisional contents. No
arbitrary files or unrestricted artifact contents are exposed. A glob pattern containing `/` is
relative to its explicit scope; a basename pattern matches file names at any depth beneath that
scope. Errors and paginated results state their bounds; an incomplete listing or search is never
represented as complete. Successful read rows receive server-bound citations, including project
paths, and the client opens project citations in its existing explorer. The project-root citation
opens the explorer at its root. Unknown source IDs refuse publication. Text generation itself
remains an LLM interpretation, clearly labelled in the client. Pending/provisional analytical
summaries never enter this context. Later action Runs receive bounded discussion context labelled
as untrusted explanations, in addition to their usual approved Run history.

Question selection recognizes registered PNG filenames as plot metadata even when a historical
artifact event omits its kind. Selection changes relevance only; it does not authorize contents.
Ordinary event projections carry the verified event owner's Run ID independently of the Run
summary, so a compact event record remains locatable when that summary is omitted. Payload claims
cannot override this identity.
The interpreter is instructed to inspect the relevant Run's virtual tree before claiming that an
existing file is unavailable when its location is absent from the bounded initial context.
Lookup `path` is a literal directory or file path; `glob` places wildcards in `pattern`, for example
`path="runs/<run_id>/artifacts", pattern="*.png"`. The same protocol applies to initial and compact
requests: use filename globs for extensions, literal grep for contents, and treat listings as
immediate children only. Run failure or a later tool error does not erase positive file evidence.
This prompt guidance does not guarantee a lookup or a factually correct interpretation.

For a complete simple Spanish or English file-location question with an explicit ASCII extension,
Core can render positive paths from the current successful authorized listing or glob directly.
Each path must agree with its exact model-visible server citation and Run owner; directories,
zero matches, credential-bearing paths and omitted sources cannot supply this reply. Core retains
whole paths within the existing message and citation limits and labels limited pages without
claiming an exhaustive inventory. Prior completed discussion does not disable this rendering:
the model still chooses the read scope using that history. Linked clarifications, compound or
ambiguous questions, and absent qualifying matches continue through ordinary QA. The reply lists
observed locations; it does not approve artifact contents or publish new analysis.

A valid JSON lookup proposal whose only schema defect is a missing, null or empty glob pattern
may receive one read-only correction per discussion after its provider usage is durably charged.
The original malformed payload is recorded and never executed. Code validates a placeholder copy
against the exact request schema before permitting correction; malformed JSON, other invalid
fields and unknown usage remain terminal. The correction keeps the initially selected model route
and cannot propose work. It consumes the existing provider-request and token allowances.

A read-only answer may propose `source_excerpt: {source, contains}`: `source` is a supplied
artifact-event citation and `contains` is an optional literal text search. Core replaces the
model prose with an exact numbered quotation only after verifying Session/project ownership,
approved event snapshot, active registered CODE revision, SHA-256 and byte count. The source
is limited to 64 KiB of Python, R, SQL or JavaScript text; the first matching region is bounded
to 48 lines and 4,400 quotation characters. A missing match is reported honestly; unavailable,
changed, invalidated, unapproved or unsupported sources fail closed. Credential-bearing text
is not quoted. This is a deterministic read after interpretation, with no tool call, additional
provider request or new Run. Source-selection text remains a model proposal.

Selected human-review requests include their same-Run, exact-decision, later authenticated human
response when uniquely recorded. Unanswered and multiply answered requests remain explicit. This
context is explanatory evidence only; approval authority remains with the Gate, and resolved
reviews do not replace a later technical terminal cause.

The local runtime stores discussion records under `discussions/{session_id}/`. It stores a
separate hash-linked `SessionModelEvent` chain per discussion under `discussions/model-evidence/`,
with explicit Session/discussion ownership and **no fictitious Run ID**. The same RequestLedger
captures the actual provider request/response through a narrow evidence protocol. The Run event
format and analytical lifecycle remain unchanged. The Session's immutable model allowlist applies;
the fixed discussion budget is at most four read queries, six provider requests including one answer
and one optional citation repair, and 16,000 cumulative inclusive input/output tokens. Token
settlement can refuse a later request before those maxima. Read queries have their own logged
policy/tool lifecycle but create no Run envelope or Run source events. Before dispatch, Core
estimates the actual message surface and the
separately serialized output schema with LiteLLM's local tokenizer, adds a documented margin, and
passes the remaining output cap using a parameter supported by the selected provider. Remote
tokenization and schema serialization may differ, so this is an estimate rather than a guarantee.
Each durable dispatch counts as a request even if the provider returns no response. Measured input
and output are charged once; cached-input and reasoning-output counts are retained as subsets and
never added twice. If reliable inclusive usage is unavailable or inconsistent, Core records the
raw counts, charges at least the remaining balance or the known subset lower bound, and fails the
turn without another request. An output that exceeds its provider-enforced cap is charged and the
turn fails with a specific diagnostic.

After a read, subsequent model requests use a compact read-only contract. Complete selected human
messages and linked restrictions remain; assistant prose and dataset inventory are omitted. Source
IDs match the complete evidence records actually transmitted. Query-page siblings stay together,
including their pagination metadata. Relevant earlier pages may remain when they fit, but an
omitted page and a later page with a nonzero offset never imply a complete scan. If a whole new
page cannot fit an answer, Core retains its raw server records, omits the page from model context
and adds a complete context-limit error instead of supplying partial coverage.

A lookup-capable continuation reserves the exact current answer-only input estimate, the input
margin and minimum useful output before setting its output cap. Otherwise Core requests an
answer directly with a schema excluding lookup and action. Settled usage and a remaining final
request slot are checked before each server read. A fifth proposed lookup may receive query-limit
feedback within the six-request ceiling, but no fifth read executes. The reserve is not charged as
usage. Candidate selection, reservation and dispatch reuse the same framed prompt, schema and
estimate; random frame nonces cannot alter the accepted projection. Local estimates remain subject
to the provider-accounting differences described above.

Citation repair is permitted once, only after a completed read-only answer and only when its
compact projection fits the remaining balance. It uses the original route, the complete draft, the
current human message and relevant linked human context, plus complete selected verified source
records and exact server-selected source IDs. Datasets, general discussion history and the full
interpretation system are excluded. A best-effort repair reserve may reduce the response cap; it is
not charged unless repair is dispatched. A valid answer with no preceding lookup therefore consumes
one request and its actual usage only. Full human messages and linked work restrictions are preserved; if
required context cannot fit, publication fails before dispatch instead of silently truncating it.
Unknown sources remain unpublished. Prompt/reply sizes and credential scrubbing also apply.

An OS lock serializes discussion publication across processes independently of the Run lock.
A pending record precedes inference; the retry identity is hashed at rest and bound to exact
actor, message and mode. A completed retry returns the saved result without another model call.
If a process dies after provider dispatch but before publishing the reply, retry marks it failed
with an honest unknown-outcome message instead of charging another call. The user can send a new
message. Pending records remain visible and can be retried by ID after navigation/restart.
Intent is persisted before Run publication, whose own idempotency key prevents duplicate work.
Created Runs can recover dispatch under a separate OS lock with a durable CREATED-state check;
running, parked and terminal Runs continue through their existing lifecycle controls.

Questions remain available while a Run is active and do not require datasets. A new action still
requires declared inputs and an available Run slot; a 409 leaves a visible pending discussion for
explicit retry, not an automatically executing queue. API WRITE permission is required because
chat persists records and may make a paid model request. READ retains existing project scope;
no new user ownership/login system is introduced.

`terminal_diagnostic` is an operational explanation, separate from `assistant_message`: a
`summary` (at most 1,000 characters), up to eight `details` of at most 1,000 characters each,
up to eight `next_steps` of at most 1,000 characters each (default empty), and nullable
`policy_rule` (128 characters). It is projected from the verified event chain;
no second persistence store or migration is introduced. Block reasons require the unique
decision bound to the closing transition, and findings require its exact audit snapshot.
Tool errors follow that report's evidence references and hashes. Missing bindings yield a
generic explanation instead of borrowing an unrelated decision. Projection scrubs credentials
again before bounding text. These explanations do not release provisional assistant output or
enter the approved-response history used by subsequent turns.

`next_steps` contains recorded recommendations from the bound findings, not permission to act.
For partial-confinement findings, the diagnostic displays the recorded unenforced isolation
limits instead of attributing a separate computation error to confinement. The chat can prepare
an Ask draft to discuss the diagnosis and recommendations without overwriting another draft or
sending automatically. Its bounded read-only context includes recommendation excerpts and
recorded approval events; omissions from that context cannot establish absence of evidence.
A blocked Run remains terminal; corrected work requires an explicit new governed turn.

MIRA interview replies are submitted through the existing Run risk-interview endpoint and
persist as profile/question/answer events of that same Run. They are not Session work messages
and do not allocate another Run. Interview-limit review accepts or rejects continuing with
unresolved facts; it does not declare them resolved or replace execution/tool approvals.

Released conversation responses can show up to six active registered raster plots inline.
The scoped artifact API supplies verified bytes, and browser previews reject withheld/redacted
content, invalid revisions, mismatched identity/length/digest, and non-raster content. Failed or
blocked Run images remain inspectable in Artifacts as evidence, not approved chat results.

Runtime status returns `status` (`ready`, `unavailable`, `unknown`, `not_configured`), `code`,
`backend`, `summary` and `next_step`. Docker checks inspect the daemon OS and configured image,
with a three-second timeout per command and 16 KiB combined output limits; they never pull an
image, start a container or execute user code. `ready` confirms prerequisites only. Tool Gate,
approval, confinement and audit still apply. Actual container tool calls repeat the check before
review, so an unavailable daemon/image cannot consume an approval. A race after that check may
still fail during execution. A session may continue using tools without subprocess execution.

THY records `conversation.response` as provisional evidence before MIRA and the findings Gate.
The transcript and subsequent prompts release it only after a terminal `completed` transition
binds the exact findings decision to its typed audit report, revision and snapshot hash. The
response must precede that snapshot and belong to the current pass. `PASS` and `WARNING` permit
release; `REQUIRE_HUMAN_REVIEW` also requires an authenticated, nonautomatic approval for the
same decision and subject, with no recorded rejection. Pending, blocked, failed, cancelled,
unbound and unaudited responses remain absent from `assistant_message` and future context.
Approved resume records the same binding as ordinary completion. These rules also apply when
reading older records without a provisional marker; no audit logs are rewritten.

Authorized Run readers can still inspect provisional events and artifacts using the existing
evidence routes. This is a boundary for final conversation text and context reuse, not a new
confidentiality restriction on audit evidence. The chat renders a response only for a completed
turn and explains when a terminal turn has no approved response.

Conversation filtering occurs in the Session repository before the page limit/cursor is applied.
Unfiltered repository/service calls retain their previous ordering and cursor behavior. The web
follows every returned cursor, including beyond 1,000 conversations, and reports a repeated
cursor as an error instead of silently displaying a partial list.

For the legacy action-only `/messages` route, each message is at most 16,000 characters. A retry
identity is 1–128 characters and must remain unchanged when retrying an uncertain request.
A new message returns 201; an identical retry
returns the existing turn with 200. Reusing an identity for another message returns 409
`message_conflict`. While a previous Run is nonterminal, a new identity returns 409 `session_busy`.
The existing cancellation route ends a turn; it does not erase it. Failed and blocked turns also
remain in order. Sending through `/runs` with a conversation Session returns 409; standalone
Sessions keep their old behavior.

Before publishing a new turn, the server checks declared project dataset files. Missing files
return 409 `session_inputs_missing` with `{missing_datasets: [{name, path}]}` and instructions to
upload, replace or remove the declaration. Invalid/unreadable configuration and paths resolving
outside the project return a generic 409 `session_inputs_unavailable`. Retry lookup happens first:
an already accepted message still returns its Run if a source later disappears. No new Run or
turn is recorded for a refused input check. The existing project upload endpoints remain the
source of truth, shared across sessions, and preserve their validation and permissions.

This check is not a filesystem snapshot. Inspect retains responsibility for source registration;
external removal or replacement between submission and Inspect may still fail or change the
input that is registered. Already registered Run source copies remain governed by existing rules.

The existing approve/reject bodies accept an optional `expected_decision_id`. A different
pending decision returns 409 `approval_changed` before recording any answer. Legacy clients
may omit the field. The chat reads verified, redacted Run events to explain the exact review,
answers through the existing APPROVE permission, and refreshes session state afterward. It
retains no second approval store and never interprets model text as authorization.
Approval resolution takes a nonblocking local OS lock per Run, rereads the waiting decision,
and records the Gate answer and state transition before releasing it. A competing resolver
receives a conflict. Dispatch occurs after lock release so synchronous execution can reach its
next review. This lock serializes approve/reject resolution, not every filesystem or Run operation.

The prompt for a follow-up action snapshots recent original messages and explicitly recorded final
responses, with at most twelve earlier turns and 16,000 history characters. History is untrusted
context, not an authorization, and never includes model reasoning. Known credentials are
scrubbed before persistence/provider use; exports additionally pass the existing fail-closed
redaction boundary. Every new Run still has its own limits and policy checks. Full history and
artifacts remain inspectable even when omitted from the bounded next prompt.

Run creation/publication, input metadata, the inverse Session link, hash-chained response events,
and controller state survive API reconstruction using the configured local runtime directory.
Reload reads this state and resumes observation. Restart does not imply blindly repeating an
in-flight tool call; existing Run resume/cancel and lifecycle recovery apply. Artifact content
stays attached to its producing Run; this does not make every earlier file available in a later
Run's sandbox.

Sending a legacy `/messages` retry first finishes pending publications for the validated Session under its
conversation lock. This repairs an interrupted inverse Session link without creating another
Run. A process exit after publication but before dispatch can leave a created Run waiting;
it remains visible and cancellable, and is not automatically redispatched by a retry. There
is no new durable execution queue in this change. Unverifiable response history fails closed
with 409 `conversation_history_unavailable`.

The explorer reads regular files below the configured project root and rejects traversal,
absolute paths, Windows path aliases, hidden paths and filesystem links. It returns at most 200
entries per directory and a 64 KiB UTF-8 file preview. `GET /project/file` adds `sha256`,
`editable` and `read_only_reason`. `PUT /project/file` requires WRITE and accepts
`{path, content, expected_sha256}` for an existing visible regular text file. A stale revision
returns 409 `project_file_conflict`; a masked/truncated source or masked replacement is read-only,
and oversized UTF-8 content is refused. A successful response contains the exact saved content
and new revision. The UI retains its draft on conflict and guards dirty navigation.

Explorer edits change the shared project source. They do not rewrite registered dataset copies,
Run artifacts, event history, hidden configuration or credentials. Existing project inputs and
artifact routes retain their specialized editing and verified views. The write path stages a
temporary file and checks the source again before atomic replacement. API writers coordinate
through a local OS lock; external editors do not participate in that lock, so a filesystem edit
in the last comparison-to-replace interval cannot be excluded by this local architecture.

Risk facts remain immutable, versioned Run evidence. The session presents the pending question
and current declarations in chat. The optional `expected_question_id` on an interview answer
binds it to the question displayed by the client; the answer is bounded to 8,000 characters.
Risk context correction is a separate operation from approval. `POST
/runs/{run_id}/risk-interview/clarify` requires WRITE, a fixed profile `field` and the exact
`expected_decision_id` of a pending planning start or uncertainty review. The Gate-authorized
transition replaces the approval wait with an information wait without recording an approval.
The correction shares the Run review lock with approve/reject, answer and plain resume. Its
answer must name the displayed `expected_question_id`, creates a new profile version, and
re-enters intake and classification. Policy-required reviews remain enforceable even when all
facts have been supplied.

An answer closes its question only when its corresponding profile revision or required
follow-up is recorded. After an interrupted publication, the saved judgment is reused without
another model call. Exact retries must match the recorded answer, actor and current question;
an older answer cannot resume a later question. Exhausted question bounds re-enter the normal
Gate review path. Clarification retires its previous start decision through the committed
controller transition, so a recovered checkpoint cannot resurrect that superseded review.

For uncertain MIRA assessments, a bounded structured model call proposes one concrete profile
field and one relevant question from the exact assessment, full profile, current request and
prior questions/answers. The model may explicitly report that no useful question remains.
Existing test-only, offline and no-live-decision declarations remain visible to both question
selection and answer sufficiency; the prompt must not invent production use. A synthetic
`risk_classification` uncertainty no longer selects a static decision-effect question.
Each persisted question binds the assessment ID and immutable profile ID/version. Replaying
that pending question does not call the provider again. The existing total/per-field interview
bounds still apply, plus at most three uncertainty questions across profile revisions per Run.

Execution risk classification uses the same bounded question mechanism when review is still
required. The selection is persisted against the exact classification event and profile,
including an explicit no-question result, before question publication. Reconstruction reuses it
without another model request. A question cannot approve the Run or remove deterministic risk
floors. Intake extraction uses `conversation.requested_work`, not unrelated historical work
included in the general Run prompt.

Interview prompts supply the canonical meaning of each fixed activity fact independently from
the generated question. The sufficiency evaluator treats question wording as context, so a
question about mitigation cannot redefine `potential_consequences` as a controls requirement.
Follow-up phrasing receives bounded previous answers for the same field. These are instructions
to the model, not deterministic semantic acceptance: a claim that work is a test never grants
approval or bypasses risk classification. The existing insufficient-answer limits and review
path still apply.

Invalid/unavailable model output, no useful remaining question or exhausted bounds lead to
explicit human review, never automatic acceptance or lower risk. The review includes known
facts, the assessment justification, clarification rounds and the effects of approving/rejecting.
Interview-limit reviews include the exhausted field, recorded counts, last question/answer,
sufficiency reason and remaining fields. Older events remain unchanged; the UI derives only
counts and answers present in their verified history. Approving a review does not authorize
future tool calls or bypass later policy gates.


## Existing model continuity and analytical completion

ConversationInput now carries work_kind (general, evaluate_existing or train_new) and a bounded
tuple of ModelInput records. Defaults preserve old records. The classifier proposes work kind;
the current human request, deterministic scope guards and the existing Gate still control work.
Ask remains read-only and cannot create a Run.

Each reusable reference binds the source Run, exact model artifact ID/name/hash, dataset and
schema IDs/hashes, target and training-event seq/hash. Discovery is limited to the selected
approved conversation history and at most sixteen references, newest source first. Explicit
model and Run selectors must agree. More than one candidate in the selected source is ambiguous.
A failed Run's artifacts remain visible operational evidence, not approved reusable results.

Core materializes only IDs carried by the authorized evaluation task, at Plan-to-Execute. It
revalidates project, Session, request snapshot, approval, manifest registration and hashes. A
Run-local model name has the form prior/<source-run>/<source-model-id>.joblib; the source logical
name, artifact ID and physical URI remain distinct. Dataset/schema/context copies have local
lineage and explicit source identity events. Publication is idempotent only for matching Core
ownership, kind, lineage and bytes. Historical stores are read directly without changing ACLs.

evaluate_model is the evaluation specialist's only tool. Existing sandbox, policy, action-bound
approval, request budgets and resume machinery remain mandatory. The default recorded_holdout
requires Core-authored provenance for the exact inputs and the supported default-training split.
It computes accuracy, precision, recall, F1 and binary ROC-AUC when available. A missing or
undefined metric has an explicit reason; it is never fabricated or silently replaced by accuracy.
An explicit all_rows evaluation is labelled and cannot satisfy a holdout contract.

ArtifactRequirement retains exact matching by default. An explicit content requirement for
METRICS declares metric keys and optional model/data/source/methodology constraints. Completion
reads and verifies the actual registered bytes and rejects nonnumeric/nonfinite/missing values
or the wrong source revision. A planner-chosen filename may differ; a human-required filename
remains exact. Additional unsupported deliverables cause a clear plan error, not deletion.

Session evidence reserves bounded space for question-relevant artifact metadata, accounts for
framing expansion and only exposes complete visible records as citation IDs. Explicit Run
selectors scope the evidence. Artifact IDs are never citation IDs. One repair remains bounded
by the six-request discussion budget (four lookups, one answer and one citation repair); if it
fails, a terminal explanation can use only a scoped server diagnostic. Otherwise the answer fails
verification. Neither path starts corrective work.

See [verification and remaining limits](../session-model-continuity-verification.md).
