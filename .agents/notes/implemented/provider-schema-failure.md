# Provider-compatible Python schema and terminal call failures

## Problem

Both Coding delegations in Session session_3632f01a96e14b4497caabb062130e6a made 40 requests and received only BadRequestError outcomes. Neither invoked a tool. The new run_python schema introduced root oneOf/not composition. A gateway-boundary regression reproduces rejection of this schema. Separately, routed_model converted provider call exceptions into ModelRetry, consuming the entire agent retry limit and obscuring the cause behind max_turns and absent PNG outputs. The original provider error body was not retained, so the exact remote rejection parameter cannot be recovered from historical events.

## Decision

Expose code and source_artifact_id in a plain object schema; keep the exactly-one-source validator and all runtime provenance/approval checks. Propagate LLMCallError out of tool and structured request loops. AgentRunner records a failed provider_error outcome with a code-owned diagnostic, and Delegator carries it into the task error. Received structured outputs that fail local validation retain their bounded repair path and usage accounting. Extract terminal reason vocabulary and classification from the oversized runner into failure_outcome, retaining existing imports through runner.

## Alternatives considered

Increasing max_turns would repeat the same provider rejection. Removing registered-source execution would restore duplicate code generation. Stripping arbitrary schema keywords globally would silently weaken unrelated tools; only this tool's emitted composition is removed. Recording raw exception bodies could expose provider input or credentials, so the terminal diagnosis remains bounded code-owned text. A general retry scheduler needs a named consumer and is not introduced.

## Consequences

Provider failures stop this agent attempt; transient failures also require a later request rather than automatic agent-loop retries. Existing output validation retries and approval/resume controls remain intact. Tool-local validation continues rejecting mixed or absent Python source. Tests exercise the real LiteLLM adapter and Coding catalog with a fake gateway, never an external model. This proves the local contract and failure handling, not a completed live-model visualization. Historical Runs remain unchanged.
