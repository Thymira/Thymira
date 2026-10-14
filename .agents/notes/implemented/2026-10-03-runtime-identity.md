## Problem

Runs did not record which process generation created or continued them. A disk hash taken for each
Run would not prove which code was loaded, would reread files on every request, and could include
mutable workspace data. Resuming a serialized Run also needs to preserve its original
`run.started` provenance while recording the runtime that actually continues execution.

## Decision

The API captures one immutable `RuntimeIdentity` after composing its graph factory and passes the
same record to `RunService` and `InlineDispatcher`. The lifecycle worker captures its own identity
after composing its graph factory and passes it to `LifecycleGraphExecutor`. The default service
helper caches an identity by PID; API and worker composition use explicit captures so an earlier
cache without a resolved catalog cannot replace their startup evidence.

`run.started` records the generation id, start time, PID, deterministic fingerprint when all
declared layers are verifiable, bounded layer names/scopes/digests/counts/statuses, and unavailable
reasons. `run_environment.runtime_observation` records the scoped loaded-code, schema, and binding
instance observations at creation. Each inline submit/resume and owner-bound worker execution
appends `runtime.attached` after graph construction and before invocation. This LOG_ONLY event
records the boot identity, execution observation, and the original `run.started` sequence and hash.
Resume never rewrites creation provenance. Custom dispatchers remain opaque and receive no
fabricated per-execution attachment.

The deterministic fingerprint keeps six separate layers: `startup_source_snapshot` covers the
installed Thymira source and selected prompt/default/catalog/spec resources; `loaded_code_objects`
is the loaded-code projection; `resolved_catalog` covers the resolved catalog, specs, prompts, and
effective-response catalog snapshot; `effective_pydantic_schemas` separately covers JSON Schema
projections of explicitly resolved and loaded Pydantic classes; `installed_transformers` covers
package-owned installed files and `METADATA`/`RECORD` for LiteLLM, OpenAI, Pydantic, Pydantic Core,
typing extensions, typing inspection, and annotated types; and `python_runtime` records the Python
implementation/version. `response_format` remains the Pydantic class supplied to the adapter; the
local JSON Schema projection is not HTTP payload evidence and does not prove remote acceptance.
Source inventory is sorted, does not consult Git, and is captured only at startup. An unreadable or
missing required inventory makes that layer unavailable and leaves the overall fingerprint unset.

The loaded-code layer is a bounded projection of direct function bindings in loaded Thymira
modules and explicit Python class methods and property accessors. A descriptor target whose
`__code__` is a Python `CodeType`, including a bound or decorated callable exposing one, is
represented by its code shape. A consumed callable descriptor without inspectable Python code
makes the loaded-code layer unavailable rather than crashing capture or inventing a digest;
nonexistent property setters/deleters are skipped. The projection does not traverse module globals,
defaults, closure state, or general callable-wrapper internals. It is not an attestation of every
executable object in the process or of loaded SDK serializer internals. Module globals and dynamic
settings are outside this digest.

In-process observations compare the in-scope loaded code, binding instances, and Pydantic schema
projections; they do not re-hash disk. A newly captured process gets a new generation even when its
content fingerprint is unchanged. A reload/rebinding observed in-process gets a new execution
generation. The installed transformer layer is a startup file snapshot; it does not attest bytes
already loaded by a lazy serializer. If runtime skill roots are configured but their catalogs are
resolved per Run, the resolved catalog layer is explicitly unavailable instead of being guessed at
startup.

Source capture follows installed `thymira` namespace roots for declared runtime packages and
selected resource directories. It excludes unrelated client packages, secrets, `.aws`, data,
artifacts, Runs, history, caches, virtual environments, and symlinks. Events contain bounded layer
evidence, not file lists or schema bodies. The existing `/runs/{id}/events` route exposes the
hash-chained evidence without a new health endpoint or persistence service. Coding tools remain
non-strict and structured output remains strict.

## Alternatives considered

- Hashing workspace or installed source files for every Run was rejected because current disk
  contents do not establish loaded code, repeated I/O is unnecessary, and workspace scans can
  include mutable user data or secrets.
- Importing LiteLLM or its serializers for inspection was rejected because it can trigger deferred
  imports and still cannot prove the bytes used by an earlier call.
- A deployment-version service, database, or registry was rejected because this evidence is
  process-local and fits the append-only Run event chain.
- Capturing an HTTP body or inferring provider acceptance was rejected because runtime identity
  cannot establish transport serialization or remote behavior.
- Wrapping the graph factory in a new abstraction was rejected because the existing factory exposes
  a narrow immutable resolved-catalog snapshot without changing its callable contract.

## Consequences

The focused final run covered 30 new identity tests and 178 controls, with no duplicate controls:
208 passed and one warning. Its raw log and JUnit report are
`.tmp-runtime-identity-review/root-final-focused-v2.log` and
`.tmp-runtime-identity-review/root-final-focused-v2.xml`. The complete suite collected 4,318 tests:
4,235 passed, 5 failed, 78 skipped, and 61 warnings in 1,655.64 seconds. The comparison with the
prior provider-review full-suite run found the same five failure nodes and no new failures. Raw
outputs remain preserved. After normalizing only Thymira frame nonces (including pytest-truncated
prefixes), prefixed random IDs, and displayed object addresses, all five failure messages and
assertion lines match. The comparison is recorded in
`.tmp-runtime-identity-review/full-failure-comparison.json`; the runs are in
`.tmp-runtime-identity-review/root-final-all-v2.log` and `.tmp-runtime-identity-review/root-final-all-v2.xml`.

The source freeze covered 854 files with SHA-256
`6c4fbb6ada167676f31e3fcfc92bbb1c71d4a2a67032585403f84ecb0b8aed05`, unchanged before and after
the full run. Manifests are `.tmp-runtime-identity-review/source-before-full-v2.json` and
`.tmp-runtime-identity-review/source-after-full-v2.json`. Independent review accepted scope 07 with
the implementer inactive; the workspace-wide gate remains pending because those five full-suite
failures persist.

Source Ruff, formatting across 746 source files, ty with zero diagnostics, and import contracts
(four kept, zero broken) passed. Agent notes validated; source YAML and package invariants passed;
16 skills were valid with none invalid; the roadmap agreed; and the offline lock contained 251
packages. The workspace-wide checks still report pre-existing evidence/fixture findings:
`just check` stopped at Ruff with 156 findings confined to `.tmp-eda-followup` (17),
`.tmp-provider-review` (9), `.tmp-publication-implementation` (60), and `.tmp-retries-review` (70),
while a separate whole-workspace format check reports 42 findings. YAML findings across prior
fixtures and evidence include newline, comma, line-length, final-newline, syntax, empty-line,
indentation, and trailing-space issues; they are not limited to CRLF.

The API events probe verified that GET of the event route returned HTTP 200 and exposed the
creation and execution identities, their original-start link, and the event hash chain in
`.tmp-runtime-identity-review/api-events-probe-v2.log`. An offline installed capture verified 415
source files and 3,820 transformer files under a deliberately empty catalog, which remained
explicitly diagnostic, in `.tmp-runtime-identity-review/installed-layers-v2.log`.
Native static/class/property descriptors without code remain unavailable without breaking capture;
the inspectable `MethodType` positive control and real worker/factory fixtures pass.

The final size review records `runtime_identity.py` at 687 lines and 27 definitions and the private
file-inventory helper at 293 lines and 10 definitions. The identity module remains cohesive around
identity records and in-memory code/schema observation while file I/O is isolated. `RunService`
retains 25 methods with no new behavior method.

The implementer ran as `gpt-6-luna` with `xhigh` reasoning. Selectors for the principal runtime and
Fast were not exposed or verifiable, so no claim is made that a particular selector was applied to
either. This limitation is separate from the runtime identity evidence.
