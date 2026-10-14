# thymira-web

The Thymira web console: a local browser client of the Thymira API. It keeps follow-up messages in
conversation Sessions, while each message starts a Run. The standalone Runs view remains available
for direct access to every Run's overview, interview, plan, agents, reviews, tool calls, artifacts,
MIRA audit, live events, inputs and model usage.

| | |
|---|---|
| Import name | `thymira.web` |
| Owner (MVP roadmap) | P5 (CLI / UX / QA) |
| Priority | P1 |
| Location | `apps/web/` |

## Run it

Start the API, then the console, from the repository root:

```bash
uv run thymira-api --workspace examples/credit-risk
uv run thymira-web --api-url http://127.0.0.1:8000
```

Open <http://127.0.0.1:8080> and paste the API token when asked. The API prints where it wrote the
token at start-up (by default `<workspace>/.thymira/runtime/api-token`). `just web` runs the same
console script.

## How it is built

- `thymira.web.app` is a Starlette application: `/` and `/static/*` serve the console and `/api/*`
  forwards to the API. There is no build step; the views are ES modules under `static/js/`.
- The console holds no Run state and no credential. The browser keeps the token in
  `sessionStorage` for the tab and sends it as `Authorization`; the console forwards that header
  unchanged, drops cookies and every other non-allowlisted header, and never adds one.
- Only the API's route roots are forwarded, one plain path segment at a time. A foreign `Host`
  name and a cross-origin state-changing request are refused, and every response carries a
  same-origin Content-Security-Policy.
- Views render Run data as text nodes, never as HTML. Artifact content comes from the API as a
  digest-verified, redacted projection.
- Request bodies are streamed to the API and bounded: 1 MiB, or 256 MiB for a dataset upload.
- The run list is loaded once, on a filter change and on "Refresh", never on a timer: the API
  verifies every Run's event log to answer it. A Run's header appears from its record while its
  event page, which the API verifies and redacts in full, is still loading.

## Runs

Creating a Run starts it: the API dispatches it as soon as it is recorded. A Run waiting for an
interview answer marks its Interview tab (answer there, Ctrl+Enter), and a Run waiting for a review
marks its Approvals tab. "Run again" starts a new Run with the same prompt.

## Sessions and project explorer

Sessions keep the user's messages and THYMIRA's returned responses together across follow-up Runs.
The current turn must finish before the composer accepts another message. If a send's network
result is uncertain, the console keeps that exact message and request id available for a safe retry.
Human reviews can be answered directly in the session chat. The card explains the recorded risk
and missing context, exact tool arguments, plan or findings as applicable, plus what the answer
covers. Both answers carry the displayed decision ID; stale or unreadable evidence disables them.
Risk questions appear inside the session and record answers against the displayed question. The
start-review card can open the declared context and request a correction; this keeps execution
stopped and reclassifies the same Run after the answer. Run-level Interview remains available.
Open a turn's Run for the remaining controls, including cancel.

The Explorer edits existing bounded UTF-8 project files. Save checks the loaded revision; a
conflict retains the draft. Hidden or credential-like paths are excluded, and redacted or
truncated files remain read-only. Changes affect the shared project, not an already registered
Run input or immutable artifact. The session's Datasets inspector and
"Add / manage datasets" button reuse the project upload, replacement and target controls. Inputs
are shared by the project's sessions, with changes applying when the next Run registers sources.
Missing declared files disable new sends and name the file to replace; the API enforces the same
readiness check. Existing Inputs and Project inputs views remain available for datasets and context.

## Project inputs

A Run reads what the project declares: `.thymira/context.md` and the datasets
`.thymira/config.yaml` lists. The console edits both, beside the prompt on the New run page and
under each Run's Inputs tab, through the API's `/project` routes (`docs/contracts/api-v0.1.md`); it
writes nothing itself.

- **Datasets.** Add a CSV or Parquet file under a name, optionally with its target column. The API
  reads the file with the reader registration uses before declaring it, so every declared file is
  registered as a source, with its `source_path`, when the next Run's Inspect step starts. Replace
  the file, change the target or stop declaring it from the same list; "Use in prompt" inserts the
  name into the prompt.
- **Context.** The editor saves `context.md` only if nobody changed it since it was loaded
  (Ctrl+S). When redaction masked part of it, the editor is read-only: edit the file directly.
- Changes reach the next Run. A Run that already registered a dataset keeps its own copy, and its
  Inputs tab lists the sources it registered.

## Rules

- Imports no runtime member: HTTP to the API only (import-linter contract "Clients own no state").
- Everything is English. The web proxy tests live in `tests/thymira/test_web.py`; session UI
  behavior is tested without added dependencies with `node --test apps/web/tests/*.mjs`.
