# Blocked conversation diagnostics and execution preflight

## Problem

Run `run_644884962018435a9caf50dd133960ee` ended BLOCKED under GOV-101 because A19 found
unusable confinement: Docker was absent. Session chat exposed the terminal label without
the causal tool failure. Human approval was requested for Python that could not run. The
generated source also contained literal backslash-newline escapes between statements, and
the EDA plan included an unsolicited model-training task.

## Decision

Project a bounded, credential-scrubbed terminal diagnostic from existing verified events.
Require an exact unique closing decision and, for findings, its bound report and evidence.
Keep it separate from released assistant text and conversation prompt history. The new API
field is additive, works for old Runs, and does not rewrite their records.

Extract existing argument validation from ToolManager into `tool_preflight.py`. Validate
Python syntax under source size/line limits without evaluating it or modifying its bytes.
Before Gate review, probe the registered container backend's Docker daemon and image with
bounded read-only commands. Expose the same prerequisite status to the chat without polling
it on every conversation refresh or disabling unrelated tools.

Move plan scope normalization into `plan_scope.py`. The current conversation message defines
explicit EDA/model prohibitions; historical training text cannot add Experiment/ML work to
that new request. Preserve positive model requests, ambiguous continuations, seeded plans,
catalog constraints and the Gate. Generic descriptive metrics/evaluation/search are not model
training requests. This is a specialist-routing guard, not arbitrary Python semantic analysis.

## Alternatives considered

- Automatically approving calls, relaxing A19 or switching to unconfined local execution would
  hide the unavailable sandbox and change the authorization model.
- Globally unescaping model-generated Python would alter valid strings and approved intents.
  Reject malformed source with bounded repair feedback instead.
- Persisting another transcript/error record would duplicate the authoritative Run log.
- Removing model tasks for every keyword-free follow-up would break continuing training work.
  Restrict removal to recognized explicit analysis-only requests and prohibitions.

## Consequences

Missing prerequisites become recorded pre-execution failures, with no tool launch or human
approval request. Read-only Docker probes are bounded but add latency to container calls.
Execution-time safeguards remain necessary because readiness can change after a probe.
Python validation uses the host parser; it is neither a security authorization nor proof of
successful sandbox execution. The original Run remains blocked and unchanged. Docker/WSL
installation is left to the user; no full live-model/container EDA is claimed.

ToolManager's flagged class stays at 918 lines while its module drops from 1,342 to 1,298.
ConversationWorkspace drops from 211 to 202 lines. New helpers are unflagged. Independent
Sol review identified missing daemon/image preflight, non-findings binding and a Spanish
tuning-negation case; all were fixed and re-reviewed. See the verification record for checks.
