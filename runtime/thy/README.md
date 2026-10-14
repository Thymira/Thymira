# thymira-thy

THY — the production / data-science orchestrator (ThyGraph: Inspect -> Plan -> Execute -> Summarize) coordinating Data, Coding/Execution and Experiment agents in the MVP.

| | |
|---|---|
| Import name | `thymira.thy` |
| Owner (MVP roadmap) | P2 (THY / Agents) |
| MVP priority | P0 |
| Location | `runtime/thy/` |

## Status

Implemented and tested (`tests/thymira/test_thy_*.py`): `ThyInput`/`ThyState`/`ThyOutput` and
`AgentTask`, and a compiled LangGraph `StateGraph` (`build_thy_graph`/`run_thy`) wiring
`START -> inspect -> plan -> execute -> summarize -> END` with four real nodes — `inspect_node`
(`load_project_context`), `plan_node` (which gates THY's own plan through
`gate.check_action('plan.proposed')` + `allows_execution`),
`execute_node` (dispatches `ThyState.plan` through `Delegator`/`AgentRunner`) and
`summarize_node`/`compare_experiments` (the `Recommendation` and its report artifact). The Data,
Coding/Execution and Experiment specs (`DATA_AGENT_SPEC`, `CODING_AGENT_SPEC`,
`EXPERIMENT_AGENT_SPEC`) live in `thymira.thy.agents`. Each node stays a bare phase-advance
placeholder when its build-time dependency (`project_dir`, `gate`, `artifact_store`) is omitted
from `build_thy_graph`/`run_thy`. THY does not consume MIRA context snapshots in this MVP.

For conversation Runs without experiments, EDA summaries use source-linked profiles to describe
dataset structure and missingness, with generated-file links and explicit limits on interpretation.
Structured task JSON and console output remain evidence, not the conversational report. Core
records the response in the Run event chain; its release still requires the ordinary MIRA/Gate
lifecycle. Numeric report facts are independently checked against registered source data.

The planner schema separates exact-file deliverables from metrics-content contracts. Runtime
provenance and recovery fields are omitted from model choices and cannot be invented by a plan.

A proposed plan requiring human review remains a separate pending candidate. THY returns before
execution; Core owns parking, exact approval validation and recovery. Only the approved candidate
can enter the executable plan. Final artifact validation still applies after execution.

## Rules

- Contracts live in `thymira.schemas`; do not redefine them here.
- Everything is English; Google-style docstrings; tests next to the feature in `tests/`.
- Follow `AGENTS.md` (conventions) and the import direction in `runtime/README.md`.
