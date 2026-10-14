# Dynamic Python publication contract

## Problem

`run_python` validated an `output_manifest_path` during publication but did not retain its
logical output declarations in terminal tool evidence. Task settlement and retry reconstruction
therefore treated dynamic publication failures as unknown. A fresh manifest could also claim a
pre-existing workspace file that the current execution had not produced.

## Decision

Persist a bounded `output_declarations` sidecar on `ToolResult` and its terminal event, using the
existing output descriptors. The manager revalidates descriptor types, canonical unique paths,
kinds, binding to the executed call and the exact partition with publication progress before
recording it. Unknown declarations remain absent. Validated declarations survive every subsequent
publication failure. The sidecar contains only logical outputs; the manifest remains atomically
published LOG bookkeeping.

Task-completion consumers share `declared_outputs_for_attempt`. Retry identity ignores fresh
manifest filenames, treats declaration order as a set, and retains earlier output obligations
when a correction omits or renames a declaration. Only a strict reduction of the best missing set
opens another publication band, counting that attempt as its first failure. Repair can match the
same logical source path or a distinct registered source only when the later authoritative
manifest covers every earlier path-and-kind declaration in the same task and Run. Source
registration and approval still authorize the exact executed bytes independently.

Before execution, `run_python` captures per-file hashes and stat fingerprints for fixed outputs
or a dynamic workspace inventory. A logical output must be new, content-changed or observably
rewritten during this call. An unreadable previous file cannot establish freshness. Copied quota
workspaces accept only new or content-changed bytes because export can change host metadata
without a Python write. An unchanged registered output can be reused with `reuse_artifact_id`;
the runtime checks active same-Run ownership, exact path, kind, digest and bounded stored bytes,
and records the reused artifact and executed source in lineage.

The coding and visualization prompts build dynamic manifests from the same loop that saves the
actual files. Deliberate fixed contracts and human/planned requirements retain their independent
authority. THY requires later successful, verified task-owned publications for every failed output,
including outputs beyond a plan's subset. Untyped `write_file` preserves its existing path-only
repair contract, while the plan enforces the later artifact kind; it does not inherit Python's
`OTHER` default. Evaluation retains its existing content and final-reference validator.

## Alternatives considered

Reconstructing missing declarations later from task prose or mutable workspace state would not survive reopening
and could bind evidence to another execution. Treating unknown declarations as an empty set would
allow an unverified failure to close. Counting manifest LOG as a logical output would make fresh
manifest filenames obligations and distort retry progress. Inferring freshness from producer
assertions or manifest timestamps would not establish which output bytes the call produced.
Deleting old user files, adding a storage layer or a second retry/metric validator is unnecessary.

## Consequences

Dynamic publication now carries exact declarations through manager serialization, persisted events,
settlement and retry reconstruction. A zero process exit does not turn publication failure into
success. Missing/present progress is not a partial artifact registration. A partial repair, an
older publication, another task's output, a wrong path/kind or an unverifiable manifest cannot
complete the failed batch. Atomic publication, confinement, durable budgets, THY/MIRA import
independence and exact source approval remain enforced.

On copied workspaces, a same-byte rewrite cannot be distinguished from untouched bytes. A prior
registered output therefore needs explicit verified reuse; an unregistered prior output needs new
or changed bytes. This is conservative freshness evidence, not a claim that all same-byte rewrites
are observable. No prior workspace files, artifacts or historical runs were removed or rewritten.

**Changed files.** The work-05 contribution spans 21 files: 12 production Python files, four test
modules and five documentation/prompt files. Production changes are in
`runtime/tools/src/thymira/tools/{models,results,result_evidence,publication_progress,manager}.py`,
`runtime/tools/src/thymira/tools/builtins/{run_python,python_arguments,python_output_manifest,python_output_freshness}.py`,
`runtime/agents/src/thymira/agents/{task_completion,retry_policy}.py` and
`runtime/thy/src/thymira/thy/task_completion.py`. Tests are
`tests/thymira/test_agents_runner_settlement.py`, `test_tools_python_output_manifest.py`,
`test_tools_python_manifest_integration.py` and `test_thy_task_completion.py` in the same directory.
Documentation is this note, `CHANGELOG.md`, `runtime/tools/README.md`, and the coding/visualization
prompts under `runtime/thy/src/thymira/thy/agents/prompts/`.

**Independent review.** The sole Luna implementer was inactive during the main review. The initial
root reproduction was three red cases. A later clean review checkpoint had 12 product failures,
four passing controls and 73 deselected cases; all identified gaps were returned to the same
implementer and corrected. A final one-case red regression proved that failed untyped
`write_file` repair worked against the preserved pre-05 consumer but failed against the first
work-05 consumer. The narrow compatibility correction passed independent review. The first completed serial suite
then found a diagnostic regression: reserved `.history` declarations were rejected atomically but
the concrete path reason was hidden by the sidecar round-trip exception handler. Moving the
canonical-path check outside that handler preserved the established reason without changing
validation or publication. The root verified that this sole moved line exactly reconstructed the
prior frozen hash when reversed; the other 15 Python hashes were unchanged. The implementer was
inactive for this final review. These red checkpoints are separate evidence, not additional passing instances.

**Final focal validation.** The final frozen code passed 128 ordinary instances: 35 new instances
and 93 existing controls, including the adapted real graph producer control. The root run took 19.02s and had one
existing Pydantic event-loop deprecation warning. Coverage includes actual numeric-only and mixed
Matplotlib producers through manager publication and the THY artifact consumer, full/partial
repair, exact path/kind/hash rejection, malformed/copied/reopened sidecars, freshness/reuse lineage,
monotonic retry bands, registered source revisions, syntax/process separation and JSONL recovery.
The separate existing `test_metric_delivery.py` controls exercise accuracy-only content against
precision/recall/F1/AUC requirements in the complete suite. Repeated runs are not added to these counts. Evidence: `.tmp-publication-review/root-final-focus.xml`.

**Complete suite.** The final frozen serial run completed with **4,186 passed, five failed,
78 skipped and 62 warnings in 812.55s**: 4,269 collected instances, zero collection errors.
Evidence is `.tmp-publication-review/root-final-all.{log,xml}`. The five failure nodes and their
assertions match the previously recorded snapshot/conversation failures; there are no additional
failing nodes against that prior inventory. The old serialization-injection control, reserved
`.history` control and all four metric-content/provenance controls passed. The first completed serial suite, preserved
as `root-before-history-all.{log,xml}`, reported 4,185 passed, six failed, 78 skipped and 62 warnings
in 816.23s. Five nodes matched the previous snapshot/conversation failures; the extra diagnostic
regression above was corrected. This completed pre-correction result is not relabeled as final. The earlier serial attempt was stopped
before completion for the compatibility correction and has no complete-suite count. A parallel
attempt stopped at collection with three errors because existing parameter IDs contain random
UUIDs and workers collected different node IDs; it ran no tests. Both attempts' evidence is retained.


**Remaining complete-suite failures.** These retain their original scope for follow-up work 08/09.
The snapshot was not refreshed.

```text
tests/thymira/test_acceptance_demo_run.py::test_demo_run_records_what_the_model_saw_and_what_it_left_behind
tests/thymira/test_conversation_e2e.py::test_session_upload_reaches_real_eda_tool_and_survives_reconstruction
tests/thymira/test_conversation_e2e.py::test_final_governance_controls_response_visibility_and_followup_context[WARNING-None]
tests/thymira/test_conversation_e2e.py::test_final_governance_controls_response_visibility_and_followup_context[REQUIRE_HUMAN_REVIEW-approve]
tests/thymira/test_conversation_e2e.py::test_real_agent_response_reaches_followup_provider_after_restart
```

**Source checks.** Ruff source lint and formatting passed (744 files already formatted); strict
`ty` reported zero diagnostics against a zero baseline. All four import contracts passed. Source
YAML passed. The literal global `just check` remains blocked by 147 Ruff findings in retained and
generated temporary evidence trees; global formatting reports 37 files requiring formatting and
762 already formatted, and global YAML reports CRLF fixture configurations. These are pending
global gates, not waivers or a green full gate. Package invariants, all 16 skills, roadmap agreement, Agent Notes, the offline lock (251 packages)
and `git diff --check` passed. Final note validation and whitespace checks passed again after recording the suite. All 16 frozen
Python files and all eleven original diagnostic artifacts retained their expected SHA-256 hashes.

**Reproduction commands.** Run in Bash from the Thymira repository with fresh isolated basetemps.
Replace recorded basetemp paths with new paths before repeating these commands.
Regression execution uses synthetic data/ScriptedProvider, offline dependencies and snapshot replay;
no paid calls, server restart, deployment, commit or push was performed.

```bash
export THYMIRA_SNAPSHOT=replay
export LITELLM_LOCAL_MODEL_COST_MAP=True
uv run --offline pytest -q \
  tests/thymira/test_agents_runner_settlement.py \
  tests/thymira/test_tools_python_output_manifest.py \
  tests/thymira/test_tools_python_manifest_integration.py \
  tests/thymira/test_thy_task_completion.py \
  tests/thymira/test_tools_python_source_reference.py \
  tests/thymira/test_tools_manager.py \
  tests/thymira/test_tools.py::test_run_python_prevalidates_every_output_name_before_publishing \
  --basetemp='<fresh-external-focal-directory>' \
  --junitxml=.tmp-publication-review/root-final-focus.xml
uv run --offline pytest -q \
  --basetemp='C:/Users/carlo/AppData/Local/Temp/thymira-work05-root-final-serial-20261003-b' \
  --junitxml=.tmp-publication-review/root-final-all.xml
uv run --offline ruff check packages runtime apps adapters scripts tests .agents/skills
uv run --offline ruff format --check packages runtime apps adapters scripts tests .agents/skills
uv run --offline python scripts/ty_ratchet.py --strict
uv run --offline python scripts/lint_imports.py
uv run --offline yamllint .github packages runtime apps adapters scripts tests .agents .yamllint.yaml
uv run --offline python scripts/check_package_invariants.py
uv run --offline python scripts/validate_skills.py
uv run --offline python scripts/check_roadmap.py
uv run --offline python scripts/validate_agent_notes.py
uv lock --offline --check
git diff --check
export UV_OFFLINE=1
just --no-dotenv --shell 'C:/Program Files/Git/bin/bash.exe' \
  --clear-shell-args --shell-arg=-uc check
```

**Execution limits.** No Docker/provider-schema tolerance was added. Eleven original diagnostic
artifacts are preserved. The runtime exposes the Luna model selector used for implementation;
the main-agent model selector is unavailable for independent verification in this environment.
