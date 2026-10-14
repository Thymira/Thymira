# Coding provider-schema failure verification

## Historical evidence

Session: session_3632f01a96e14b4497caabb062130e6a.

- First EDA: run_f5953c8385d94d00b793cdb34be43a3b, 890 verified events. Dataset profiling succeeded; Coding failed before any Coding tool invocation. The Run ultimately completed with the report explicitly identifying failed Coding work. This was a partial analytical result, not evidence of Python execution.
- Visualization follow-up: run_c2b5ef5e2d4048b39afc7a24e09b7d13, 406 verified events. Coding was delegated at sequence 156, ended at 398, and the Run failed at 405. Missing visualization_heatmap.png and visualization_graphs.png were downstream consequences: the agent never reached write_file or run_python.
- Each Coding delegation recorded 40 provider_error responses with zero recorded response tokens. Their error-type digest matches litellm.exceptions.BadRequestError. This is not a measured dollar-cost estimate. Request digests differ as retry context changes; the unsupported Python tool schema remains present.
- Both artifact manifests contain four registered artifacts. Their bytes, sizes and SHA-256 digests verified through direct reads. No historical store was instantiated, script executed, checkpoint deserialized or event rewritten.

## Cause and correction

The recent registered-source change added root oneOf with not clauses to run_python's function parameters. The provider only accepts a subset of JSON Schema; see the [official schema guide](https://developers.openai.com/api/docs/guides/structured-outputs). Historical request records contain that schema, while the provider response body was deliberately not retained. The exact remote rejection field is therefore unavailable; attributing these historical BadRequestError responses specifically to this composition is an inference supported by the changed schema and offline reproduction, not a replay of the remote error.

The tool now emits a plain object schema. Its Pydantic validator still enforces exactly one source before policy review, and verified source identity remains bound to approval and execution. No global schema sanitizer or permission relaxation was added.

The independently reproduced retry defect is definite: model_binding converted LLMCallError into ModelRetry, making a rejected request look repairable by the model. The adapter now propagates call failures and AgentRunner records provider_error with a safe diagnostic. Delegation carries that reason into task failure. Increasing the turn limit is unnecessary. Malformed responses actually returned by the model still follow the existing bounded validation-repair path.

## Validation

Before correction, two new regressions failed: the actual Coding tool schema was rejected by the offline gateway boundary, and one BadRequestError became 40 recorded requests. After the schema-only correction, the schema regression and 11 registered-source boundary cases passed. The completed change passed 209 tests across provider parsing, model binding, agent execution, delegation, resume/approval, exact Python sources and THY execution. That combined selection includes seven new cases: schema acceptance and BadRequestError, authentication and timeout closure for tool-capable and structured-only agents. Earlier overlapping runs are not added to this count.

Ruff and repository-wide ty pass. Import contracts: 4 kept, 0 broken (407 modules, 1,631 dependencies). Package invariants pass. Runner size decreases from 1,183 to 1,150 lines and its class from 517 to 504; model_binding remains 670. The new failure classification module is below detector thresholds. One Pydantic Graph event-loop deprecation warning was emitted.

## Limits and operation

The gateway is faked in tests; no external model request, paid API trial or Docker visualization was performed. The original error bodies are unavailable. This is a focused regression selection, not the complete just check gate. Restart the API to load the changes before testing a new conversational request. Existing terminal Runs and reports are preserved.
