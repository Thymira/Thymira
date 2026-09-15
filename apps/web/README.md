# thymira-web

The Thymira web console: a local browser client of the Thymira API, built as a thread-first IDE
shell. A sidebar lists every Run in the project as a thread; opening one shows its transcript
folded from the event log, one state-driven composer, and an inspector holding the evidence panels
(overview, plan, agents, reviews, tools, artifacts, audit, events, usage, inputs, interview).

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
  forwards to the API. There is no build step; the views are ES modules under `static/js/`, styled
  by a token-based design system split across `static/css/*.css` (tokens, base, layout,
  components, thread, panels, motion).
- The console holds no Run state and no credential. The browser keeps the token in
  `sessionStorage` for the tab and sends it as `Authorization`; the console forwards that header
  unchanged, drops cookies and every other non-allowlisted header, and never adds one.
- Only the API's route roots are forwarded, one plain path segment at a time. A foreign `Host`
  name and a cross-origin state-changing request are refused, and every response carries a
  same-origin `default-src 'none'` Content-Security-Policy that allows only same-origin scripts and
  styles.
- Views render Run data as text nodes and attributes built with the `h()` helper, never as HTML:
  no `innerHTML`, `insertAdjacentHTML`, `outerHTML` or `DOMParser` on API data. Assistant text goes
  through a safe Markdown parser (`markdown.js`) that emits its own DOM nodes rather than parsing
  HTML. Artifact content comes from the API as a digest-verified, redacted projection.
- Request bodies are streamed to the API and bounded: 1 MiB, or 256 MiB for a dataset upload.
- The run list is loaded once, on a filter change and on "Refresh", never on a timer: the API
  verifies every Run's event log to answer it. A thread's header appears from its Run record while
  its event page, which the API verifies and redacts in full, is still loading.

## Shell

- **Sidebar** (`views/sidebar.js`) — the project card (name, domain, dataset count, up to two
  frameworks) links to the project page; below it, every Run as a thread, grouped by the day it
  started (Today/Yesterday/This week/Older), with filter pills (All · Needs you · Running · Done)
  and instant client-side search. A thread waiting on a review carries a "Review" pill; an active
  thread's dot pulses. The footer holds Refresh, Connect/Disconnect, Settings and the theme cycle.
  `Ctrl+B` collapses the sidebar to a 56px icon rail; below 900px it becomes an overlay drawer
  behind the menu button and the scrim.
- **Home** (`views/home.js`) — the page that starts a thread: one composer (`mode: "new"`), three
  cards explaining how a thread runs (Inspect → Plan → Execute → Report; questions and reviews wait
  for you; MIRA audits before it completes), a link to manage project inputs, and the most recent
  threads. An unsent prompt survives navigation in `sessionStorage`, never in the Run record.
- **Thread** (`views/thread.js`, `transcript.js`, `composer.js`) — a sticky header (prompt, status,
  stage stepper, Resume/Cancel/Run again) above the transcript, which folds the Run's hash-chained
  event log (`thread-fold.js`) into prompt, stage dividers, interview questions and answers, the
  risk profile, the plan, agent groups (with their tool calls, reviews and messages nested inside),
  the MIRA audit group, and the outcome. One composer follows the Run's state — new, answering a
  question, reviewing a tool call, paused at a checkpoint, working, or ended — so the operator
  always types in the same place.
- **Inspector** (`views/inspector.js`) — the evidence beside the thread, opened by a transcript
  reference, a keyboard shortcut (`Ctrl+I`) or a tab click: the same panel renderers the console has
  always had (`views/tabs/*.js`), unchanged, reading the same `ctx`. It is resizable by drag or
  arrow keys, and a panel the operator is typing into shows a "New events" refresh button instead of
  redrawing under their hands.
- **Project page** (`views/project.js`) — what `.thymira/config.yaml` declares (name, domain,
  frameworks, whether an audit is required) plus the datasets and context editor a Run reads
  (`views/inputs.js`, ported unchanged in behaviour).
- **Settings** (`views/settings.js`) — appearance (theme: system/light/dark), the connection line
  (token present or not, Connect/Disconnect), and any per-tab preferences.
- **Command palette** (`Ctrl+K`) — fuzzy-matched actions and threads; `?` opens a shortcuts sheet.
  The console never renames wire vocabulary (`RunStatus`, `Decision`, event types) in these
  surfaces — only shell copy says "thread" and "project".

## Project inputs

A Run reads what the project declares: `.thymira/context.md` and the datasets
`.thymira/config.yaml` lists. The console edits both, on the project page and under each thread's
Inputs panel, through the API's `/project` routes (`docs/contracts/api-v0.1.md`); it writes nothing
itself.

- **Datasets.** Add a CSV or Parquet file under a name, optionally with its target column. The API
  reads the file with the reader registration uses before declaring it, so every declared file is
  registered as a source, with its `source_path`, when the next Run's Inspect step starts. Replace
  the file, change the target or stop declaring it from the same list; "Use in prompt" inserts the
  name into the prompt.
- **Context.** The editor saves `context.md` only if nobody changed it since it was loaded
  (Ctrl+S). When redaction masked part of it, the editor is read-only: edit the file directly.
- Changes reach the next Run. A Run that already registered a dataset keeps its own copy, and its
  Inputs panel lists the sources it registered.

## Rules

- Imports no runtime member: HTTP to the API only (import-linter contract "Clients own no state").
- Everything is English; tests live in `tests/thymira/test_web.py`, `test_web_static.py` and
  `test_web_js.py` (the last runs the pure modules' `node --test` suite under
  `tests/thymira/web_js/`, skipped when Node is not installed).
