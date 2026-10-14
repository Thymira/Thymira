# Python publication retry diagnosis and verification

Date: 2026-10-01. Scope: the latest cancelled graphical EDA follow-up and its local runtime fixes.
No historical Run was resumed or rewritten; no provider calls were made for validation.

## Recorded evidence

Session: session_bcd69d6982f34449bc20bf86004d8acd.

- Initial EDA: run_d1c16be36d4b4f6b97985978c76a6e7e, 662 hash-chain-valid events.
  Its plan used data profiling without a Coding delegation. It completed.
- Graphical follow-up: run_4ed45afb6a384bbab1be24695c396b66, 698 hash-chain-valid events.
  The user cancelled it during execution. There were 25 approval requests, 24 human answers,
  23 agent pauses and 45 recorded model requests, of which 33 belonged to Coding task
  task_bc572d6c455f49fa953fcafe72e493d6 and agent_58b261f1ca6e4d278913b8bca2fe9597.
- Direct, read-only verification of manifest SHA-256 and byte lengths found no mismatches in
  4 entries for the initial Run and 37 entries for the follow-up, including retained revisions.
  Historical artifact stores were not instantiated and their scripts/checkpoints were not run.

The Coding script selected numeric columns, while its independently supplied output_artifacts
also named savings_status and property_magnitude plots. Seventeen PNG files existed in the
workspace, including numeric histograms/boxplots and a correlation matrix. They were never
registered because the batch contained nonexistent outputs. The store's atomic refusal was
correct; the duplicated output specification and ambiguous error caused the repair loop.

Concrete event sequences in the follow-up:

- 178 and 205: registered source syntax failures from literal backslash-n statement separators.
- 246: interpreter exit zero; publication failed on histogram_savings_status.png.
- 296: a later rewrite introduced a pandas KeyError while dropping an already excluded column.
- 336, 386, 436, 520, 570, 620 and 670: exit-zero publication failures on property_magnitude.
- 256, 346, 396, 446, 530, 580, 630 and 680: attempts to read missing PNG files as text.
- 694: agent closed with Cancelled from web console.

Each script rewrite changed its source hash, so exact-source repetition detection could not
identify the unchanged publication mismatch. The 33 Coding requests had not yet reached the
configured 40-turn limit when the user cancelled. Fresh limits after approvals were a separate
reproduced defect, not evidence that this particular Run had already exceeded 40 Coding calls.

## Corrections

1. A saving script can produce output_manifest_path: a fresh JSON list of the files it actually
   saves. Coding and Visualization guidance use this for data-dependent selections. Existing
   fixed output_artifacts remain compatible; combining nonempty declarations with a manifest
   is rejected. Exact required plan deliverables are still checked.
2. The manifest is bounded to 65,536 bytes and 1-64 descriptors. It shares the existing atomic
   file publisher and its containment, link, collision, media-type, kind and byte checks. The
   manifest itself is registered as LOG with source lineage, together with the outputs. Internal
   .thymira paths are rejected case-insensitively, including Windows aliases.
3. Failed publication explicitly states that Python exited successfully and reports missing and
   existing declared files. The model is instructed to repair declarations and avoid missing
   PNG reads or unrelated script rewrites. Successful manifest calls expose exact published paths.
4. write_file validates registered .py syntax and source bounds before human review. Valid
   escapes and source bytes remain unchanged; other formats and unregistered drafts are unaffected.
5. AgentRunner derives remaining task requests from owned dispatch-ledger events. Approval
   resumes, agent reconstruction, surface compaction and event-log reopening do not replenish
   recorded task usage; other tasks' requests do not consume it.

## Validation

The publication contract, turn reset and invalid-code write were reproduced in failing tests
before their respective fixes. The real-execution test uses authored synthetic mixed data,
pandas and matplotlib, then verifies a numeric PNG and the registered manifest with hashes and
source lineage. No historical code, real user dataset or paid provider was executed.

Main regression batch: 288 passed. Request-ledger/tool-bridge batch: 63 passed, with one test
repeated from the main batch. Final targeted batch: 27 passed, including two new internal-path
case tests. Total distinct regression cases passed: 352. Only dependency deprecation warnings
were emitted (NumPy/joblib and pydantic_graph event-loop access).

Covered boundaries include malformed/oversized manifests, descriptor limits, missing outputs,
path containment, internal paths, duplicate/self-referential outputs, stale manifests before
Gate, canonical legacy arguments, immutable source approvals, real PNG publication, actual
approval-resume reconstruction and durable request counting after surface replacement.

Static verification: Ruff check passed; Ruff format reports 13 files already formatted;
workspace ty check passed with zero diagnostics; Contracts: 4 kept, 0 broken;
package/invariant inventory OK; Agent Notes: valid. The existing
oversized AgentRunner module remains 1150 lines; its class decreased from 504 to 503 lines.
The new modules and the Python tool remain below the detector's thresholds.

## Limits

Offline provider fixtures do not guarantee a live model will follow the revised workflow.
The running backend must reload/restart to load changed Python modules and prompt catalogs.
The cancelled Run and its missing published outputs have not been repaired retroactively.
Script-produced declarations still require valid files; they do not prove each file was freshly
created. Uninstrumented legacy/injected providers cannot reconstruct unrecorded historical use.
The initial Run also made repeated successful MIRA searches and audit repairs; that is a separate
cost/efficiency observation, not a failing Python tool and not changed here.
