# Thymira Console: thread-first IDE redesign

**Status:** approved design (autonomous session, 2026-09-15). **Owner:** P5 (CLI / UX / QA).
**Member:** `apps/web` (`thymira.web`), plus one read-only route in `apps/api`.

## 1. Goal

Turn the console from a records-and-tabs viewer into a **thread-first workspace** in the family
of Claude Code, Codex and Cursor: a sidebar of threads under the project, a conversation
transcript folded from the Run's event log, one composer whose function follows the Run's state,
and an inspector that holds the evidence. It must look and feel like a modern IDE: a coherent
dark/light design system, purposeful animation, keyboard-first navigation, and zero dead ends.

The console's contract does not change: it is a replaceable client that holds no Run state, adds
no authority, renders Run data as text nodes and talks only to the API through the same-origin
pass-through under a `default-src 'none'` Content-Security-Policy.

### Non-goals

- No framework, bundler, build step or external asset (fonts, icons, scripts). The CSP stays.
- No client-side Run state: every screen is a fold over API responses.
- No multi-project switcher: one API process serves one project (`_configured_project`). The
  "project" in this design is that workspace's identity, knowledge (`context.md`) and datasets.
- No conversation continuation inside a Run: a Run is one prompt. "Follow-ups" start a new Run.

## 2. Vocabulary shown to the operator

| Wire concept | Shown as | Why |
|---|---|---|
| Run | **Thread** | one prompt, its work and its evidence; the sidebar item |
| Workspace served by the API | **Project** | its knowledge (`context.md`) and datasets |
| THY | **THY** (weaver glyph, logo blue) | the intelligence that acts |
| MIRA | **MIRA** (thread glyph, logo orange) | the intelligence that watches |
| `human.approval_requested` | **Review** | "an approval authorizes exactly one call, once" stays in the card |
| risk interview question | **Question** | answered from the composer |

The wire vocabulary (`RunStatus`, `Decision`, event types) is never renamed in code or in the
inspector; only the shell speaks "thread" and "project".

## 3. Information architecture and routes

Hash routing stays (no server routes change).

| Route | View |
|---|---|
| `#/` | Home: composer-first "new thread" page |
| `#/new` | alias of `#/` (old links keep working) |
| `#/runs/<run_id>` | Thread view, inspector closed or on its last panel |
| `#/runs/<run_id>/<panel>[/<item>]` | Thread view with the inspector open on `panel`; `item` selects an entry (an artifact id) |
| `#/project` | Project page: knowledge and datasets |
| `#/settings` | Settings: model routing, appearance, connection |

Panel ids: `details` (the old `overview`, which is accepted as an alias), `plan`, `agents`,
`reviews` (the old `approvals`, alias accepted), `tools`, `artifacts`, `audit`, `events`,
`usage`, `inputs`, `interview`.

## 4. App shell

```
┌────────────┬──────────────────────────────────────────┬──────────────┐
│ sidebar    │ thread header (sticky)                    │ inspector    │
│ 280px      │──────────────────────────────────────────│ 420px        │
│ project    │ transcript (scrolls, 840px column)        │ (resizable   │
│ new thread │                                           │  320–60vw,   │
│ search     │                                           │  collapsible)│
│ threads    │                                           │ panel tabs   │
│ grouped    │                                           │ panel body   │
│            │──────────────────────────────────────────│              │
│ footer     │ composer (sticky bottom, state-driven)    │              │
└────────────┴──────────────────────────────────────────┴──────────────┘
```

- `≥ 1280px`: three columns. `900–1279px`: the inspector overlays the transcript as a right
  sheet with a scrim. `< 900px`: the sidebar becomes a left drawer behind a menu button, the
  inspector a full-height sheet, the composer full width.
- Sidebar and inspector widths, collapsed state, theme and last inspector panel persist in
  `localStorage` under `thymira.console.prefs` (a small `prefs.js` wrapper; every access in
  try/catch). The token stays in `sessionStorage`, unchanged.
- A thin banner under the header appears when the API is offline ("The API is not reachable ·
  retrying") and slides away when it returns.

## 5. Sidebar (`views/sidebar.js`, replaces `rail.js`)

1. **Brand row**: wordmark (glyph + THYMIRA) and a collapse button (`Ctrl+B`). Collapsed, the
   sidebar is a 56px icon rail: new thread, threads, project, settings; titles on hover.
2. **Project block** (links to `#/project`): project name from `GET /project` (fallback
   "Workspace" until it answers, or on 404 from an older API), domain chip, framework chips,
   dataset count. A status dot (online/offline) sits in the row.
3. **New thread** button (primary, plus icon).
4. **Search** field: client-side filter over the loaded threads (title, id).
5. **Filter pills**: All · Needs you · Running · Done. "Needs you" = `WAITING_FOR_APPROVAL`;
   "Running" = `CREATED PLANNING RUNNING EXPERIMENTING AUDITING`; "Done" = terminal statuses.
   Filtering is client-side over the full list already loaded (`listAllRuns`); the API's `status`
   query stays available in `api.js` but the sidebar no longer refetches on filter change.
6. **Thread list** grouped by `created_at`: Today, Yesterday, This week, Older. Item: status
   dot (pulsing when the status is active), two-line title (`headline(prompt)`), meta line
   (relative time · decision label or status label), attention pill "Review" when
   `WAITING_FOR_APPROVAL`. Selected item has an accent bar. Right-click or `…` is out of scope.
7. **Footer**: refresh (icon, spins while loading, "updated 12:03" tooltip), Connect/Disconnect,
   Settings, theme toggle (system → light → dark).

The list is loaded once, on "Refresh" and after a thread is created or changes status
(`upsert`), never on a timer (the API verifies every Run's log to list it; unchanged rule).

## 6. Home (`views/home.js`, absorbs `new-run.js`)

Centered column (max 760px, vertically centered on tall viewports):

- Eyebrow: project name · "New thread". Heading: "What should THY work on?".
- **Composer** (shared `composer.js` component, `mode: "new"`): auto-growing textarea (3–14
  rows), rounded 16px surface, focus glow, inner toolbar: left = dataset chips ("`german_credit`"
  inserts the name at the cursor; a "Manage inputs" link opens `#/project`), right = hint
  "Enter to send · Shift+Enter for a new line" and the send button (arrow icon; disabled when
  empty; spinner while creating). `Ctrl+Enter` also sends.
- Under the composer, three quiet "how a thread runs" cards: Inspect → Plan → Execute → Report ·
  Questions and reviews stop the thread and wait for you · MIRA audits before it completes.
- "Recent" list of the last five threads (title, status, time) for warmth; hidden when empty.
- A draft survives navigation within the tab (`sessionStorage` key `thymira.console.draft`).

## 7. Thread view (`views/thread.js`, replaces `run.js`)

Owns the Run record, the event log, the SSE follow, the catch-up and the 400 ms update batching
exactly as `run.js` does today (that logic moves, it is not rewritten). It renders three parts.

### 7.1 Header (sticky)

Title (`headline(prompt, 120)`, full prompt on hover title), status pill (`statusTag`), compact
stage stepper (Plan · Execute · Audit · Report; the current step's dot pulses, done steps fill
with an animated line), copyable short id, and actions: **Resume** (only when
`isResumableState(state)`; the test invariant stays: `const resumable = isResumableState(state);`
and `resumable ? button("Resume"`), **Cancel** (active runs; confirm dialog, not
`window.confirm`), **Run again** (terminal runs), **Inspector** toggle (`Ctrl+I`, shows the
attention badge when a review or question is pending). A `run.error` renders as a block notice
under the header.

### 7.2 Transcript (`transcript.js` + `thread-fold.js`)

`foldThread(run, events) → ThreadItem[]` is a pure function in `thread-fold.js` (unit-testable
with node). Item kinds, in log order:

| kind | source | rendering |
|---|---|---|
| `prompt` | `run.prompt`, `run.created_at` | user turn: "You" label, time, prompt text in a soft card |
| `question` | `activity_profile.questioned` | assistant turn from MIRA: "Question n · field", question text |
| `answer` | `activity_profile.answered` | `answer_source == live_human`: user turn; `project_context`: quiet note "answered from context.md (field)" |
| `risk` | `risk.classified` (+ the `risk-classifier` `agent.message`) | MIRA card: level tag, category, confidence, "human review required" |
| `plan` | first `run.transitioned` with `stage == executing`, or any `plan.*` event | THY card that lazily loads `api.plan()` (items with state chips); refreshes on `plan.*` events |
| `agent` | `agent.started … agent.completed/parked` | collapsible group: avatar, name, objective, status tag; contains the items below in order |
| `message` | `model.response_chunk` with non-empty `message.content` and `role == assistant`, grouped by `request_id` and concatenated in `source_sequence` order | assistant turn attributed to the innermost open agent, else THY. If the content parses as JSON it renders as a collapsed "structured output" code block, else as safe markdown |
| `tool` | `tool.*` folded by `foldTools` (one row per intent digest) | one-line row: tool icon, tool name, first argument summary, status chip, artifact count; expands to arguments, attempts and result/error |
| `review` | `foldApprovals` request | review card: summary, reason, rule, tool + arguments; when answerable: note + Reject/Approve; when resolved: "Approved by human/operator · note" |
| `agent_summary` | `agent.message` with no `form` | assistant turn "data completed: …" (markdown), inside the agent group |
| `audit` | `audit.started … audit.completed` (+ `audit.finding`) | MIRA group: findings list with severity tags, status; link "Open audit" |
| `stage` | `run.transitioned` where `stage` changes | hairline divider with the stage label ("Executing") |
| `outcome` | `run.completed` / `run.failed` / terminal transition | card with the final decision tag, error, and "Run again" |

Hidden from the transcript (still in the inspector's Events panel): `policy.decision`,
`model.selected`, `model.request_recorded`, `model.input_*`, `model.route_*`,
`activity_profile.recorded`, `agent.message` with `form` (`runtime_context`,
`resume_history_compacted`), `artifact.created` (surfaced through the tool row's artifact count
and the Artifacts panel), `artifact.invalidated`, `subagent.settled`, `context.compacted`,
`turn.ended`.

Rendering rules:

- Items carry a stable `key` (event seq or digest) and a `version` (last seq that changed them).
  The transcript keeps `Map<key, element>`; on update it re-folds, replaces only changed items,
  appends new ones with the `rise` animation (staggered 30 ms, at most 8), and removes nothing.
- Expanded/collapsed state is kept per key across updates.
- Auto-scroll: if the viewport was within 80 px of the bottom, stay pinned; otherwise show a
  floating "New activity ↓" pill that scrolls down when clicked.
- While the Run is active, a "working" row closes the transcript: three animated dots and
  "THY is working · Executing" (or "MIRA is auditing" in the auditing stage).
- Text from the API is always a text node or a safe-markdown DOM tree (see §11). Never HTML.

### 7.3 Composer (`composer.js`, `mode: "thread"`)

One component, one surface, one state machine driven by `run.status`, `latestRunState(events)`,
`foldApprovals(events).focus` and `interviewStatus()`:

| state | condition | surface |
|---|---|---|
| `loading` | run or events not loaded | skeleton |
| `answer` | state waiting for `information` and a `pending_question` | question text above the field ("Question n · field"), placeholder "Answer in your own words; it is recorded as evidence", send → `answerRiskInterview` |
| `review` | `WAITING_FOR_APPROVAL` and a focus request | review bar: tool name + argument summary, "Show the call" (scrolls to the card), optional note field, **Reject** / **Approve this call** |
| `paused` | `isResumableState(state)` | "Paused at a durable checkpoint" + **Resume** |
| `working` | active, none of the above | disabled field with a shimmering placeholder "THY is working…"; Cancel lives in the header |
| `ended` | terminal status | enabled field, placeholder "Describe a follow-up — it starts a new thread with the current project inputs"; send → `createRun`; secondary **Run again** |

Keys: `Enter` sends, `Shift+Enter` inserts a newline, `Ctrl+Enter` sends, `Esc` blurs. Every
action shows a toast on success and an inline error on failure, and re-enables the surface.

## 8. Inspector (`views/inspector.js`)

Right panel with a tab strip (icon + label, horizontally scrollable) and a body. Panels reuse the
existing renderers in `views/tabs/*.js` with the same `ctx` contract (`api, run, events, runId,
item, onRunMutated, interviewStatus, runAgain`), so no evidence view is lost:

| panel | renderer | badge |
|---|---|---|
| Details | `renderOverview` (callouts removed: the composer and transcript carry them) | — |
| Plan | `renderPlan` | — |
| Agents | `renderAgents` | agent count |
| Reviews | `renderApprovals` | pending count, attention |
| Tools | `renderTools` | call count |
| Artifacts | `renderArtifacts` (list above preview in the narrow panel; the panel may be widened to 60vw) | artifact count |
| Audit | `renderAudit` | findings count |
| Events | `renderEvents` | event count |
| Usage | `renderUsage` | — |
| Inputs | `renderInputs` | — |
| Interview | `renderInterview` | attention when a question is pending |

Live panels redraw on the 400 ms batch as today (`update(ctx)` when the renderer returns one).
The "editing → stale button" guard from `run.js` stays for panels with a form.

## 9. Project page (`views/project.js`)

Header: "Project · <name>", domain and framework chips, `audit_required`. Two sections rendered
by the existing `renderProjectInputs` (restyled): **Knowledge** (the `context.md` editor with its
compare-and-set save, read-only when redacted) and **Datasets** (declared datasets, upload with
progress, target, remove). A "New thread" button in the header.

## 10. Settings, connect, palette, toasts, shortcuts

- **Settings** (`views/settings.js`): sections *Model routing* (existing form, restyled),
  *Appearance* (theme: System / Light / Dark; motion follows the OS), *Connection* (API state,
  Connect / Disconnect).
- **Connect dialog**: same `<dialog>` semantics and copy, restyled (centered card, 16px radius,
  backdrop blur); opened only on 401 or from Connect, unchanged.
- **Command palette** (`palette.js`, `Ctrl+K`): commands (New thread, Go to project, Settings,
  Toggle theme, Toggle sidebar, Toggle inspector, Refresh threads) followed by threads matched by
  title/id; arrow keys, `Enter`, `Esc`; simple subsequence match with highlighted characters.
- **Toasts** (`toast.js`): bottom-center stack, 3.5 s, tones `ok | block | neutral`, dismissible,
  `aria-live="polite"`.
- **Shortcuts** (`shortcuts.js`): `Ctrl+K` palette, `Ctrl+B` sidebar, `Ctrl+I` inspector, `?`
  opens a shortcuts sheet, `Esc` closes overlays. None fire while typing in a field except
  `Ctrl+K`/`Esc`.

## 11. Safe markdown (`markdown.js`)

`renderMarkdown(text) → DocumentFragment`, built only with `h()`/text nodes (no HTML parsing):
fenced code (with a language label and a copy button), `#`/`##`/`###` headings, unordered and
ordered lists, blockquotes, paragraphs; inline `code`, `**bold**`, `*italic*`, and `[text](url)`
links only for `http(s)` URLs (`rel="noopener noreferrer"`, `target="_blank"`); any other syntax
is plain text. Angle brackets are text. Nothing else is interpreted.

## 12. Design system (`css/tokens.css`)

Dark-first, warm neutrals, one orange action accent and the two brand colours of the product
logo (THY blue, MIRA orange, as the favicon draws them). `--accent` drives buttons, focus and
selection; `--thy`/`--mira` mark only what belongs to one orchestrator (avatars, the wordmark,
the stepper's current step, orchestrator panels, the settings swatches). Tokens on `:root` (light), overridden under
`:root[data-theme="dark"]` and, for `data-theme="system"`, under `prefers-color-scheme: dark`.

| token | light | dark |
|---|---|---|
| `--bg` | `#f6f5f2` | `#141311` |
| `--surface` | `#ffffff` | `#1b1a17` |
| `--surface-2` | `#f0eeea` | `#23221e` |
| `--surface-3` | `#e7e4de` | `#2b2a25` |
| `--border` | `#e4e1da` | `#2e2c27` |
| `--border-strong` | `#cdc8be` | `#403d36` |
| `--text` | `#1b1a17` | `#eceae4` |
| `--text-2` | `#5b5850` | `#b1ada3` |
| `--text-3` | `#8b877c` | `#7d7970` |
| `--accent` (actions, focus, selection) | `#d9531e` | `#ff7a45` |
| `--thy` (the product logo's blue) | `#1b2e63` | `#7c97f2` |
| `--mira` (the product logo's orange) | `#c2410c` | `#ff7438` |
| `--ok` | `#1f8a4c` | `#4fcf7a` |
| `--caution` | `#4f9d5f` | `#8fd6a0` |
| `--warn` | `#b3701a` | `#e3a23b` |
| `--review` | `#2f6fde` | `#7aa9ff` |
| `--active` | `#0e8a94` | `#3cc4cf` |
| `--block` | `#cf3527` | `#ff6d61` |

- Type: `--sans: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` (a local
  Inter is used only if installed; nothing is fetched); `--mono: ui-monospace, "Cascadia Code",
  "JetBrains Mono", Consolas, monospace`. Base 14px/1.5; transcript prose 15px/1.6; UI labels
  12–13px; eyebrows 11px mono uppercase tracked.
- Radius: 6px chips, 10px cards and rows, 14px composer and dialogs, 999px pills.
- Elevation: light mode shadows (`0 1px 2px rgb(0 0 0 / .05)`, popovers
  `0 12px 32px rgb(0 0 0 / .12)`); dark mode uses lighter surfaces and 1px borders instead.
- Focus: 2px `--accent` ring with 2px offset on `:focus-visible` only.
- Icons: `icons.js` builds inline SVGs (24px grid, 1.75 stroke, Lucide-like) with
  `createElementNS`; ~28 names (plus, search, settings, sun, moon, monitor, sidebar, panel,
  check, x, play, pause, square, alert, info, file, image, table, tool, agent, shield, thread,
  copy, chevron-down, chevron-right, refresh, external, terminal, database, clock, send,
  arrow-down, command).

## 13. Motion (`css/motion.css`)

- Durations: 120 ms micro (hover, press), 180 ms standard (view enter, rows), 260 ms panels
  (sidebar/inspector width, sheets). Easing: enters `cubic-bezier(.2,.8,.2,1)`, moves
  `cubic-bezier(.4,0,.2,1)`.
- Keyframes: `rise` (opacity 0→1, translateY 6px→0), `fade`, `pulse` (active status dots and
  the current stepper dot), `shimmer` (skeletons, working placeholder), `spin` (refresh), `pop`
  (attention badges), `dots` (the working row).
- View switch: `main` content enters with `rise`; transcript items stagger; sidebar and inspector
  animate width; sheets slide; toasts rise; the offline banner slides.
- Buttons: 120 ms background/border transitions and a 0.98 press scale.
- `@media (prefers-reduced-motion: reduce)`: every animation and transition duration becomes
  0 ms; pulses stop; content still appears.

## 14. API addition: `GET /project`

`apps/api` gains one read-only route in `routes/project.py` (read scope) returning
`ProjectSummary`:

```json
{ "project_id": "project_…", "name": "credit-risk", "domain": "credit_risk",
  "frameworks": ["EU_AI_ACT", "CREDIT_RISK"], "audit_required": true,
  "datasets": ["german_credit"] }
```

It reads the same `config.yaml` `list_datasets` already loads and refuses an unconfigured API
with the existing 503. Documented in `docs/contracts/api-v0.1.md` under "Project inputs"; tested
next to the other project routes. The console's `/api` pass-through already forwards the
`project` root; `api.project()` treats 404 as "older API" and the sidebar falls back to
"Workspace".

## 15. File layout

```
apps/web/src/thymira/web/static/
  index.html                 rewritten shell (sidebar, main, inspector, palette, toasts, dialog)
  favicon.svg                kept
  css/tokens.css             §12
  css/base.css               reset, typography, tones, focus, selection
  css/layout.css             shell grid, sidebar, inspector, sheets, responsive
  css/components.css         buttons, inputs, chips, tags, cards, tables, kv, skeletons, dialog, palette, toasts
  css/thread.css             header, stepper, transcript items, composer, working row
  css/panels.css             inspector panel styles carried over from console.css (panels, findings, artifacts, usage, events)
  css/motion.css             §13
  js/main.js                 bootstrap: prefs/theme, shell, router, health, unauthorized flow
  js/router.js               parse, route table, navigate, aliases
  js/prefs.js                localStorage wrapper
  js/theme.js                theme cycle and data-theme
  js/icons.js                inline SVG icons
  js/toast.js, js/palette.js, js/shortcuts.js, js/markdown.js
  js/thread-fold.js          foldThread (pure)
  js/api.js (+project), js/dom.js, js/fold.js, js/format.js, js/status.js, js/ui.js (+skeleton, iconButton, avatar, confirm)
  js/views/sidebar.js        §5   (replaces rail.js)
  js/views/home.js           §6   (absorbs new-run.js)
  js/views/thread.js         §7   (replaces run.js)
  js/views/transcript.js     §7.2
  js/views/composer.js       §7.3
  js/views/inspector.js      §8
  js/views/project.js        §9
  js/views/settings.js       §10
  js/views/inputs.js         kept, restyled
  js/views/tabs/*.js         kept; class names may change with the CSS
```

Removed: `console.css`, `views/rail.js`, `views/run.js`, `views/new-run.js`. `app.py` and
`server.py` are unchanged.

## 16. Testing

- **Python (fast lane, `tests/thymira/test_web.py`)**: the pass-through tests stay; the resume
  invariant test points at `views/thread.js` with the same assertions; new tests: every relative
  `import … from "./…js"` in `static/js/**` resolves to a file; `index.html` references only
  existing `/static/…` assets; every static JS module passes `node --check` when `node` is on
  `PATH` (skipped otherwise, never failed).
- **Node unit tests (fast lane, run from pytest)**: `tests/thymira/web_js/*.test.mjs` run with
  `node --test` when node is available: `thread-fold` (prompt, question/answer sources, agent
  grouping with tool rows, message concatenation by request id, hidden event kinds, stage
  dividers, outcome), `markdown` (no HTML passthrough, only http(s) links, fences),
  `router` (aliases and panel/item parsing). The pytest wrapper runs them as one test and skips
  without node.
- **API**: `tests/thymira/test_api_project.py` (or the existing project route test module) gains
  `GET /project` cases: summary fields, 503 when unconfigured, read scope.
- **Manual visual verification**: start the API on `examples/credit-risk` and the console, open
  `http://127.0.0.1:8080`, connect with the token in `.thymira/runtime/api-token`, screenshot
  home, a completed thread (`run_635b0c4c…`, 449 events), the inspector on Artifacts and Events,
  the project page, light and dark; check `prefers-reduced-motion`.

## 17. Accessibility

Landmarks (`nav`, `main`, `aside`, `dialog`), `aria-current` on the selected thread and panel,
`role="status"` for toasts and action status, `aria-expanded` on collapsibles, visible focus
rings, full keyboard operation of the palette, lists and composer, 4.5:1 text contrast on both
themes, and tones never as the only signal (an icon or label always accompanies a colour).

## 18. Follow-ups (not in this program)

- A project switcher once the API serves more than one workspace.
- Streaming assistant text chunk by chunk (the log records chunks; the transcript renders them
  when their request completes).
- Artifact diff views and an in-console file tree (needs an API surface for workspace files).
