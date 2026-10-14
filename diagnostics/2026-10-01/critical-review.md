# Independent critical review of the recurrent-Run diagnosis

Review completed: 2 October 2026, Europe/Madrid. Reviewer: Sol 6.1, xhigh.

**Disposition: accept the diagnosis as a bounded basis for repair planning.** The principal
mechanisms survive independent checks. The review found one additional, independently
reproduced verifier selection defect: a cited human approval is omitted even when the candidate
is present and the request is not truncated. The main report has incorporated that finding,
the validation totals of nine deliberately red assertions and two passing controls across
three diagnostic files, and the new reproduction command.
No product repair or structural change is justified merely by accepting this diagnosis.

## 1 Scope and actual architecture

Reviewed `diagnosis.md`, `execution-contracts.md`, both initial diagnostic test files,
`recorded_verifier_evidence.json`, `source_snapshot.json`, the scanner/snapshot code, relevant
member documentation, and the actual verifier, grounding, Gate, event-surface, AgentRunner,
model-binding, tool-preflight, retry, THY scheduling and deliverable-consumer paths.

The architecture description correctly separates execution, publication, task settlement,
dependency readiness, THY deliverable validation, MIRA findings and deterministic governance.
The independent reproductions use scripted providers. They do not demonstrate how a real
model would respond, train or deserialize a model, run historical programs, or exercise the
complete production graph. No provider/network call, backend restart, original-log mutation,
historical artifact-store opening or recursive delegation was performed.

## 2 Historical chronology and source attribution

An independent read-only scan exactly matched every chain and verifier-request row in
`recorded_verifier_evidence.json`: 23 logs, 23 valid chains, 192 recorded verifier requests
across eight Runs. All 192 user prompts have 12,000 characters, a truncation marker and no
`candidate_finding` key. None contains an `audit.finding` event string. The index is a faithful
record of those markers, not a substituted provider transcript or a claim about absent calls.

Creation timestamps, Madrid conversion, event counts and terminal records agree with the
chronology. Spot checks independently confirmed:

- `run_644884…`: failed Python at 127/146 with exit 125 and Docker diagnostics; A19 CRITICAL
  at 642; GOV-101 at 652.
- `run_ac252…`: failed Python at 323, successful Python at 364, successful Experiment at 415,
  and 13 positive human approvals, all LOG_ONLY.
- `run_393460…`: the events at 109/114 are human approval responses, not unresolved requests.
- `run_4ed45…`: a KeyError at 296 is separate from exit-zero publication failures at
  336/386/436/520/570/620/670. Its Coding delegation recorded 33 model requests.
- `run_f5953…` and `run_c2b5…`: each Coding delegation recorded 40 requests and no tool starts.
- All five `early_route_refusals.json` failure payloads match their original seq 2 records.
  They establish a disallowed/unconfigured model-route refusal; the origin of the old
  configuration/allowlist disagreement remains unknown.

The 475-file snapshot digest independently matches the investigated source, with no changed
or added files in its defined source set. Git independently shows one commit, `537943a`, dated
17 September, and the stated 121-file tracked diff totals. These facts do not identify the
Python implementation loaded by an old backend. Historical omissions are direct observations;
their match to today's canonical-order/truncation mechanism does not recover old source bytes.

## 3 Principal causal claims subjected to falsification

| Claim | Independent result and limit |
| --- | --- |
| The verifier can lose its designated candidate. | Confirmed through public `build_finding_verifier`: large-event and large-reference assertions are red; the small-input control passes. Canonical key ordering followed by whole-text clipping explains the current failure. The historical candidate-object omission is independently observed. |
| A later unrelated same-tool success can erase a failed publication at task settlement. | Confirmed: real runner/manager/Gate/store produce FAILED Python A, successful Python B and a COMPLETED CodeResult task. `runner.py:1128-1147` keys the projection only by tool name. The existing single-failure control passes. |
| Failure consultation depends on final output-schema shape. | Confirmed for DataProfileOutput and the shipped EvaluationResult schema. `runner.py:1064-1069` skips the failure fold when there is no integer `exit_code`. This is a settlement defect, not a new authorization. |
| The registered-write route lacks the existing syntax repeat stop. | Confirmed: three identical invalid writes consume the three-request cap; the inline Python control stops at two. Preflight still rejects every invalid write before execution. The additional stopping property is proposed coverage for this earlier seam. |
| Tool-capable final-result retries lose useful validation fields. | Confirmed: actual next-provider input carries generic CodeResult feedback without `exit_code`. A corrected scripted result completes. The no-tools structured path preserves a different safe-feedback boundary. |
| Changed source bytes need not represent publication progress. | Confirmed: three valid changed sources yield the same missing-output obstruction until the cap. This is an already documented stopping limitation, not evidence of an unbounded loop. |

The current single-source execution, output manifests, preflight and durable request budget
controls pass. These results refute an assertion that the prior changes simply do nothing.
They also refute masking this publication error behind stdout at the tested manager seam:
the diagnostic error remains visible to the scripted model.

## 4 Additional finding: cited approvals disappear before truncation

**R1, confirmed, material to the audit repair.**
[test_verifier_approval_selection.py](test_verifier_approval_selection.py) drives a real default
PolicyEngine and Gate with an isolated synthetic human. Low-confidence capability review emits
`policy.decision`, `human.approval_requested` and a positive, nonautomatic `human.approval`.
The candidate cites the approval's exact sequence and hash. The resulting valid-log request is
short (1,380 characters in the initial probe), untruncated, and contains its candidate and the
policy decision. The complete cited approval record and its request are absent.

`runtime/mira/src/thymira/mira/agents/verification.py:56-68` excludes approval event types from
its audit allowlist. At `:193-199`, the code first admits current-surface or allowlisted events,
then prioritizes references within that already filtered set. Gate writes approval records
LOG_ONLY; `packages/events/src/thymira/events/surface.py:54-59` excludes them from the general
model surface. Citing a valid approval therefore cannot restore it at the verifier boundary.

The final desired-visibility assertion fails after all arrangement, candidate-presence,
nontruncation and hash-chain checks pass. This is an independent selection mechanism, not a
third size case and not evidence of a historical counterfactual verdict. Reserve candidate
space **and** preserve narrowly scoped authoritative cited approval/resolution evidence when
repairing this verifier. Broadening THY's general model surface is not required by this finding.

## 5 Relationships and contract status

The report correctly avoids treating all missing PNGs as one defect: provider rejection occurs
before tools, whereas the later declaration mismatch occurs after a successful interpreter.
It also separates ACL/Docker/project-input prerequisites, route-policy refusal, semantic scope
loss, computation errors, output contracts and finding verification.

The red assertions are diagnostic properties, not nine breaches of identical preexisting
contracts. Candidate adjudication requires a visible proposition and relevant evidence.
Generic runtime failure consultation is an intended settlement safeguard evidenced by the
existing single-failure regression and the evaluation prompt. Extending syntax repetition to
writes and defining publication-progress stopping are additional explicit stopping contracts.

There is still a design choice in defining which failed operation remains a task obligation.
The Python A/B witness declares an output at the tool boundary; it does not encode that output
as a complete THY Plan requirement. Its status observation is conclusive, but repair should
define required, repaired, superseded and abandoned work rather than make every exploratory
failure permanently poison a task. The report's proposed acceptance criteria preserve that
qualification.

## 6 Blast radius and barriers

`runtime/thy/src/thymira/thy/nodes/execute.py:437-439,890-891` consumes COMPLETED task status for
dependency readiness. The reproduced settlement mechanism can therefore mislead downstream
scheduling. That consequence is supported by the consumer code; a full dependent-task graph
was not executed in this review.

`runtime/thy/src/thymira/thy/graph.py:413-443` still validates required artifacts, and
`artifact_delivery.py:136-233` independently checks active typed content and declared metric
constraints. A task-level false success does not imply a whole Run will pass these barriers,
MIRA or Gate. Deterministic/preflight findings also bypass LLM suppression by design
(`runtime/mira/src/thymira/mira/verification.py:147-157`). Neither verifier defect authorizes a
tool, alters a log hash or demonstrates a Policy Engine bypass.

Sibling compaction/regulatory projections, other output schemas, fixed descriptors and
parallel-call parsing remain appropriately scoped as inspected exposure or hypotheses unless
their own boundary witness exists. The global audit/rework progress check and bounded MIRA
discovery are meaningful counterexamples to “no progress checking anywhere.”

## 7 Latent predictions and evidence limits

The predicted reappearance of missing candidates, selected-away approvals, false task
readiness, repeated invalid writes and low-information final-result retries follows from
located current conditions. The predicted model reaction and complete Run outcome remain
conditional. Fresh manifests do not establish freshness of every referenced output; the report
appropriately asks for a separate stale-output witness.

The EvaluationResult witness uses a synthetic coding specification and a failed fake
`evaluate_model` boundary with the real shipped output schema. It establishes AgentRunner's
schema dependence; it does not exercise the production evaluator catalog or worker. The
large-reference candidate is constructor-valid synthetic input passed directly to the public
verifier before flow grounding. That proves the builder's size behavior, not that those 2,000
particular synthetic references would all survive a production grounding pass. The long-event
case and raw historical omission establish the practically observed size failure independently.

## 8 Open historical hypotheses

The schema-regression attribution is supported by request chronology, newly composed parameters
and the prior offline reproduction. Missing remote error detail prevents identifying the exact
rejected parameter/provider mode with certainty. A schema rule from one official provider guide
must not be generalized to all gateways. This review did not perform network verification.

Complete-context MIRA verdicts and changed Run outcomes remain unknown. Thirteen tool approvals
contradict a literal absence-of-approval claim in that Run; they do not settle every distinct
human-oversight or compliance allegation. The report does not make that leap.

The exact historical code versions, ordering of every uncommitted repair, output freshness,
publication races and behavior of a provider violating the parallel-call setting remain open.
No additional structural diagnosis follows from those uncertainties alone.

## 9 Detection, validation and reproducibility

Independently reran the initial ten diagnostic cases: **eight intended failures, two passing
controls**. Added and reran the cited-approval case: **one intended failure**. Final unique
diagnostic inventory: **nine red assertions and two green controls across three files**.
Repeated runs are not extra cases. Independently reran the five existing execution-control
modules: **60 passed**, with one upstream event-loop deprecation warning. Ruff check and format
check pass for the newly added approval test, using per-assert diagnostic suppressions.

The other 64 passing tests are the main investigation's reported independent selection; this
review does not claim to have rerun them. The report's 124-test aggregate represents two
disjoint existing selections, not a full repository gate. Earlier full-suite or live-run counts
remain prior evidence. No full suite or deployment was performed by this review.

Reproduce from the repository root through bash, with fresh basetemp paths:

```bash
uv run --no-sync python -m pytest diagnostics/2026-10-01/test_evidence_properties.py diagnostics/2026-10-01/test_execution_contracts.py -q --basetemp=diagnostics/2026-10-01/tmp/review-reproduce-initial
uv run --no-sync python -m pytest diagnostics/2026-10-01/test_verifier_approval_selection.py -q --basetemp=diagnostics/2026-10-01/tmp/review-reproduce-approval
uv run --no-sync python -m pytest tests/thymira/test_tools_python_output_manifest.py tests/thymira/test_tools_python_source_reference.py tests/thymira/test_tools_python_write_preflight.py tests/thymira/test_agents_cumulative_turns.py tests/thymira/test_agents_tool_bridge.py -q --basetemp=diagnostics/2026-10-01/tmp/review-reproduce-controls
```

Nonzero exits for the diagnostic files are expected. The review's historical checks read the
raw logs and compared them in memory with the index; they did not overwrite original history.

## 10 Disposition and proof required for later repair

R1 is incorporated in the main report's causal, reach, prediction, detection and intervention
sections. The new diagnostic count, reproduction link and methodological qualifications are
also incorporated; no material report correction remains outstanding.
The investigation date now correctly distinguishes its 1 October start from 2 October completion.
No material contradiction requires discarding the principal diagnosis.

The proposed repair order is justified if its acceptance criteria remain explicit: complete
mandatory verifier subject and cited authoritative evidence, schema-independent settlement
with operation identity and genuine repair, then stage-aware retry/progress and consistent safe
feedback. Repairs must also preserve scoped one-shot approvals, deterministic policy and exact
deliverable/content barriers. Each changed mechanism needs its own red-to-green regression and
successful-repair control; a green aggregate or live smoke Run alone is insufficient.

No recommendation here calls for raising caps, relaxing policy, replacing the architecture or
performing a speculative refactor. Accepting the evidence permits a concrete repair design;
it does not assert that the recurrent-failure class has already been corrected.

Skills applied: python-debug, python-testing, python-testing-unit,
python-testing-integration, python-general, ruff.
