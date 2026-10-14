# Basic EDA publication and coherent closure

## Problem

The reported October 7 Run corrected an invalid Python write, then executed successfully without
output declarations. Three PNGs existed only in the workspace. Its final artifact references
failed validation. Superseded code/resume revisions also polluted active integrity evidence.
Unresolved task failures could disappear from THY's error when the plan declared no outputs.
Generic Python cannot establish the bounded descriptive scope required for uncertain findings review.

## Decision

Give claimed, unpublished Python outputs one durable, task-owned final-output correction within
the existing request limit. Preserve Gate approval, tool-failure settlement and final validators.
Never discover or import undeclared workspace files. Report unresolved task failures, excluding
successful code-owned plot recovery. Exclude archived revisions from active-manifest observations
and review rows while retaining verification of every archived byte.

Extend the existing deterministic profile_dataset with optional include_plots. Render PNGs from
its computed profile values, using an isolated Agg figure; atomically publish the unchanged JSON
profile and PNGs through the existing artifact batch API. Each chart links to that profile.
Display at most 12 distribution/correlation columns and disclose that scope in chart titles;
the JSON retains all selected columns. Default profiling remains unchanged. Planning and Data
instructions use this tool for basic EDA; custom analyses still use policy-gated Python.
The finding verifier must describe and cite the actual completed action alongside the profile.
No finding is dropped, severity reduced or governance exception broadened by these changes.

## Alternatives considered

Automatically importing existing PNGs would bypass execution freshness and output declarations.
Increasing turns or repeating final validation indefinitely would replenish failed work without
bounded progress. Accepting arbitrary Python as deterministic profiling cannot prove descriptive
scope. A separate chart tool would duplicate the profiler's already verified calculations and
capability. Ignoring all task failures once any matching file exists would hide failed execution.

## Consequences

A basic EDA has a bounded, registered chart path and remains eligible for the existing explicit
human review of grounded uncertain findings. It does not automatically approve that review or
change any terminal historical Run. Custom Python still requires declared verified publication.
Regressions cover publication correction/refusal/budget exhaustion, valid PNG bytes and lineage,
selected/empty columns, archived revisions and integrated Core/MIRA reviewed completion.
Two older graph tests now assert that valid preexisting files cannot erase dispatch or ownership
failures; their artifact matching assertions remain. Python and artifact storage run for real;
model responses are scripted in tests. A manual isolated replay with the reported 998-row,
21-column German Credit CSV published the profile and two real PNGs with valid artifact/event
hashes. No production approval was fabricated and no historical Run was rewritten.

Validation: the combined focused suite passed 366 tests (28 existing numeric/deprecation warnings).
After exposing the published chart paths in the model-facing text, its four targeted PNG and
Core/MIRA cases passed separately; these are not added to the combined count. Ruff checks and
formatting pass, ty has zero diagnostics, and all four import contracts hold. AgentRunner is
505 class lines / 1133 module lines, below its measured 507 / 1134 baseline. The local API was
restarted through uv after confirming all 28 stored Runs were terminal. Health and an authenticated
live profile_dataset descriptor confirm that the new include_plots argument is loaded. Its normal
startup rotated the local API credential; full live model-driven EDA remains to be exercised.


**Follow-up: concurrent Run projection reads.**

The new live Run `run_3e5d50a7300e4525a4b9ca88ad4b64a1` completed the Data agent and
profile_dataset, publishing a report and two PNGs, all with verified artifact hashes. Its
542-event chain is valid. It failed subsequently during auditing with projection read-back
failed. The historical error omitted its filesystem/JSON cause, so its precise OS error cannot
be reconstructed from the recorded terminal diagnostic.

A real Windows concurrency reproduction exposed readers rebuilding run.json outside the
append lock, causing projection read-back disagreement. Two gated regressions also failed
before the fix: get and version replaced version 2 with their stale version 1 projection.
Both now hold the existing per-Run lock across snapshot, comparison and reconstruction.
Atomic write/read-back verification moves unchanged into one persistence function; actual
read-back failures retain their underlying cause. The local store remains single-writer per
process composition; this does not add coordination between independently instantiated stores.

Ignoring read-back errors or blindly retrying stale snapshots would conceal data disagreement.
The fix preserves hash verification, optimistic version conflicts, ACLs and terminal history.
A permanent read denial still raises and leaves its already appended event verifiable.
The repaired Windows stress replay completes 100 writes and 800 reads with 101 verified events.
The focused state, durability, dispatcher, lifecycle and Core/MIRA suite passes 122 tests with
one existing pydantic_graph warning. Ruff and formatting pass, ty has zero diagnostics and all
four import contracts hold. LocalRunStore shrinks from its measured 522 to 495 class lines.
These are focused checks, not a full repository suite or a fresh live model-driven Run.

The API was reloaded after confirming no active Runs. Its existing token file was absent,
so normal startup minted a new credential. Health and authenticated read-back of the reported
Run both return HTTP 200. Its terminal FAILED status and all 542 original events remain intact.
