# Attribute audit regulation reads without widening execution scope

## Problem

The real EDA Run `run_758b895f368d43b3ac38b705c40b4181` recorded one successful profile call
and 24 MIRA regulation lookups. Core treated every completed tool as a profile action, so the
read-only audit queries made bounded descriptive execution scope fail. The same Run also had
an independent confirmed A18 correlation finding. Correcting attribution alone cannot erase
that finding or authorize PASS.

## Decision

Inject audit-tool capabilities from the separately composed MIRA registry into normal and
terminal Core dependencies. Accept `search_regulation` only with regulation-store-only access,
no side or external effects, and no prohibited model-work risk tags. Require a unique paired
authenticated tool start/completion with matching call, task, Run, tool identity and subjects,
enclosed by an authenticated MIRA agent start and successful completion. MIRA's authenticated
SYSTEM owner is valid; an AGENT actor kind is not assumed.

Keep every completed-tool hash and the used capabilities in the exact review binding. Require
at least one safe profile action. Unknown, unsafe or unattributed calls remain outside scope;
training and experiment evidence still excludes bounded descriptive review. Confirmed critical
findings, audit completeness, freshness, artifact verification and exact human approval retain
their existing checks. Core composes MIRA; THY gains no MIRA dependency or authority.

## Alternatives considered

- Filtering by tool name or an informal audit stage would trust unowned calls.
- Dropping audit completion hashes would weaken the exact persisted evidence binding.
- Sharing THY's registry would erase the independently composed audit capability boundary.
- Relaxing confirmed findings or automatically approving review would cross policy authority.

## Consequences

The regression uses a real local ToolManager profile call and a ScriptedProvider-driven MIRA
agent calling the real regulation tool. Its initial RED confirmed that both genuine calls were
present while `all_safe` remained false. Negative checks cover absent capability metadata,
missing agent or tool start, missing profile, unsafe effects, wrong actor and wrong task.
The scope regression passes (1 test, 2.96 seconds). The focused scope, Core composition and
integrated-session suite passes (47 tests, 160.34 seconds). Scoped Ruff/format passes for six
files, typecheck is zero against baseline zero, all four import contracts hold (437 files,
1,801 dependencies), and Agent Notes validation passes. The final broad post-scope THY/Core/policy/API/lifecycle run passes: 970 passed,
2 Windows symbolic-link skips, 4 warnings in 174.00 seconds.

Read-only reverification of the real audit snapshot ending at event 846 accepts all 25 completed
calls: one profile and 24 MIRA lookups. All 25 completion hashes remain in the evidence,
`all_safe` is true, and no training or experiment event is present. The auditors' ordinary
OpenAI model calls remain part of the recorded audit. See
`diagnostics/historical-artifact-followup/real-audit-action-reverification.json`.

The original terminal BLOCKED Run and its history remain unchanged. A18's separate correction
passes read-only reverification of its recorded profile; this attribution change supplies no
approval and does not demonstrate a fresh completed two-turn real-provider scenario.
