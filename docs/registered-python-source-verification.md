# Registered Python source execution verification

This follow-up addresses the duplicate source submission behind the EDA syntax loop. It does not rewrite any historical Run or repeat external model calls. The earlier history and cost evidence remain in [the EDA retry record](session-eda-retry-source-verification.md).

## Contract

The agent registers a script through `write_file(kind="code")` and uses its returned `artifact_id` as `run_python.source_artifact_id`. Execution no longer needs the agent to retype the saved source. The tool resolves only active `.py` CODE artifacts in the current Run, verifies registered bytes and syntax before approval, and binds source identity and content into the canonical intent. Execution rechecks the source and stages that same program; a divergent workspace copy is not executable input. Output artifacts retain the source as a lineage input. Inline `code` remains compatible for existing callers.

The model-facing write result previously omitted its registered artifact ID even though the typed result contained it. A failing regression reproduced this omission. The result now includes that ID when registration occurred. Coding and Visualization prompts use the registered-source workflow. The repetition guard recognizes immutable source IDs in live and restored conversations as well as exact inline source bytes.

## Verification

The real Coding catalog, tool registry, Tool Manager, artifact store and approval/resume machinery are exercised with ScriptedProvider and a sandbox that captures staged interpreter input. The model selects the ID solely from its visible tool response. It does not obtain the ID through test-side store lookup. Tool tests cover source lineage and preflight rejection. No Docker process or external model is required.

The compatibility bundle covered 490 distinct cases across tools, file contracts, agents, history, approval/resume, Coding, Visualization and MIRA checks. The first run passed 489; its only failure expected the original opening phrase of tool guidance. Preserving that opening phrase fixed the focused rerun (1 passed). Earlier overlapping focused runs are not added to this count.

The final combined reference-specific suite passed all 16 cases (11 tool-boundary cases and 5 agent/workflow cases). Coverage includes exact staged bytes and output lineage, the model-visible source ID, approval/resume despite a changed workspace copy, invalid-source repetition across live/restored history, tampered/invalidated or foreign-Run artifacts, credential rejection, mixed/forged arguments, empty/invalid source, canonical legacy arguments, and fresh approval for a new revision. One dependency deprecation warning about event-loop lookup was emitted. In total, 506 distinct cases were validated across the compatibility bundle and these new tests.

Static verification: Ruff lint and format passed on all 11 touched Python files; repository-wide ty passed, the package/invariant inventory passed, and import-linter kept all four contracts with none broken. The size detector reports the existing ToolManager class (918 lines) and manager module (1,298 lines) unchanged; extracting file argument contracts reduces the file-tools module from 662 to 621 lines. New source modules do not cross the thresholds. An independent focused security review found no P1/P2 issue.

The full just check gate and a live model/Docker trial were not run. Checks use isolated temporary stores and do not instantiate a writable artifact store over historical Run data.

## Limits

This removes retranscription from the registered-source workflow; it cannot guarantee that the initially written Python is correct or that a live model always chooses the intended tool arguments. Invalid scripts still fail before review, and the existing repetition limit remains a cost backstop. Source selection is local to one Run; a workspace path, basename or artifact from another Run is not an authorized substitute. Existing historical results and approvals are unchanged. Source identity remains in the tool.started canonical arguments even when a later process failure is normalized to the generic failure result. Successful execution values also carry source identity. A separate process with an older artifact-store snapshot can retain the original revision, but cannot substitute newer workspace bytes for the approved program; this change does not promise cross-process manifest invalidation propagation.

The API processes observed during verification started before these edits. Restart the API to load the updated code before a new live trial.
