# Diagnosis of repeated Thymira Run failures

Investigation started on 1 October and completed on 2 October 2026, Europe/Madrid. This report reconstructs the local
Thymira execution system, distinguishes historical failures from current defects, and proposes
property based stabilization. It is a diagnosis before repair. Product sources, historical
Runs, approvals and reports were not changed or resumed. No live Thymira provider request was made.

The evidence supports several interacting failure classes, together with independent
environment and persistence faults. Fixing an early barrier has often exposed a later existing
barrier. There are also documented regressions. The most consequential new finding is that
**all 192 recorded MIRA verifier requests in eight historical Runs omit the candidate finding
object because their evidence projection is truncated**. The current implementation reproduces
that omission. An independent short-input probe also shows that cited human approvals are
excluded before truncation. Separately, current AgentRunner can record task completion despite an unresolved
tool failure. These are demonstrated mechanisms; their existence does not establish that every
historical policy block was wrong or that an entire Run can bypass its final governance checks.

The investigated on-disk source comprises 475 files under `runtime`, `packages` and `apps`.
Its set digest is `3a939eedef5b4c8732893fd4c1ba1d5bc3d5731da35b8fb890873c7fc087a398`;
individual hashes are in [source_snapshot.json](source_snapshot.json). This is an investigation
snapshot, not a claim about code loaded by an old backend. Git has one commit, `537943a`
from 17 September. The preexisting tracked diff spans 121 files, with 6,170 insertions and
2,245 deletions, excluding numerous new untracked files. There is no commit sequence from which
to recover every repair. Historical prompts, event sequences, chat records and verification
documents therefore carry much of the chronology.

## Evidence and confidence

- **Reproduced:** a deterministic current-code witness exercises the named boundary with a
  scripted provider. It establishes runtime behavior, not how a live model would respond.
- **Historical fact:** directly checked events, timestamps, digests, terminal states or
  request contents. A valid chain establishes integrity, not the truth of model claims.
- **Prior verification:** earlier reports document reproduction and correction. Those tests
  were not all independently rerun during this investigation.
- **Supported inference:** the mechanism and trace align, but a missing remote error or
  code-version record prevents complete causal attribution.
- **Open:** static exposure or a possible consequence lacks a witness or historical match.

Raw history is at `examples/credit-risk/.thymira/runtime/runs/<run_id>/events.jsonl`.
[recorded_verifier_evidence.json](recorded_verifier_evidence.json) contains the independently
verified 23-log inventory and safe per-request omission markers and hashes. It does not copy
historical prompts or datasets. Prior reports' artifact hash checks are attributed where used;
this investigation did not open a mutable LocalArtifactStore on historical directories.

## 1 Global explanation and actual architecture

The running product has more boundaries than a single agent loop. The current code composes:

`Session discussion → frozen work request → Run → intake → MIRA preflight → execution
classification and Gate → THY inspect/plan/execute/summarize → MIRA audit and verifier →
findings Gate → reporting and terminal state`.

A tool approval interrupts the THY pass. Core persists the wait; a later human response resumes
the same Run from its checkpoint and reconstructs the agent. A failed tool does not necessarily
fail the whole task immediately: the agent may repair it. A failed task also need not
immediately terminate THY; declared deliverables and final governance are later barriers.
Consequently, `process exit zero`, `tool success`, `task completed`, `required deliverable
present`, `audit supported`, and `Run completed` are different facts. Several incidents result
from information or meaning being lost between these facts.

| Boundary | Actual owner and code | What must remain true |
| --- | --- | --- |
| Auto, Ask and work publication | `core/session_chat.py:175-304`, `session_chat_clarifications.py:75-223`, `conversations.py:134-204` | A model interpretation cannot discard an outstanding human restriction or turn a read-only clarification into authorized work. |
| Persistent lifecycle | `core/runs.py`, `control_plane.py`, `run_resume.py`, `state/local_run_store.py` | Core owns transitions; a human answer belongs to the exact active review. |
| Composition | `core/graph/compose.py:84-128`, `graph/adapters.py:1318-1430` | THY and MIRA exchange shared state through Core; execution stays gated. |
| THY task planning and scheduling | `thy/nodes/plan.py`, `planning_contract.py`, `nodes/execute.py`, `artifact_delivery.py` | Plans constrain tasks and artifacts; dependency readiness must reflect actual settled work. |
| Model loop and reconstruction | `agents/runner.py`, `model_binding.py`, `request_budget.py` | Retry feedback must be useful; the durable budget must survive approval and history reconstruction. |
| Tool authority and execution | `tools/manager.py`, `tool_preflight.py`, `builtins/run_python.py` | Syntax/runtime prerequisites precede review; authorization binds one exact call; execution and publication are separately observable. |
| Evidence and audit | `events`, `state/artifacts`, `mira/flow.py`, `agents/evidence_projection.py`, `agents/verification.py` | Stored identities and hashes must verify, and the model must actually receive the proposition and evidence it judges. |
| Final decision | `policies/defaults/base.yaml:80-104`, `core/graph/compose.py` | Models propose findings; deterministic policy decides. A CRITICAL surviving finding triggers GOV-101. |

These owners were reconstructed from executable composition and consumers. Some repository
overview text still describes wiring as future work although it is implemented. That overview
was used as a map, not as proof of the current flow.

The global pattern is therefore not simply “the model makes mistakes.” Model-generated invalid
code and misleading declarations are real inputs, but code decides whether to preserve work
scope, provide actionable feedback, stop futile repetition, recognize successful repair and
supply a complete audit question. The current failure succession mixes weaknesses at those
boundaries with independent faults such as Docker availability and Windows ACL propagation.

## 2 Reconstruction of Runs and earlier changes

Times below are Europe/Madrid, converted from historical UTC creation records. Report dates
are not Run dates. In particular, the model-continuity report dated 30 September covers Runs
created on 29 September. Short identifiers in the table refer to the full IDs in the linked
reports and the machine-readable inventory.

| Creation in Madrid | Run and terminal record | Observed origin and later manifestation | Recorded intervention and subsequent evidence |
| --- | --- | --- | --- |
| 27 Sep, before 19:45 | Five startup Runs, three events each, failed | One route was `<unconfigured>` and four were `gpt-4o-mini`; each was disallowed by the same Session policy snapshot, version 1. Refusal occurred before work began. | [Exact failure records](early_route_refusals.json) establish a route/configuration mismatch and fail-closed behavior. The historical source/configuration that chose those routes or produced that allowlist is not recoverable from these three-event logs. |
| 27 Sep 19:45 | `run_849e4d…`, 55 events, failed | Declared `german_credit` resolved to absent `data/applications.csv`; analysis failed after review/resume. | Dataset management and review context, [inputs verification](../../docs/session-inputs-review-verification.md). This is a project-input incident, not the later publication bug. |
| 27 Sep 21:56 | `run_d277a2…`, 450, failed | Classification lacked declared profile values; searches for Articles 11/12/14 ignored corpus locations and returned no evidence. | Field values and location-aware search added, [risk/files verification](../../docs/session-risk-files-verification.md). Later unscoped searches still exposed a distinct filter mismatch. |
| 28 Sep 00:02 | `run_644884…`, 654, blocked | Python could not find Docker at seq 127/146; A19 CRITICAL at 642 led to GOV-101 at 652. Unsolicited training and invalid Python also existed. | Runtime preflight and syntax validation moved before review; initial EDA scope guard and diagnostic added, [blocked verification](../../docs/session-blocked-run-verification.md). Fixing prerequisites does not eliminate generated-code or artifact defects. |
| 28 Sep 21:46 / 22:22 | `run_267812…`, 971, completed; `run_9da148…`, 249, failed | Follow-up “que es eso” entered the work path and repeated intake/execution instead of only explaining the result. | Separate persisted discussions and Ask/work routing, [discussion verification](../../docs/session-discussion-risk-verification.md). Later clarification-chain mistakes remained possible. |
| 29 Sep 10:50 | `run_f81ed5…`, 18, failed | A staging file inherited an ACL when parent security was updated; persistence rejected it before EDA. Published request was only `Si`. | Native directory securing and linked human clarification context, [EDA failure verification](../../docs/session-eda-failure-verification.md). The ACL and scope failures are independent. |
| 29 Sep, follow-up without a Run | `session_fee18…`, two failed discussions | The clarification fix rejected a first dataset question because the model incorrectly claimed continuation of a nonexistent parent. | Initial-only continuation normalization. This is a documented regression in the preceding clarification change, not an old hidden computation bug. |
| 29 Sep 12:28 | `run_ac252…`, 895, blocked | Dataset-only current request lost EDA scope; an Experiment task ran at seq 415. Pandas failed at 323 and recovered at 364. A model CRITICAL allegation of absent approval cited budget/model selection, despite 13 approvals. | Scope and audit projection were improved, [audit verification](../../docs/session-eda-audit-verification.md). Recomputed deterministic prefix yielded A19 MEDIUM only. New investigation finds all 24 verifier requests in this Run lack the candidate object. |
| 29 Sep 16:12 | `run_5ba3b9…`, 307, failed | Mitigation wording replaced the semantic requirement for consequences. `search_regulation(framework=null)` returned mixed frameworks; mandatory EU AI Act evidence guard rejected the result. | Canonical fact definitions and auditor-owned default filter, [search verification](../../docs/session-regulation-search-verification.md). Its recorded prompt already contained earlier context fixes; stale backend alone does not explain this incident. |
| 29 Sep 18:29 | `run_1c08c4…`, 255, failed | Natural confirmations again lost the original request. Coding produced `data/eda_plots.png` while its plan required `eda_plots.png`; repeated saves overwrote charts. | Pending request binding independent of model continuation flags; bounded plot correction and preview, [preflight verification](../../docs/session-eda-preflight-verification.md). The exact deliverable guard correctly refused the mismatch. |
| 29 Sep 19:52 / 20:08 / 20:23 | `run_65b90…`, 789, completed; `run_b866f…`, 650, completed; `run_dd78…`, 208, failed | Model/evidence selection omitted the previous model. “More metrics” retrained, collided on output destinations, then produced accuracy only at the wrong exact path. | Frozen prior model references, dedicated evaluator and content checks, [continuity verification](../../docs/session-model-continuity-verification.md). Changing only the filename would still leave requested precision/recall/F1/AUC absent. |
| 30 Sep 14:18 / 15:05 | `run_14cca…`, 1,434, completed; `run_393460…`, 131, failed | EDA repeated two invalid source variants 21 times. Next Run failed planning: MODEL/content completion violated the contract, runtime-owned fields were exposed, and two planner retries used the same gateway digest without repair feedback. | Safe field feedback, model-facing plan variants and exact-source stop; later source-by-ID execution, [retry verification](../../docs/session-eda-retry-source-verification.md), [source verification](../../docs/registered-python-source-verification.md). Reviews at 109/114 were resolved; the terminal cause was technical planning. |
| 30 Sep 22:59 / 23:06 | `run_f5953…`, 890, completed with failed Coding; `run_c2b5…`, 406, failed | Each Coding delegation got 40 provider_error responses and invoked no Coding tool. Missing PNGs were downstream. Recorded requests contain the newly added composed source schema. | Plain object parameters and terminal provider-error propagation, [provider verification](../../docs/session-provider-schema-verification.md). This is a supported schema regression attribution; the remote error body was not retained. |
| 30 Sep 23:43 / 23:54 | `run_d1c16…`, 662, completed; `run_4ed45…`, 698, cancelled | Initial EDA profiled without Coding. Follow-up generated 17 PNGs but its independently guessed list included categorical plots absent from numeric-only code. Atomic publication repeatedly refused the batch; rewrites and missing-file reads followed. | Execution-produced manifests, clearer publication diagnostics, Python-write preflight and durable task cap, [publication verification](../../docs/session-python-publication-verification.md). The user cancelled after 33 Coding requests, below the configured 40; cap reset did not cause that observed count. |

The five startup refusals are separate from later dataset, audit and publication failures.
They establish the immediate denial cause; they do not establish why the old configuration
and policy snapshot disagreed. Their exact failure messages are retained without executing or
resuming their Runs.

Historical changes were not all improvements of one cause. The initial clarification rejection
and source-schema provider rejection are concrete regressions. Conversely, Docker/ACL fixes
allowed execution to expose plan, source and output mismatches. There is no evidence that
fixing the ACL caused the later pandas or artifact error.

## 3 Confirmed causes and their causal chains

### Required meaning is lost in projections between components

The historical Session path allowed a model-owned continuation flag to determine whether
earlier human scope entered the immutable current request. Short dataset/confirmation replies
then became the only authoritative work. THY and risk classification could reason about
broader project capabilities instead of the requested EDA. The failure originated at
interpretation/publication, not at the later Experiment tool. Current linked pending-work
binding addresses that specific mechanism; the existing Session regression selection passes.

The new MIRA defect is a distinct instance of incomplete projection. In
`mira/agents/verification.py:158-176`, the candidate and audit events are serialized with
`canonical_json`. Alphabetical order places `audit_evidence_events` before `candidate_finding`.
`agents/prompt_framing.py:61-73` then cuts the serialized text to fit 12,000 characters while
retaining safe delimiters. Large events consume the budget before the candidate appears.
Even with small events, an oversized candidate evidence list sorts before its `finding`,
`severity` and `title` and can cut out the proposition.

The public `build_finding_verifier` reproduction is **two red desired-property cases and one
small-input passing control** in [test_evidence_properties.py](test_evidence_properties.py).
The long-reference case invokes the public builder with schema-valid synthetic references;
it does not claim those references would pass the preceding flow's grounding check. The
large-event witness and the directly recorded historical requests independently establish
candidate loss with actual event evidence.
The raw-history scan independently confirms **192/192 requests truncated and 0/192 with the
candidate object visible**, across eight Runs. Safe digests and event sequences are preserved
in the JSON evidence index. This is an observed historical omission, not only a prediction.

The independent critical review found a second, confirmed selection defect in
`mira/agents/verification.py:56-68,193-199`: the private audit allowlist excludes
`HUMAN_APPROVAL` and `HUMAN_APPROVAL_REQUESTED`. These events are LOG_ONLY on the generic
model surface; cited-reference prioritization happens only after that exclusion. A public
Gate/verifier boundary probe records three valid Gate events, cites the resulting human
approval, and produces an untruncated 1,380-character prompt. The candidate and policy decision
are present, but the cited approval's payload/type and its request are absent. This defect
does not depend on size and survives a candidate-first truncation fix. The main agent
independently reran [test_verifier_approval_selection.py](test_verifier_approval_selection.py).
It is a current boundary witness, not proof of a historical counterfactual verdict; see
[critical-review.md](critical-review.md).

Core does build a provider-backed second-pass verifier by default when audit specs are enabled
(`graph/adapters.py:1390-1410`). The hypothesis “there is no verifier in production” is false.
The problem is what that verifier receives. A supported verdict cannot establish adjudication
of the designated proposition when that proposition is absent. This weakens the historical
verification claim, but does not prove that the original allegation was false in every case.

Grounding separately validates that references exist and match hashes
(`mira/grounding.py:156-171,188-213`). It does not establish that a budget decision proves
missing human oversight. Semantic support is delegated to the judge. A surviving CRITICAL
finding then correctly triggers the deterministic GOV-101 rule. The Policy Engine is the
manifestation point, not the source of omitted or unsupported audit content.

### Independent descriptions of the same work diverge

The source workflow originally wrote a script and independently generated inline code for
execution. The generated bytes already contained literal backslash-n separators in the
recorded responses; earlier adapter checks preserved both real and literal newlines. There is
no evidence of runtime double escaping as the source of the 21 rejections. Registered source
execution now binds active ID, digest and bytes before Gate and at execution. That removes
one duplication; it cannot make initially generated source semantically correct.

The latest publication incident duplicates a different fact: the code chooses actual numeric
plots, while another model field lists expected files. The two disagree. Interpreter exit zero
does not make absent categorical PNGs exist. Atomic registration correctly rejects the whole
batch. The duplicated output specification originates before execution, manifests at
publication, and provokes repeated code rewrites because earlier diagnostics did not clearly
separate those stages. An execution-produced manifest removes this independent guess when used;
fixed descriptor lists remain supported and can still mismatch.

Likewise, planning, agent instructions and artifact paths are separate representations. Exact
required names are checked at the THY pass boundary, after expensive generation. The path
failure at `run_1c08…` is confirmed, but relaxing it to basename matching would hide an
inconsistency rather than establish the intended contract. The more-metrics incident also
needed semantic metric content, not a filename-only fix.

### Retry policy does not consistently represent the failed obligation

Three mechanisms must be separated. The earlier planner replay lacked validation feedback;
the current no-tool structured path now supplies safe fields/codes. The earlier provider
exception path converted a rejected remote call into a model repair request; current code
stops it as provider_error. Approval formerly replenished task usage; current
`request_budget.py:17-31` counts durable owned requests and its reconstruction controls pass.

Two concrete gaps remain. Identical invalid registered `write_file` calls are not included in
the exact-source early-stop tracker, which names only `run_python`
(`tool_bridge.py:50-52,73-84,266-272`). And tool-capable `final_result` validation still replaces
field errors with `the model returned invalid CodeResult output`
(`model_binding.py:340-346`). The reported planner feedback repair did not cover every adapter.
Both are reproduced through the actual AgentRunner boundary.

Different valid source revisions can also repeat the same missing-output obstruction until
the cumulative cap. This is a confirmed absence of progress detection, already acknowledged
by the earlier report, rather than an unbounded-loop claim. Changing source bytes is not
evidence that publication advanced. The graphical follow-up's sequences 336/386/436/520/570/
620/670 match this class. Its pandas KeyError at 296 is a different computation error.

### Task completion is projected from incomplete runtime facts

`agents/runner.py:1128-1147` stores the latest tool failure/success under a key consisting
only of the tool name. Failed Python A with missing `source_a.txt`, then unrelated successful
Python B, then model `exit_code=0` yields a **COMPLETED** task although A remains unrepaired.
The logs preserve both events; the derived completion projection loses their relationship.

At `runner.py:1064-1069`, even that lossy check runs only if the final output schema exposes
integer `exit_code`. A DataProfileOutput and the shipped EvaluationResult can both complete
after failed tools. The latter accepts a model-claimed metrics ID without this settlement
seam verifying a successful producing call. This is reproduced without deserializing models
or executing real datasets. The Evaluation witness uses the shipped output schema in a
synthetic coding specification with a fake `evaluate_model` boundary; it exercises actual
AgentRunner settlement, not the complete production evaluator worker. DataProfileOutput is a
test fixture. The located schema-dependent settlement branch is shared runtime code.

The origin is AgentRunner settlement; the downstream manifestation can be false dependency
readiness (`thy/nodes/execute.py:437-439,890-891`) or an inaccurate task report. THY's declared
artifact/metric checks remain later barriers (`thy/artifact_delivery.py`, `graph.py:413-443`).
The witnesses do **not** prove a whole-Run approval bypass, and these latent defects are not
assigned as terminal causes of the historical planner/provider failures.

### Independent prerequisites and platform behavior also fail

Docker was absent in `run_644884…`; missing project input affected `run_849e4d…`; Windows
directory security propagation altered a staging file ACL in `run_f81ed…`; regulatory location
matching and auditor framework binding failed at separate search seams. Earlier records show
specific reproductions and corrections. These cannot responsibly be collapsed into a single
LLM, retry, or architectural cause. Preflight and canonical context can expose or prevent each
earlier, but the required assertions differ.

Five early startup Runs refused model routes outside one Session policy snapshot. Current
`agents/route_policy.py:72-126` enforces this boundary before provider invocation. The old
configuration/policy disagreement is an independent startup incident; refusing the call is
the intended authorization behavior. The evidence does not justify widening the allowlist
or treating it as a provider response failure.

## 4 Relationships between problems

| Apparent symptoms | Actual relationship |
| --- | --- |
| Dataset confirmations, unsolicited experiment work, repeated risk questions | Loss of immutable current scope and incomplete/incorrect semantic intake context. The fact-versus-mitigation issue remains a distinct prompt-contract mistake. |
| Missing source explanation, absent prior model, false “still blocked” explanation | Bounded evidence selection omitted identities or later review resolutions. These share projection fragility, not necessarily the same selection algorithm. |
| Python syntax errors, failed `write_file`, repeated retries | Same invalid source family can move to an earlier validating seam. The current repetition guard did not follow that move. |
| PNGs missing after provider rejection versus PNGs missing after exit zero | Independent origins. In the first no code tool ran; in the second files were generated but an invalid output list prevented publication. |
| Planner structured failure versus provider schema failure | Local response-contract rejection versus remote request rejection. They need different repair and termination policies. |
| A19 with missing Docker versus A19 MEDIUM with partial confinement | Distinct recorded enforcement conditions. The latter did not alone explain `run_ac252…`'s CRITICAL model GOV-101 block. |
| Completed Run with a failed Coding step versus false completed task after failed tool | Different status layers. Partial Run completion is documented behavior; the newly reproduced task settlement loses specific failure evidence. |
| Hash-valid artifacts/events versus reliable audit interpretation | Integrity checks answer whether bytes/links changed. They do not prove source freshness, semantic correctness, or complete model-visible evidence. |

Canonical ordering also appeared in earlier profile/schema comparison: stored profile keys
sorted alphabetically while CSV schema order remained original. That reported A18 mismatch was
corrected. The current verifier omission is another consumer-sensitive use of canonical order,
but occurs in prompt construction, not profile verification. Canonicalization itself is useful
for hashing; its order must not implicitly decide semantic visibility.

## 5 Blast radius and searched equivalents

The investigation searched actual `frame_untrusted`, serialized projections, validation-feedback,
source-repeat and task-settlement consumers across `runtime`, `apps` and `packages`, and traced
their callers. The following coverage is intentionally bounded; static equivalents are not
presented as newly reproduced failures.

| Pattern | Confirmed reach | Additional inspected exposure or barrier |
| --- | --- | --- |
| Serialize a whole object then truncate text | All 192 recorded verifier requests; current long-event and long-reference witnesses. | `mira/agents/compaction_fidelity.py:288-319` orders shadowed events before summary inside an entry; `reg_evidence.py:314-353` orders events before findings. Large inputs may remove the compared summary or finding list. These sibling consequences are static hypotheses awaiting boundary witnesses. |
| Same projection concern, with counterevidence | MIRA's generic evidence projection still can truncate, but preserves prioritized context with insertion order. | `evidence_projection.py:93-169` independently tracks complete visible event/artifact records. Session `frame_session_evidence` admits citations only for complete visible JSON records. These are useful existing mechanisms to extend, not evidence that every projection is broken. |
| Filter before prioritizing cited evidence | Current short Gate/verifier witness loses cited LOG_ONLY approval events despite no truncation. | A finding's required reference can be removed before citation ranking. Audit each specialist's typed allowlist and compaction/current-surface handling; only the human-approval case is newly reproduced. Preserve narrow privileged audit access rather than expanding every agent's surface. |
| Settlement by tool name | CodeResult tasks with independent repeated tools can lose a failure. | Applies conceptually to distinct file operations, notebook runs and exports using one name. THY exact artifact checks protect explicitly required outputs later, but do not repair task readiness. |
| Settlement by output schema | Reproduced DataProfileOutput and shipped EvaluationResult. | Inspect all catalog output schemas lacking exit_code; require runtime evidence per task role instead of adding a cosmetic exit_code to every schema. |
| Retry guard tied to one entry point | Registered Python write preflight lies outside the run_python syntax tracker. | Source-reference preflight deliberately restores the recognized Python error prefix; the suspicion that it lost the prefix was refuted by inspection and passing controls. |
| Feedback repair applied to one adapter | Tool-capable final-result retry remains generic. | No-tool planner structured feedback is corrected and its controls pass. Inventory both provider and model-binding entry paths when repairing. |
| Dynamic file declarations | Manifests solve one independently guessed output list when adopted. | Fixed lists, other file-producing tools and required-plan deliverables still need a shared producer/consumer contract. Fresh manifest identity does not prove freshness of each referenced output. |
| Code-version attribution | Current graph hashes describe nodes/edges; provenance records commit/dirty status and dependencies. | `core/provenance.py:45-62,109-132`, `thy/graph.py:84-92`, `core/graph/compose.py:72-81` do not fingerprint every loaded implementation. Exact gateway schemas/prompts help attribution, but cannot replace a loaded build identity. |

THY global rework already has a separate evidence fingerprint and no-progress check
(`core/graph/compose.py:594-632`). The hypothesis that there is no progress mechanism anywhere
is false. That mechanism is after audit/rework; it does not govern repeated source rewrites
inside a single Coding delegation. MIRA discovery is also bounded to four rounds and deduplicates
exact text/references (`mira/verification.py:54-65,103-117`). Paraphrases can still appear new;
this is a plausible efficiency exposure, not a demonstrated unbounded loop.

## 6 Latent problems and predictions

| Prediction | Confidence and concrete condition | Observable signal |
| --- | --- | --- |
| MIRA can again judge without receiving its candidate | Confirmed current defect; large tool payloads or a long evidence list suffice. Historical omission is pervasive. | Missing required candidate fields in the actual final gateway prompt despite a recorded verifier response. |
| MIRA can lack a cited approval even with a short candidate | Confirmed current selection defect; Gate records a LOG_ONLY human approval cited by the finding. | Candidate present, untruncated prompt, but referenced approval payload/type absent. A negative oversight allegation cannot be adjudicated from evidence deliberately removed. |
| A dependent task can start after unrepaired work | Confirmed task settlement mechanism; a later same-tool success or a no-exit-code result permits COMPLETED. | Task completion with outstanding output/failure identity; later missing/incorrect input in a dependent step. |
| Invalid saved Python can consume the request cap | Confirmed current witness; identical invalid registered write repeated. | Same source/diagnostic at write preflight across requests, zero approvals/executions. |
| Valid rewrites can continue despite unchanged publication failure | Confirmed current witness and historical pattern. Existing total caps still apply. | Same missing artifact set, no registered-output progress, changing source digests. |
| A tool-capable final-result shape can be repeated without useful correction | Confirmed generic feedback; actual model repetition remains contingent. | Missing safe field path/error code in the next gateway request. |
| A fresh manifest can name a stale file | Explicit known contract limit; outputs are read if present after exit. Fresh-origin attribution was not newly reproduced here. | Published bytes/lineage claimed for an execution that did not demonstrably create those bytes. Requires a dedicated stale-output witness. |
| Another bounded MIRA specialist can lose its comparison subject | Static sibling patterns above. | Complete-frame delimiters with incomplete compared summary/findings; do not accept semantic coverage until checked. |
| Later evaluation wording may remain unreliable | Current English/Spanish training-intent and prior-model bounds are conservative; ambiguous language/mixed plans remain unsupported. | Clarification/refusal rather than silent new training is the property to preserve. No new defect is claimed without a case. |

These predictions come from located conditions. They are not a generic list of concurrency,
memory, numerical or security bugs.

## 7 Open hypotheses and evidence needed

**Exact historical provider-schema rejection.** Recorded BadRequestError digests, newly composed
parameters and offline schema rejection strongly support a regression in the registered-source
change. The remote body/parameter is absent, so the exact remote rejection is unproved. The
official [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
documents a restricted schema subset, including unsupported `not`; that corroborates a
compatibility risk, not the exact Chat Completions tool mode or historical error. Verify the
actual adapter/model/mode with a contract check and, only when authorized, a tiny synthetic
live request. Do not broaden this into “one schema rule applies to every provider.”

**What a complete MIRA verification would have concluded.** The omitted candidate is confirmed.
The counterfactual verdict and changed Run outcomes are not. Preserve historical records and
compare complete-context verification on a separate fixture. An absent approval allegation
can be contradicted by recorded tool approvals while a different human-oversight concern could
still be valid; this investigation makes no legal/compliance conclusion.

**Fresh files and confinement races.** Byte/hash integrity does not establish creation by the
latest execution. A stale-file witness and an explicit freshness contract are needed. An
external file modification during publication is another static concern, reduced by workspace
locking and sandbox cleanup; no race was reproduced here.

**Parallel tool-call setting.** The runner requests `parallel_tool_calls=False`, but parsing
accepts a returned list. A noncompliant provider plus approval reconstruction needs a concrete
witness before claiming a lost/bypassed call. Restored-history guards already reject deferred
batch ambiguity; no real provider violation was observed.

**Loaded backend identity.** Some old requests visibly contain prior corrections, refuting
staleness as a universal explanation. Other reports explicitly require a restart. At the
investigation check there was no listener on ports 8000/8080/8081/8765/8766; no server was
started/stopped. Neither process age nor an on-disk hash alone proves the loaded implementation.
A startup build/source/catalog identity, persisted in each Run, would resolve this.

**Remaining historical gaps.** The five startup traces establish disallowed/unconfigured
routes, but not why their historical configuration disagreed with the policy snapshot. The
exact ordering of every local edit is also unknown. Git history cannot recover uncommitted code versions. Prior
chat final answers confirm the source-ID change followed by the schema failure and publication
fixes, but do not replace original source snapshots or remote error bodies.

## 8 Systemic weaknesses supported by the evidence

The main weakness is incomplete enforcement of contracts between already separated components.
Authorization, persistence and artifact integrity have substantial safeguards; all 23 inspected
event chains are valid, and existing boundary regressions pass. There is no evidence requiring
a wholesale architecture replacement or weakening policy to make Runs green.

However, semantically required information sometimes remains optional model context: current
work restrictions, planner completion choices, the exact candidate for a judge, and final-result
repair fields. Where runtime correctness depends on it, prompt guidance alone is insufficient.
Some consumers also derive state from too little identity: a tool name stands in for an
operation's unresolved obligation, and an output field stands in for runtime success evidence.

The same property is not consistently maintained across alternate paths. Syntax stopping covers
execution but not the newly earlier write stage; validation feedback covers structured planning
but not tool-capable final results; generic MIRA visibility filtering exists but the second-pass
judge uses a different projection. This explains why a locally reasonable correction can leave
the failure class alive elsewhere.

Large source modules and accumulated uncommitted changes make review and attribution harder,
but size alone is not a demonstrated root cause. Existing source/module extraction and import
checks are useful. Refactoring should follow a tested contract and named consumer, not precede
diagnosis or serve as a substitute for fixing the reproduced mechanisms.

## 9 Why the failures escaped earlier detection

The repository already has scripted unit/integration/E2E tests, Windows/Linux CI, coverage,
import contracts, type checks and deterministic audit invariants. “No tests” is false. The
missing signal is the relevant property across real producer/consumer boundaries and adversarial
inputs. For example, `test_mira_verification.py:409-422` checks maximum prompt length and a
truncation marker, but not that the designated proposition survives. All those existing tests
can pass while the judge sees no candidate.

| Class | Signal that should have appeared before a live Run | Suitable check |
| --- | --- | --- |
| Scope lost on natural reply | Published requested_work drops the original EDA/no-training restriction. | Whole clarification chain through persistence/reconstruction and actual classification/plan prompt; vary reply shape, intervening Ask, actor and replacement. |
| Candidate omitted | A verifier gateway request has no complete proposition/severity/selected evidence. | Capture actual final provider input; grow event payloads, references, escaping and history. Require complete mandatory fields, not merely bounded text. |
| Cited approval excluded | A valid citation names a human-approval event whose authoritative record is missing from the judge's prompt even when space is available. | Produce real Gate approval/request events with a synthetic human; pass them through actual verifier selection; assert cited evidence coverage before and after bounding/compaction. |
| Wrong completion | COMPLETED while required runtime operation has no successful settled result. | Inject A failure and unrelated B success; use several real output schemas, approvals and reopened logs; assert task/dependency outcomes. |
| Retry lacks useful feedback | Next request repeats the same schema/digest without safe error fields, or never changes the failed obligation. | Both no-tools and tool-capable adapter contract tests; distinguish request rejection from returned invalid output. |
| Publication mismatch | Exit zero plus zero registered outputs, with absent declared files. | Actual synthetic file producer through manager, publisher and THY requirement consumer; test partial/stale/dynamic outputs and exact paths. |
| Lost task cap on resume | Same owned dispatch count produces more remaining requests after reconstruction. | Durable ledger property across approval, compaction and reopening; current controls already exercise it. |
| Prerequisite absent | Docker/image/dataset/runtime unavailable before human review. | Readiness check records a refusal and no execution/approval; independent confinement tests remain required. |
| Weak version attribution | Different loaded implementations share commit/dirty/topology identity. | Record a code/catalog/schema build identity at startup and Run creation, with a regression that a relevant source change changes that identity. |

Observability is mixed. Exact gateway requests, ownership, hashes, tool status, publication errors
and resolved human reviews make much of this investigation possible. Conversely, the provider
failure record saves type digest and a coarse code, omitting the remote parameter/request detail.
Preserve a bounded allowlisted provider status/code/parameter/request ID when available; keep
credential, arbitrary response and user-input redaction. Do not solve this by logging entire
exception bodies or chain of thought.

CI checks committed content, whereas the investigated feature and repairs have accumulated in
the local uncommitted tree. Earlier targeted counts establish their selected behaviors; they
do not establish a complete integration gate over the newest combined source. The 29 September
full non-slow run was reported as 3,928 passed with a Docker prerequisite setup error, followed
by an isolated passing rerun; it must not be relabeled a clean full pass.

## 10 Prioritized stabilization and proof of correction

No intervention in this table has been applied by this investigation. Preserve the evidence
snapshot and turn the red diagnostic properties into ordinary regression tests during repair.

| Priority and purpose | Intervention justified by evidence | Proof required before calling the class corrected |
| --- | --- | --- |
| P0 recover audit correctness | Assemble verifier prompts by complete typed records with reserved space for proposition, severity and binding; explicitly represent omitted evidence. Admit the authoritative cited approval/request records before ranking, with narrowly scoped audit access. Reuse existing visibility ideas instead of truncating canonical JSON text. Audit compaction/regulatory sibling projections. | Both red size cases and the short cited-approval witness pass over varied payload sizes/reference counts/escaping. Mandatory subjects and required cited evidence always survive or the runtime returns insufficient evidence without claiming support. An isolated historical projection comparison confirms previously omitted candidates are now present; historical Runs remain unchanged. |
| P0 recover task correctness | Settle tasks from authoritative tool/operation evidence for every output schema. Preserve failure identity and an explicit successful repair/supersession relation; independent success must not erase failed required work. | Python A failure/B success and Evaluation/Data witnesses stay failed/unresolved; a genuine corrected A can settle successfully. Run through dependency scheduling, approvals and restart. Optional exploratory failures need an explicit abandon/irrelevant outcome rather than permanently poisoning every task. |
| P1 eliminate futile-retry classes | Represent error stage and repairability explicitly: configuration/provider request, returned schema, source syntax, process, publication and deliverable failure. Reconstruct bounded attempts/progress for the failed obligation across resume. Extend syntax repetition coverage to registered writes. | Same invalid write stops after the configured repeat bound; valid changed source remains eligible. Same missing-output obstruction stops without useful progress; a real output repair advances. Total budgets never replenish and one-shot Gate authority is unchanged. |
| P1 restore feedback across adapters | Send the same bounded field-path/error-code feedback through tool-capable final_result validation as through the corrected structured planner path. | Missing `exit_code` witness names the safe missing field in the actual next gateway request; values/secrets/arbitrary keys remain excluded. Exercise planner, Coding and Evaluation output paths. |
| P1 eliminate duplicated output expectations | Bind plan requirements, execution source and publication contract explicitly. Prefer execution-produced manifests for data-dependent output sets, while preserving human-required exact paths and content constraints. | Synthetic numeric/categorical production publishes only created outputs and passes actual THY consumers. Wrong path/missing metric refuses early or receives a bounded targeted correction. No basename fallback or fabricated metrics. Define and test whether stale existing outputs may be reused and with what lineage. |
| P1 prevent schema regressions | Test actual catalog schemas at each configured gateway/mode boundary, keeping locally enforced invariants separate from supported provider schemas. Add a small provider-schema acceptance test when authorized. | Invalid requests close once with safe typed evidence. Supported object schemas reach the configured gateway. Local exact-source, approval and provenance restrictions still reject invalid combinations; no generic sanitizer silently deletes constraints. |
| P1 make code attribution reliable | Record a startup implementation/catalog/schema identity and bind it to Run creation alongside commit/dirty/dependencies. Make the loaded identity inspectable. | A relevant source/prompt/schema change changes the build record. A Run demonstrably belongs to the loaded version used for its tests. No need to guess whether a restart occurred. |
| P2 stabilize the integrated product | Establish a small scenario matrix combining Session routing, intake, plan, manager, publication, THY settlement, MIRA projection and Gate; add fault injection at each seam. Then run the repository's complete applicable gates on a versioned source state. | Basic EDA, graph follow-up, training, prior-model evaluation, read-only explanation and cancellation/review/resume retain their properties under injected failure/reconstruction. Verify persisted logs/artifacts and mandatory model-visible context. A live synthetic smoke Run is supplementary evidence, not the proof by itself. |
| P2 reduce semantic and diagnostic fragility | Keep canonical activity facts separate from elicitation wording; record verdict context coverage and safe provider error attributes. Review text/reference based MIRA deduplication only if measured repetition justifies it. | Scoped negative declarations are judged against the canonical fact; omitted information is not evidence of absence. Diagnostics name first causal stage and exact review resolution. Efficiency changes preserve deterministic findings and policy authority. |

Order matters: repairing evidence and settlement first prevents a misleading green Run from
being treated as validation. Retry and output-contract changes then remove costly repeated
obstructions. Integrated scenarios and source attribution establish whether later fixes preserve
earlier invariants. Raising turn limits or weakening exact-deliverable/policy checks does not
meet any of these acceptance conditions.

## Validation performed in this investigation

- Independent direct verification: **23/23 historical event chains valid**; **192 verifier
  request records across eight Runs**, all truncated and all missing the candidate object.
- Existing current-code selections: **64 passed** for Session clarification/preflight,
  structured validation feedback, MIRA grounding/verifier and chat evidence; an independent
  **60 passed** for Python manifest/source/write boundaries, cumulative turns and tool bridge.
  The selected module sets are disjoint: **124 existing tests passed**.
- The five new diagnostic Python files pass `ruff check` and `ruff format --check`.
  Rehashing the 475-file investigated source set reproduces its original digest.
- New diagnostic properties: **9 deliberately red cases and 2 passing controls** across
  three files. The main agent independently reran the execution witnesses: six red and one
  passing control, and the cited-approval witness: one red case. Repeated runs are not added as extra cases. One upstream event-loop
  deprecation warning appeared in the execution selection.
- Commands to reproduce, from the repository root through bash:

  ```bash
  uv run --no-sync python diagnostics/2026-10-01/inspect_recorded_verifiers.py
  uv run --no-sync python -m pytest diagnostics/2026-10-01/test_evidence_properties.py -q --basetemp=diagnostics/2026-10-01/tmp/evidence-properties-check
  uv run --no-sync python -m pytest diagnostics/2026-10-01/test_execution_contracts.py -q --basetemp=diagnostics/2026-10-01/tmp/execution-properties-check
  uv run --no-sync python -m pytest diagnostics/2026-10-01/test_verifier_approval_selection.py -q --basetemp=diagnostics/2026-10-01/tmp/approval-property-check
  ```

  Each basetemp must be a fresh diagnostic path if concurrent execution is used. A direct
  `pytest` invocation of the execution diagnostic initially failed collection because the
  external diagnostic directory did not put the repository's `tests` fixtures on sys.path;
  the documented `python -m pytest` invocation independently reproduced the intended cases.
  That collection error is not a Thymira runtime failure.

The red cases are retained outside normal `testpaths=["tests"]` so diagnosis does not silently
change the product gate. Some are violated correctness properties; repeat/progress assertions
also express proposed stopping properties, not promises the existing code already makes.
No full-suite, new live-model conversation, real historical script, new Docker run, deployment
or backend restart was performed. The earlier large test totals are reported as prior evidence,
not tests executed again here. No original source, key, model binary or user data was copied
into this report.

## Orchestration and critical review

The main investigation retained architecture, hypothesis integration, raw omission confirmation
and conclusions. One Luna xhigh subagent collected chronology and safe historical markers;
one Sol 6.1 xhigh subagent investigated execution contracts and produced
[execution-contracts.md](execution-contracts.md). Both were instructed not to repair product
code or recursively delegate. Current execution witnesses and historical verifier counts were
independently checked by the main agent. A separate Sol 6.1 xhigh review of the integrated
report and witnesses is the final critical check; its findings and disposition are recorded in
[critical-review.md](critical-review.md).
That review accepts the diagnosis as a bounded basis for repair planning. Its additional
cited-approval selection defect is incorporated above and independently reproduced by the
main agent. No material contradiction remains within the stated evidence limits.

Skills applied: python-debug, python-testing, python-testing-unit,
python-testing-integration, python-general, ruff, write-page. Source-set capture and the read-only historical scanner
are diagnostic instrumentation only.
