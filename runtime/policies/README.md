# thymira-policies

Policy Engine: deterministic rules that turn audit findings (severity, confidence, evidence, framework) into PASS / WARNING / REQUIRE_HUMAN_REVIEW / BLOCK. The LLM never decides enforcement.

| | |
|---|---|
| Import name | `thymira.policies` |
| Owner (MVP roadmap) | P4 (MIRA / Governance) |
| MVP priority | P0 |
| Location | `runtime/policies/` |

## Status

Implemented and tested (`tests/thymira/test_policies.py`, `test_examples.py`): rules as data, `PolicyEngine` with fail-safe escalation, `Gate`, loaders and the `base` / `credit_risk` defaults.

Capability decisions preserve inherited execution constraints. A run-wide human-review flag
escalates an otherwise passing or warning capability to `REQUIRE_HUMAN_REVIEW`; it never lowers
BLOCK. The Gate records this decision before asking about the exact tool effect. Its
execution-start summary explains that each tool will need separate human approval, so an
approved start does not imply approval for subsequent calls.

### Indeterminate MIRA findings

Core may pass an optional `FindingReviewContext` to `PolicyEngine.decide_findings` and
`Gate.review_findings`; only the registered local `profile_dataset` capability is eligible, and
`run_python` or any other capability is not. `core.finding_review` validates the complete MIRA
snapshot, candidate, profile, artifact manifest, typed Run work kind and event scope; `graph.compose`
passes that exact context and `governance_binding` plus `RunService.resolve_approval` revalidate it
after resolution and reopening. Only a critical, agent-authored candidate with `supported=None`
can receive the special `GOV-101-INDETERMINATE` review; confirmed findings and custom blocking
rules remain blocked, and missing or stale context keeps the ordinary fail-safe decision.

The persisted request includes the claim, evidence and exact context digest; its human answer is
bound to that audit, Run, profile and `profile_dataset` action and authorizes no other tool or model
operation. Core rechecks the binding after the callback and reopening, while an incomplete report or
required control with `NOT_EVALUATED` blocks closure even when no findings remain.

## Rules

- Contracts live in `thymira.schemas`; do not redefine them here.
- Everything is English; Google-style docstrings; tests next to the feature in `tests/`.
- Follow `AGENTS.md` (conventions) and the import direction in `runtime/README.md`.
