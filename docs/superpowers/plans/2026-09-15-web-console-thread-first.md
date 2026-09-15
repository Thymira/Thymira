# Thread-first Web Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Thymira web console (`apps/web`) as a thread-first IDE shell: a sidebar of threads under the project, a transcript folded from the Run's event log with one state-driven composer, and an inspector hosting the existing evidence renderers; plus one read-only `GET /project` API route.

**Architecture:** Vanilla ES modules with no build step, served by the unchanged Starlette app under a `default-src 'none'` CSP. Pure folds (`thread-fold.js`, `markdown.js` parser, `router.js`) are unit-tested with `node --test`; DOM views are classes that render with the `h()` builder (text nodes only). CSS is a token-based design system split by responsibility. The API gains `ProjectInputs.config()` and `GET /project`.

**Tech Stack:** JavaScript (ES2022 modules, no dependencies), CSS custom properties, Starlette (unchanged), FastAPI + pydantic for the API route, pytest, Node ≥ 22 for JS tests (skipped when absent).

**Spec:** `docs/superpowers/specs/2026-09-15-web-console-thread-first-design.md` — read it first; every section number below refers to it.

## Global Constraints

- Everything is English: code, comments, docs, commits, UI copy.
- No external asset of any kind (fonts, icons, scripts, styles); CSP is `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'`. No inline `style=""` attributes with data, no inline `<script>`. `h(tag, { style: {...} })` (a JS style object) is allowed.
- Run data reaches the page only as text nodes or attributes built by `h()`; **never** `innerHTML`, `insertAdjacentHTML`, `outerHTML` or `DOMParser` on API data.
- The console holds no Run state and adds no authority; the token stays in `sessionStorage` (`api.js` unchanged in that respect).
- Wire vocabulary (`RunStatus`, `Decision`, event types, ids) is never renamed; only shell copy says "thread" and "project".
- The run list is never polled on a timer.
- Python: ruff full rule set (`line-length = 100`, Google docstrings), ty zero diagnostics, tests in `tests/thymira/`, `slow`/`integration` markers, write files with `newline="\n"`.
- JS style: 2-space indent, double quotes, semicolons, `const`/`let`, arrow functions for callbacks, explicit `.js` in import specifiers, a leading comment block on every module saying what it is and what it must never do.
- Every JS module under `static/js/` must pass `node --check` and must not touch `window`/`document` at import time (only inside functions), so node can import the pure ones.
- Commit policy for subagents: **do not commit** unless the brief says otherwise; the parent session owns commits (one commit per task, Conventional Commits, `uv.lock` untouched since no dependency changes).
- Verification gate: `just check` (lint, ty ratchet, import contracts, skills, roadmap, notes, fast tests). Fast JS tests run inside `just test`.

---

## File structure and ownership

| Path | Task | Responsibility |
|---|---|---|
| `runtime/core/src/thymira/core/project_inputs.py` | 1 | `ProjectInputs.config()` |
| `apps/api/src/thymira/api/schemas.py`, `routes/project.py`, `__init__.py` | 1 | `ProjectSummaryResponse`, `GET /project` |
| `docs/contracts/api-v0.1.md`, `tests/thymira/test_api_project.py`, `tests/thymira/test_api_contract.py` | 1 | contract + tests |
| `apps/web/src/thymira/web/static/js/package.json` | 2 | `{"type": "module"}` so node treats the modules as ESM |
| `static/js/router.js`, `prefs.js`, `theme.js`, `icons.js`, `toast.js`, `shortcuts.js`, `palette.js`, `markdown.js`, `thread-fold.js` | 2 | pure and shell utilities |
| `static/js/ui.js` (additions), `static/js/api.js` (`project()`) | 2 | shared primitives |
| `tests/thymira/web_js/*.test.mjs`, `tests/thymira/test_web_js.py`, `tests/thymira/test_web_static.py` | 2 | JS unit tests and static checks |
| `static/index.html`, `static/css/*.css` | 3 | shell markup and the whole design system |
| `static/js/main.js`, `views/sidebar.js`, `views/home.js`, `views/project.js`, `views/settings.js`, `views/inputs.js` (restyle) | 4 | shell views |
| `static/js/views/thread.js`, `views/transcript.js`, `views/composer.js`, `views/inspector.js`, `views/tabs/*.js` (class tweaks) | 5 | thread views |
| delete `console.css`, `views/rail.js`, `views/run.js`, `views/new-run.js`; `tests/thymira/test_web.py`; `apps/web/README.md`; `CHANGELOG.md` | 6 | integration |

Tasks 1, 2 and 3 are independent and run in parallel. Tasks 4 and 5 depend on 2 and 3 and run in parallel with each other (disjoint files). Task 6 depends on 4 and 5.

---

## Shared contracts (read before any task)

### C1. `index.html` skeleton and element ids (Task 3 writes it; Tasks 4–5 rely on it)

```html
<!doctype html>
<html lang="en" data-theme="system">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Thymira</title>
  <link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/css/tokens.css">
  <link rel="stylesheet" href="/static/css/base.css">
  <link rel="stylesheet" href="/static/css/layout.css">
  <link rel="stylesheet" href="/static/css/components.css">
  <link rel="stylesheet" href="/static/css/thread.css">
  <link rel="stylesheet" href="/static/css/panels.css">
  <link rel="stylesheet" href="/static/css/motion.css">
  <script type="module" src="/static/js/main.js"></script>
</head>
<body>
  <div class="shell" id="shell" data-sidebar="open" data-inspector="closed">
    <nav class="sidebar" id="sidebar" aria-label="Threads"></nav>
    <div class="scrim" id="scrim" hidden></div>
    <main class="main" id="main"></main>
    <aside class="inspector" id="inspector" aria-label="Inspector" hidden></aside>
  </div>
  <div class="banner" id="banner" hidden role="status"></div>
  <div class="toasts" id="toasts" aria-live="polite"></div>
  <div class="palette-host" id="palette" hidden></div>
  <dialog class="dialog connect" id="connect" aria-labelledby="connect-title">…same form as today, ids unchanged (connect-form, token, connect-error, connect-cancel)…</dialog>
  <dialog class="dialog" id="confirm" aria-labelledby="confirm-title"></dialog>
</body>
</html>
```

`data-sidebar` ∈ `open | collapsed | drawer-open` (drawer only below 900px); `data-inspector` ∈ `open | closed`. `#shell` also gets `style.setProperty("--sidebar-width", …)` and `--inspector-width` from prefs (numbers in px).

### C2. CSS class contract (Task 3 styles these; Tasks 4–5 emit exactly these names)

Shell (`layout.css`): `.shell`, `.sidebar`, `.sidebar-brand`, `.sidebar-collapse`, `.wordmark`, `.wordmark-glyph`, `.wordmark-name`, `.project-card`, `.project-name`, `.project-meta`, `.project-dot[data-state=online|offline|unknown]`, `.sidebar-new`, `.sidebar-search`, `.filter-pills`, `.pill[aria-pressed]`, `.thread-groups`, `.thread-group`, `.thread-group-title`, `.thread-list`, `.thread-item[aria-current=page]`, `.thread-dot.tone-*[data-live]`, `.thread-title`, `.thread-meta`, `.thread-attention`, `.sidebar-footer`, `.sidebar-message`, `.main`, `.view`, `.inspector`, `.inspector-head`, `.inspector-tabs`, `.inspector-tab[aria-current=page]`, `.inspector-badge[data-attention]`, `.inspector-body`, `.inspector-resize`, `.inspector-close`, `.scrim`, `.banner`, `.toasts`, `.toast.tone-*`, `.palette-host`, `.palette`, `.palette-input`, `.palette-list`, `.palette-item[aria-selected]`, `.palette-group`, `.palette-hint`, `.dialog`, `.dialog-body`, `.dialog-actions`, `.icon-rail` (collapsed sidebar buttons), `.menu-button` (mobile).

Components (`components.css`): `.btn`, `.btn-primary`, `.btn-ghost`, `.btn-danger`, `.btn-approve`, `.btn-small`, `.btn-icon`, `.input`, `.input-small`, `.textarea`, `.chip`, `.chips`, `.tag.tone-*`, `.status.tone-*`, `.sq`, `.card`, `.card-head`, `.kv`, `.grid` (tables), `.table-wrap`, `.row-detail`, `.is-expandable`, `.is-open`, `.skeleton`, `.skeleton-line`, `.empty`, `.loading`, `.notice.tone-*`, `.muted`, `.small`, `.mono`, `.eyebrow`, `.copyable`, `.copy`, `.avatar[data-kind=thy|mira|user|agent|tool]`, `.stack`, `.stack-tight`, `.form-actions`, `.form-status`, `.field`, `.field-label`, `.check`, `.kbd`, `.divider`.

Thread (`thread.css`): `.thread`, `.thread-head`, `.thread-head-title`, `.thread-head-meta`, `.thread-head-actions`, `.stepper`, `.step.is-done|.is-current|.tone-*`, `.step-dot`, `.step-label`, `.step-line`, `.transcript`, `.transcript-column`, `.turn[data-kind=…]` (one per ThreadItem kind), `.turn-head`, `.turn-name`, `.turn-time`, `.turn-body`, `.prose` (markdown output), `.user-card`, `.stage-divider`, `.agent-group[open]`, `.agent-summary`, `.agent-children`, `.tool-row[data-status]`, `.tool-name`, `.tool-summary`, `.tool-detail`, `.review-card.is-answerable`, `.review-actions`, `.risk-card`, `.plan-card`, `.plan-items`, `.plan-item[data-state]`, `.audit-group`, `.finding`, `.outcome-card.tone-*`, `.working-row`, `.dots`, `.scroll-pill`, `.composer`, `.composer-context`, `.composer-field`, `.composer-toolbar`, `.composer-send`, `.composer-hint`, `.composer-review`, `.composer-error`, `.home`, `.home-hero`, `.home-cards`, `.home-recent`.

Panels (`panels.css`): everything the tab renderers already use, ported from `console.css` and restyled: `.panel`, `.panel-head`, `.panel-aside`, `.panel-body`, `.columns`, `.span-2`, `.summary-strip`, `.stat`, `.tally`, `.findings`, `.cards`, `.callout`, `.decide`, `.toolbar`, `.event-type`, `.family-*`, `.artifacts`, `.artifact-list`, `.list-tools`, `.list-summary`, `.file-list`, `.file-group`, `.file`, `.file-name`, `.file-meta`, `.artifact-preview`, `.preview-image`, `.csv`, `.events`, `.bars`, `.bar-row`, `.bar-track`, `.bar-fill`, `.code`, `.prose-code`, `.control-legend`, `.inputs`, `.dataset-list`, `.dataset`, `.dataset-head`, `.dataset-name`, `.dataset-actions`, `.dataset-add`, `.dataset-add-row`, `.field-inline`, `.chosen-file`, `.upload-progress`, `.context-editor`, `.prompt-text`, `.question`, `.plain-list`, `.settings`, `.settings-section`, `.project`, `.project-head`.

Tones: `.tone-neutral|active|review|ok|caution|warn|block|thy|mira` set `--tone`.

### C3. Module interfaces (Task 2 produces; Tasks 4–5 consume)

```js
// router.js  (pure except navigate)
export const PANELS = ["details", "plan", "agents", "reviews", "tools", "artifacts", "audit", "events", "usage", "inputs", "interview"];
export const PANEL_ALIASES = { overview: "details", approvals: "reviews" };
export function parseRoute(hash) // → { view: "home"|"thread"|"project"|"settings", runId: string|null, panel: string|null, item: string|null }
export function href(route)      // inverse of parseRoute; href({ view: "thread", runId, panel, item })
export function navigate(route)  // route object or "#/…" string → sets window.location.hash

// prefs.js
export function readPref(key, fallback) // from localStorage "thymira.console.prefs" (JSON object); try/catch everywhere
export function writePref(key, value)

// theme.js
export const THEMES = ["system", "light", "dark"];
export function currentTheme()  // from prefs, default "system"
export function applyTheme(name) // sets document.documentElement.dataset.theme = name and writes the pref
export function cycleTheme()     // system → light → dark → system; returns the new name

// icons.js
export const ICON_NAMES; // array of the 30 names in spec §12
export function icon(name, { size = 16, className = null, title = null } = {}) // → <svg> built with createElementNS; unknown name renders "circle"

// toast.js
export function toast(text, { tone = "neutral", duration = 3500 } = {}) // appends .toast.tone-<tone> to #toasts, removes after duration; click dismisses

// shortcuts.js
export function isTyping(target) // true for input, textarea, select, [contenteditable]
export function installShortcuts(bindings) // bindings: Array<{ keys: "ctrl+k"|"ctrl+b"|"ctrl+i"|"?"|"escape", whenTyping?: boolean, run(event) }>; returns uninstall()

// palette.js
export function fuzzyMatch(query, text) // → { score: number, indices: number[] } | null ; case-insensitive subsequence, bonus for word starts
export class CommandPalette { constructor(host, { commands }) ; open(); close(); toggle(); get isOpen }
// commands: () => Array<{ id, label, hint?: string, group: "Actions"|"Threads", run(): void }>

// markdown.js
export function parseMarkdown(text) // → Block[]  (pure; tested in node)
// Block = { type: "heading", level: 1|2|3, inlines } | { type: "paragraph", inlines } | { type: "code", lang: string, text }
//       | { type: "list", ordered: boolean, items: Inline[][] } | { type: "quote", inlines }
// Inline = { type: "text", text } | { type: "code", text } | { type: "strong", inlines } | { type: "em", inlines } | { type: "link", href, inlines }  (href only when it starts with http:// or https://; otherwise the raw text is a text inline)
export function renderMarkdown(text) // → DocumentFragment with class "prose" children, built with h(); code blocks get a language label and a copy button

// thread-fold.js (pure; tested in node) — see C4
export function foldThread(run, events) // → ThreadItem[]
export const HIDDEN_EVENT_TYPES; // Set of the spec's hidden types
export function stageLabel(stage) // "planning" → "Planning", "executing"/"experimenting" → "Executing", "auditing" → "Auditing", "reporting" → "Reporting"

// ui.js additions (keep every existing export)
export function skeleton(lines = 3)             // .skeleton with n .skeleton-line
export function iconButton(name, { label, onClick, kind, pressed, title }) // .btn.btn-icon with icon() and aria-label
export function avatar(kind, text)              // .avatar[data-kind] with a 1–2 letter text or icon
export function confirmDialog({ title, body, confirmLabel = "Confirm", danger = false }) // → Promise<boolean> using #confirm <dialog>
export function kbd(text)                       // <kbd class="kbd">

// api.js addition
project: () => request("GET", "/project"),
```

### C4. `ThreadItem` shapes (Task 2 produces; Task 5 renders)

Every item has `key` (stable string), `kind`, `version` (highest event `seq` that changed it), `ts` (ISO of its first event).

```js
{ kind: "prompt", key: "prompt", text, ts }                               // run.prompt, run.created_at
{ kind: "stage", key: `stage:${seq}`, stage, ts }                          // run.transitioned whose payload.stage differs from the previous stage; skip the first "planning"
{ kind: "question", key: `question:${seq}`, number, field, text, ts }      // activity_profile.questioned: payload.question_number, field, question
{ kind: "answer", key: `answer:${seq}`, field, text, source, by, ts }      // activity_profile.answered: answer, answer_source ("live_human"|"project_context"|other), answered_by ?? actor.id
{ kind: "risk", key: `risk:${seq}`, level, category, confidence, needsHumanReview, factors, missing, ts } // risk.classified payload.risk_profile
{ kind: "plan", key: "plan", ts }                                          // inserted once at the earlier of: first plan.* event, first transition to stage executing/experimenting; version = latest plan.* seq
{ kind: "agent", key: `agent:${id}`, id, name, objective, status, framework, depth, items: ThreadItem[], ts, endTs }
{ kind: "message", key: `message:${requestId}`, agent, text, structured, ts } // chunks grouped by payload.request_id; text = concatenated message.content of role "assistant" chunks in source_sequence order; skip when trimmed text is empty; structured = text starts with "{" or "[" and JSON.parse succeeds; agent = innermost open agent name or "THY"
{ kind: "tool", key: `tool:${call.key}`, tool, args, status, resultCode, exitCode, error, reason, denials, artifactIds, attempts, agent, ts } // from foldTools(events, byTask); position = seq of its first attempt
{ kind: "review", key: `review:${decisionId}`, decisionId, summary, reason, ruleId, toolCall, sandboxMode, expiresAt, resolution, ts } // from foldApprovals; position = request seq; resolution = { approved, automatic, note, actor, ts } | null
{ kind: "agent_summary", key: `summary:${seq}`, agent, status, text, ts }  // agent.message without payload.form
{ kind: "audit", key: `audit:${seq}`, status, findings: [{ id, severity, title, finding, recommendation, control_id, confidence }], items: ThreadItem[], ts, endTs } // audit.started … audit.completed; agents started in between nest under items
{ kind: "outcome", key: "outcome", status, decision, error, ts }           // when run.status is COMPLETED|BLOCKED|FAILED (use run.final_decision, run.error, run.completed_at ?? last event ts)
```

Nesting: keep a stack of open agents; `agent.started` pushes (by `payload.agent_id ?? subject_id`), `agent.completed` and `agent.parked` pop that agent (a parked agent that later starts again reopens the same item and extends it). `message`, `tool`, `review`, `agent_summary` land in the innermost open agent's `items`; with no open agent they land at the top level (or under the open `audit` item during the audit). An `agent` opened while an `audit` is open nests under the audit. Top-level order is by the seq of each item's first event.

### C5. View classes (Tasks 4–5 produce; `main.js` in Task 4 wires them)

```js
// views/sidebar.js
export class Sidebar { constructor(root, { onNewThread }); refresh(); upsert(run); select(runId|null); setProject(summary|null); setApi(online: boolean|null); setCollapsed(bool); get runs }
// views/home.js
export class HomeView { constructor(root, { onCreated(run), recent: () => Run[] , project: () => summary|null }); dispose() }
// views/project.js
export class ProjectView { constructor(root, { project: () => summary|null }); dispose() }
// views/settings.js
export class SettingsView { constructor(root, { onConnect(), onDisconnect(), hasToken: () => boolean }); dispose() }
// views/thread.js
export class ThreadView { constructor(main, inspectorRoot, runId, panel, item, hooks /* onRunChanged, onRunCreated, onInspectorChange(panel|null) */); setRoute(panel, item); dispose(); get runId }
// views/transcript.js
export class Transcript { constructor(root, deps /* { api, runId, onApprove(decisionId, note), onReject(decisionId, note), onRunAgain(), loadPlan: () => Promise } */); render(items, { run, state, focusDecisionId, answerable }); setWorking(label|null); scrollToKey(key) }
// views/composer.js
export class Composer { constructor(root, { mode: "new"|"thread", onSend(text), onApprove(note), onReject(note), onResume(), onRunAgain(), onShowCall(), datasets: () => Array<{name}> }); setState(state /* { kind: "loading"|"new"|"answer"|"review"|"paused"|"working"|"ended", question?, review?, stage? } */); setBusy(bool); setError(text|null); focus(); value(); clear(); insert(text) }
// views/inspector.js
export class Inspector { constructor(root, { onClose(), onPanelChange(panel), onWidth(px) }); open(panel, item); close(); toggle(); get isOpen; get panel; update(ctx); setBadges({ [panel]: { count, attention } }) }
```

---

### Task 1: `GET /project` in the API

**Files:**
- Modify: `runtime/core/src/thymira/core/project_inputs.py` (add `ProjectInputs.config()` after `datasets()` at ~line 270)
- Modify: `apps/api/src/thymira/api/schemas.py` (add `ProjectSummaryResponse` next to `ProjectContextResponse`, ~line 439)
- Modify: `apps/api/src/thymira/api/routes/project.py` (import, route `GET /project` before `GET /project/context`)
- Modify: `apps/api/src/thymira/api/__init__.py` (export `ProjectSummaryResponse` next to `ProjectContextResponse`)
- Modify: `docs/contracts/api-v0.1.md` (section "Project inputs", add `GET /project` before `GET /project/context`)
- Modify: `tests/thymira/test_api_contract.py` (pinned route table ~line 258: add `("GET", "/project"): (200, "ProjectSummaryResponse")`)
- Test: `tests/thymira/test_api_project.py`

**Interfaces:**
- Produces: `ProjectInputs.config() -> ProjectConfig` (raises `ProjectInputError` when config.yaml is missing or invalid, exactly like `datasets()`); `ProjectSummaryResponse(project_id, name, domain, frameworks: tuple[str, ...], audit_required: bool, datasets: tuple[str, ...])`; `GET /project` (read permission, 503 `project_not_configured`, 409 `project_config_invalid`).

- [ ] **Step 1: Write the failing tests** (append to `tests/thymira/test_api_project.py`)

```python
def test_the_project_summary_names_the_workspace(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/project")

    assert response.status_code == 200
    body = ProjectSummaryResponse.model_validate(response.json())
    assert body.name == "credit-risk"
    assert body.domain == "credit_risk"
    assert body.datasets == ("german_credit",)
    assert body.audit_required is True
    assert body.frameworks == ()


def test_the_project_summary_refuses_an_unconfigured_api(tmp_path: Path) -> None:
    client = _client(tmp_path, configured=False)

    response = client.get("/project")

    assert response.status_code == 503
    assert response.json()["code"] == "project_not_configured"
```

Add `ProjectSummaryResponse` to the `from thymira.api import (...)` block.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/thymira/test_api_project.py -k summary -v`
Expected: ImportError for `ProjectSummaryResponse`.

- [ ] **Step 3: Implement**

`project_inputs.py`:

```python
    def config(self) -> ProjectConfig:
        """Return the validated ``config.yaml``.

        Raises:
            ProjectInputError: The file is missing, is not YAML, or does not validate.
        """
        _, _, config = self._load_config()
        return config
```

`schemas.py`:

```python
class ProjectSummaryResponse(BaseModel):
    """What ``config.yaml`` declares about the project the API serves, for a client's shell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: Id
    name: str
    domain: str
    frameworks: tuple[str, ...] = ()
    audit_required: bool = True
    datasets: tuple[str, ...] = ()
```

`routes/project.py` (before `get_context`):

```python
@router.get(
    "",
    response_model=ProjectSummaryResponse,
    dependencies=[_REQUIRE_READ],
    responses=_VALIDATION_ERROR_RESPONSE,
)
def get_project(deps: RuntimeDeps = _DEPS) -> ProjectSummaryResponse:
    """Describe the configured project: its name, domain, governance and declared datasets."""
    project_id, inputs = _inputs(deps)
    try:
        config = inputs.config()
    except ProjectInputError as exc:
        raise problem(409, "project_config_invalid", str(exc)) from exc
    return ProjectSummaryResponse(
        project_id=project_id,
        name=config.project.name,
        domain=config.project.domain,
        frameworks=tuple(str(framework.value) for framework in config.governance.frameworks),
        audit_required=config.governance.audit_required,
        datasets=tuple(dataset.name for dataset in config.datasets),
    )
```

Check how `Framework` serialises (`str(framework)` vs `.value`) with a quick `uv run python -c "from thymira.schemas import Framework; print([f.value for f in Framework])"` and use the form that yields `"EU_AI_ACT"`.

Contract doc, under "Project inputs", before `### GET /project/context…`:

```markdown
### `GET /project`

Describes the project the API serves, read from `.thymira/config.yaml`, so a client's shell can
name it without reading the file:

```json
{
  "project_id": "project_0123456789abcdef0123456789abcdef",
  "name": "credit-risk",
  "domain": "credit_risk",
  "frameworks": ["EU_AI_ACT", "CREDIT_RISK"],
  "audit_required": true,
  "datasets": ["german_credit"]
}
```

`409 project_config_invalid` when the file is missing or does not validate.
```

- [ ] **Step 4: Run the tests and the contract test**

Run: `uv run pytest tests/thymira/test_api_project.py tests/thymira/test_api_contract.py -q`
Expected: all pass (the route table now includes `GET /project`).

- [ ] **Step 5: Gate**

Run: `uv run ruff check apps/api runtime/core tests/thymira/test_api_project.py && uv run ruff format --check apps/api runtime/core tests && uv run python scripts/ty_ratchet.py`
Expected: clean, `ty diagnostics: 0`.

---

### Task 2: Pure modules, shared primitives and the JS test harness

**Files:**
- Create: `apps/web/src/thymira/web/static/js/package.json` → `{"type": "module"}`
- Create: `static/js/router.js`, `prefs.js`, `theme.js`, `icons.js`, `toast.js`, `shortcuts.js`, `palette.js`, `markdown.js`, `thread-fold.js`
- Modify: `static/js/ui.js` (add `skeleton`, `iconButton`, `avatar`, `confirmDialog`, `kbd`; keep every existing export), `static/js/api.js` (add `project`)
- Create: `tests/thymira/web_js/router.test.mjs`, `markdown.test.mjs`, `thread-fold.test.mjs`, `palette.test.mjs`
- Create: `tests/thymira/test_web_js.py`, `tests/thymira/test_web_static.py`

**Interfaces:** produces everything in C3 and C4. Consumes `fold.js` (`foldTools`, `foldApprovals`, `foldAgents`, `latestRunState`) and `dom.js` (`h`).

- [ ] **Step 1: Write the node tests first** (`tests/thymira/web_js/`)

`router.test.mjs`:

```js
import assert from "node:assert/strict";
import { test } from "node:test";
import { PANELS, href, parseRoute } from "../../../apps/web/src/thymira/web/static/js/router.js";

test("the root and #/new both open the home view", () => {
  assert.deepEqual(parseRoute(""), { view: "home", runId: null, panel: null, item: null });
  assert.deepEqual(parseRoute("#/"), { view: "home", runId: null, panel: null, item: null });
  assert.deepEqual(parseRoute("#/new"), { view: "home", runId: null, panel: null, item: null });
});

test("a thread route carries its panel and item, decoding each segment", () => {
  const route = parseRoute("#/runs/run_ab%20c/artifacts/art_1");
  assert.deepEqual(route, { view: "thread", runId: "run_ab c", panel: "artifacts", item: "art_1" });
});

test("old panel names map to their new ids and unknown panels fall back to details", () => {
  assert.equal(parseRoute("#/runs/r/overview").panel, "details");
  assert.equal(parseRoute("#/runs/r/approvals").panel, "reviews");
  assert.equal(parseRoute("#/runs/r/nope").panel, "details");
  assert.equal(parseRoute("#/runs/r").panel, null);
});

test("project and settings routes parse and unknown sections go home", () => {
  assert.equal(parseRoute("#/project").view, "project");
  assert.equal(parseRoute("#/settings").view, "settings");
  assert.equal(parseRoute("#/whatever").view, "home");
});

test("href is the inverse of parseRoute", () => {
  for (const route of [
    { view: "home", runId: null, panel: null, item: null },
    { view: "thread", runId: "run_1", panel: null, item: null },
    { view: "thread", runId: "run 1", panel: "events", item: null },
    { view: "thread", runId: "run_1", panel: "artifacts", item: "art/1" },
    { view: "project", runId: null, panel: null, item: null },
    { view: "settings", runId: null, panel: null, item: null },
  ]) {
    assert.deepEqual(parseRoute(href(route)), route);
  }
  assert.equal(PANELS.length, 11);
});
```

`markdown.test.mjs`:

```js
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseMarkdown } from "../../../apps/web/src/thymira/web/static/js/markdown.js";

test("paragraphs, headings, lists, quotes and fences are recognised", () => {
  const blocks = parseMarkdown("# Title\n\nHello **world** and `code`.\n\n- one\n- two\n\n1. first\n\n> quoted\n\n```python\nprint(1)\n```\n");
  assert.deepEqual(blocks.map((block) => block.type), ["heading", "paragraph", "list", "list", "quote", "code"]);
  assert.equal(blocks[0].level, 1);
  assert.equal(blocks[2].ordered, false);
  assert.equal(blocks[3].ordered, true);
  assert.equal(blocks[5].lang, "python");
  assert.equal(blocks[5].text, "print(1)");
});

test("inline emphasis and code nest as inlines, never as markup", () => {
  const [paragraph] = parseMarkdown("Hello **world** and `x < y`");
  assert.deepEqual(paragraph.inlines, [
    { type: "text", text: "Hello " },
    { type: "strong", inlines: [{ type: "text", text: "world" }] },
    { type: "text", text: " and " },
    { type: "code", text: "x < y" },
  ]);
});

test("only http(s) links become links; everything else stays text", () => {
  const [paragraph] = parseMarkdown("[ok](https://example.org) [bad](javascript:alert(1)) <b>raw</b>");
  const types = paragraph.inlines.map((inline) => inline.type);
  assert.equal(types.filter((type) => type === "link").length, 1);
  assert.equal(paragraph.inlines[0].href, "https://example.org");
  assert.ok(paragraph.inlines.some((inline) => inline.type === "text" && inline.text.includes("<b>raw</b>")));
  assert.ok(!paragraph.inlines.some((inline) => inline.href && inline.href.startsWith("javascript:")));
});

test("an unterminated fence still yields a code block and never swallows the document", () => {
  const blocks = parseMarkdown("```\nabc");
  assert.deepEqual(blocks, [{ type: "code", lang: "", text: "abc" }]);
});
```

`palette.test.mjs`:

```js
import assert from "node:assert/strict";
import { test } from "node:test";
import { fuzzyMatch } from "../../../apps/web/src/thymira/web/static/js/palette.js";

test("a subsequence matches case-insensitively and reports its indices", () => {
  const match = fuzzyMatch("nt", "New thread");
  assert.ok(match);
  assert.deepEqual(match.indices, [0, 4]);
});

test("a query that is not a subsequence does not match, and an empty query matches everything", () => {
  assert.equal(fuzzyMatch("xyz", "New thread"), null);
  assert.ok(fuzzyMatch("", "anything"));
});

test("word-start matches score higher than mid-word matches", () => {
  assert.ok(fuzzyMatch("ne", "New thread").score > fuzzyMatch("ne", "Fine tuned").score);
});
```

`thread-fold.test.mjs` (build events with a tiny helper; `seq` ascending, `ts` ISO strings):

```js
import assert from "node:assert/strict";
import { test } from "node:test";
import { HIDDEN_EVENT_TYPES, foldThread, stageLabel } from "../../../apps/web/src/thymira/web/static/js/thread-fold.js";

let seq = 0;
const at = (n) => new Date(Date.UTC(2026, 8, 15, 10, 0, n)).toISOString();
function ev(type, payload = {}, extra = {}) {
  seq += 1;
  return { seq, ts: at(seq), type, payload, actor: { kind: "system", id: "system" }, subject_id: null, ...extra };
}
const run = (over = {}) => ({ id: "run_1", prompt: "Profile german_credit", status: "RUNNING", created_at: at(0), final_decision: null, error: null, ...over });

test("the prompt opens the thread and hidden kinds never appear", () => {
  seq = 0;
  const items = foldThread(run(), [ev("run.started"), ev("policy.decision", { decision: "PASS" }), ev("model.selected", { model: "m" })]);
  assert.deepEqual(items.map((item) => item.kind), ["prompt"]);
  assert.equal(items[0].text, "Profile german_credit");
  assert.ok(HIDDEN_EVENT_TYPES.has("policy.decision"));
});

test("questions and answers keep their source, and stage changes become dividers", () => {
  seq = 0;
  const items = foldThread(run(), [
    ev("run.transitioned", { command: "start", stage: "planning", state: { stage: "planning", condition: "active" } }),
    ev("activity_profile.questioned", { question_number: 1, field: "purpose", question: "**What** is the purpose?" }),
    ev("activity_profile.answered", { field: "purpose", answer: "Scoring", answer_source: "live_human", answered_by: "operator" }, { actor: { kind: "human", id: "operator" } }),
    ev("activity_profile.answered", { field: "jurisdiction", answer: "EU", answer_source: "project_context", source_ref: ".thymira/context.md" }),
    ev("run.transitioned", { command: "begin_execution", stage: "executing", state: { stage: "executing", condition: "active" } }),
  ]);
  assert.deepEqual(items.map((item) => item.kind), ["prompt", "question", "answer", "answer", "plan", "stage"]);
  assert.equal(items[1].number, 1);
  assert.equal(items[2].source, "live_human");
  assert.equal(items[3].source, "project_context");
  assert.equal(items[5].stage, "executing");
  assert.equal(stageLabel("experimenting"), "Executing");
});

test("an agent groups its messages, tool calls, reviews and summary; chunks concatenate by request", () => {
  seq = 0;
  const events = [
    ev("agent.started", { agent: "data", agent_id: "agent_1", task_id: "task_1", objective: "Profile it" }),
    ev("model.response_chunk", { request_id: "req_1", source_sequence: 0, message: { role: "assistant", content: "Hello " } }),
    ev("model.response_chunk", { request_id: "req_1", source_sequence: 1, message: { role: "assistant", content: "world" }, terminal: { outcome: "success", chunk_count: 2 } }),
    ev("model.response_chunk", { request_id: "req_2", source_sequence: 0, message: { role: "assistant", content: "", tool_calls: [{ name: "profile_dataset" }] }, terminal: { outcome: "success", chunk_count: 1 } }),
    ev("human.approval_requested", { decision_id: "dec_1", summary: "profile_dataset", reason: "side effects", tool: "profile_dataset", arguments: { name: "german_credit" }, tool_intent_sha256: "abc" }),
    ev("tool.denied", { tool: "profile_dataset", tool_call_id: "call_1", tool_intent_sha256: "abc", task_id: "task_1", reason: "denied for review", decision_id: "dec_1", arguments: { name: "german_credit" } }),
    ev("agent.parked", { agent: "data", agent_id: "agent_1", status: "PENDING" }),
    ev("human.approval", { decision_id: "dec_1", approved: true, note: "go" }, { actor: { kind: "human", id: "operator" } }),
    ev("agent.started", { agent: "data", agent_id: "agent_1", task_id: "task_1" }),
    ev("agent.message", { agent: "data", form: "runtime_context", text: "Current runtime context" }),
    ev("tool.started", { tool: "profile_dataset", tool_call_id: "call_2", tool_intent_sha256: "abc", task_id: "task_1" }),
    ev("tool.completed", { tool: "profile_dataset", tool_call_id: "call_2", tool_intent_sha256: "abc", status: "COMPLETED", result_code: "SUCCESS", artifact_ids: ["art_1"] }),
    ev("agent.completed", { agent: "data", agent_id: "agent_1", status: "COMPLETED" }),
    ev("agent.message", { agent: "data", status: "COMPLETED", text: "data completed: 1000 rows" }),
  ];
  const items = foldThread(run(), events);
  assert.deepEqual(items.map((item) => item.kind), ["prompt", "agent", "agent_summary"]);
  const agent = items[1];
  assert.equal(agent.name, "data");
  assert.equal(agent.status, "COMPLETED");
  assert.deepEqual(agent.items.map((item) => item.kind), ["message", "review", "tool"]);
  assert.equal(agent.items[0].text, "Hello world");
  assert.equal(agent.items[0].agent, "data");
  assert.equal(agent.items[1].resolution.approved, true);
  assert.equal(agent.items[2].status, "COMPLETED");
  assert.deepEqual(agent.items[2].artifactIds, ["art_1"]);
  assert.equal(agent.items[2].denials, 1);
  assert.ok(agent.version >= events.at(-2).seq);
});

test("structured JSON content is flagged, the audit nests its agents, and a terminal run ends with an outcome", () => {
  seq = 0;
  const events = [
    ev("risk.classified", { risk_profile: { risk_level: "high", activity_category: "model_development", confidence: 0.94, needs_human_review: true, risk_factors: ["a"], missing_information: [] } }),
    ev("model.response_chunk", { request_id: "req_9", source_sequence: 0, message: { role: "assistant", content: "{\"tasks\":[]}" }, terminal: { outcome: "success", chunk_count: 1 } }),
    ev("audit.started", {}),
    ev("agent.started", { agent: "euaiact", agent_id: "agent_9" }),
    ev("agent.completed", { agent: "euaiact", agent_id: "agent_9", status: "COMPLETED" }),
    ev("audit.finding", { finding: { id: "f1", severity: "MEDIUM", title: "Confinement enforced", control_id: "A9" } }),
    ev("audit.completed", { status: "PASS" }),
    ev("run.transitioned", { command: "complete", stage: "reporting", state: { stage: "reporting", condition: "terminal", outcome: "completed" } }),
    ev("run.completed", {}),
  ];
  const items = foldThread(run({ status: "COMPLETED", final_decision: "WARNING" }), events);
  assert.deepEqual(items.map((item) => item.kind), ["prompt", "risk", "message", "audit", "stage", "outcome"]);
  assert.equal(items[1].level, "high");
  assert.equal(items[2].structured, true);
  assert.equal(items[2].agent, "THY");
  assert.deepEqual(items[3].items.map((item) => item.kind), ["agent"]);
  assert.equal(items[3].findings.length, 1);
  assert.equal(items[3].status, "PASS");
  assert.equal(items[5].decision, "WARNING");
});
```

`tests/thymira/test_web_js.py`:

```python
"""Run the console's node unit tests (pure folds, markdown parser, router, palette matching)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SUITE = Path(__file__).resolve().parent / "web_js"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_console_node_suite_passes() -> None:
    files = sorted(str(path) for path in SUITE.glob("*.test.mjs"))
    assert files, "no node tests found"
    result = subprocess.run(  # noqa: S603  # fixed executable, repository files only
        [NODE, "--test", *files], capture_output=True, text=True, check=False, encoding="utf-8"
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
```

`tests/thymira/test_web_static.py`:

```python
"""Static checks over the console's shipped assets: the console has no build step to catch them."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from thymira.web.app import STATIC_DIR

NODE = shutil.which("node")
IMPORT_RE = re.compile(r"""from\s+["'](\.{1,2}/[^"']+)["']""")
ASSET_RE = re.compile(r"""(?:href|src)=["']/static/([^"']+)["']""")
MODULES = sorted(STATIC_DIR.glob("js/**/*.js"))


def test_every_relative_import_resolves_to_a_shipped_module() -> None:
    missing = [
        f"{module.relative_to(STATIC_DIR)} -> {spec}"
        for module in MODULES
        for spec in IMPORT_RE.findall(module.read_text(encoding="utf-8"))
        if not (module.parent / spec).resolve().is_file()
    ]
    assert missing == []


def test_the_shell_references_only_shipped_assets() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    missing = [asset for asset in ASSET_RE.findall(html) if not (STATIC_DIR / asset).is_file()]
    assert missing == []
    assert "console.css" not in html


@pytest.mark.parametrize("module", MODULES, ids=lambda path: str(path.relative_to(STATIC_DIR)))
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_module_parses(module: Path) -> None:
    result = subprocess.run(  # noqa: S603  # fixed executable, repository files only
        [NODE, "--check", str(module)], capture_output=True, text=True, check=False, encoding="utf-8"
    )
    assert result.returncode == 0, result.stderr
```

(`MODULES` is computed at import time; with `pytest --collect-only` that is fine. Adjust the `noqa` codes to whatever ruff actually reports; keep the reason.)

- [ ] **Step 2: Run the node suite to see it fail**

Run: `node --test tests/thymira/web_js/router.test.mjs` → fails with "Cannot find module". Same for the other three.

- [ ] **Step 3: Implement the pure modules**

Behavioural notes beyond C3/C4:

- `router.js`: `parseRoute` strips a leading `#` and `/`, splits on `/`, `decodeURIComponent`s each segment (a malformed sequence falls back to the raw segment); `href` `encodeURIComponent`s each; `navigate(route)` accepts a string beginning with `#` or a route object; a `thread` route with no panel is `#/runs/<id>`.
- `markdown.js`: a line-based block parser. Fences start with three backticks (optional language word) and end at the next line that is exactly three backticks; a fence that never closes runs to the end. `#`, `##`, `###` headings; `- `/`* ` unordered and `1. ` ordered items (consecutive lines of the same list type form one list; a following indented line continues the item); `> ` quotes; blank lines separate paragraphs; consecutive non-blank lines join a paragraph with a space. Inline parser: backtick code (no nesting), `**strong**`, `*em*` or `_em_`, `[text](url)`; unmatched markers are literal text. `renderMarkdown` maps blocks to `h("h1"…"h3")`, `h("p")`, `h("ul"|"ol", …, h("li"))`, `h("blockquote")`, and for code `h("div", { class: "code-block" }, h("div", { class: "code-block-head" }, h("span", { class: "code-lang" }, lang || "text"), copyButton), h("pre", { class: "code" }, h("code", null, text)))`; links `h("a", { href, rel: "noopener noreferrer", target: "_blank" }, …)`.
- `thread-fold.js` imports only `./fold.js`. Implement with one pass over `events` keeping: `previousStage`, `openAgents` (array stack of agent items), `openAudit`, `chunks` (`Map<requestId, { texts: Map<sourceSequence, string>, first: event, agentName }>`), `placed` (top-level array of `{ seq, item }`). Tools and reviews come from `foldTools(events, foldAgents(events).byTask)` and `foldApprovals(events)`; place each at the seq of its first attempt / its request event by inserting into the container that was open at that seq (record the open container per seq while scanning). Finish with `outcome` when the run status is terminal. Sort top-level and each container's `items` by first seq. `version` of a container = max version of itself and its children.
- `palette.js`: `fuzzyMatch` scores +2 per matched char, +3 extra when the char starts a word (index 0 or preceded by a space, `-`, `_`, `/`), −1 per gap; returns `{ score, indices }`. `CommandPalette` renders into `#palette` (`.palette` card with an `.palette-input` and a `.palette-list`), groups by `group`, keeps a selected index, handles ArrowUp/Down/Enter/Escape, closes on backdrop click, restores focus to the previously focused element on close.
- `toast.js`: creates `#toasts` children; `.toast` gets `data-leaving` before removal (CSS animates), removed after 200 ms.
- `shortcuts.js`: parses `keys` like `"ctrl+k"` (ctrl means Ctrl or Meta), `"?"` (Shift+/ produces `?` in `event.key`), `"escape"`; ignores events when `isTyping(event.target)` unless `whenTyping` is true; `preventDefault` when a binding runs.
- `icons.js`: a `PATHS` object of `name → "M…"` (24×24 viewBox, `fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"`); draw simple, recognisable Lucide-like shapes yourself (no copying of trademarked icon sets is needed for these primitives).
- `ui.js`: `confirmDialog` renders `h("form", { method: "dialog" }, h("h2", { id: "confirm-title" }, title), h("p", { class: "dialog-body" }, body), h("div", { class: "dialog-actions" }, cancel, confirm))` into `#confirm`, `showModal()`, resolves `true` only when the confirm button submits.

- [ ] **Step 4: Run the node suite and the static tests**

Run: `node --test tests/thymira/web_js/*.test.mjs` (list the files explicitly on Windows) → all pass.
Run: `uv run pytest tests/thymira/test_web_js.py tests/thymira/test_web_static.py -q` → pass (the static tests still pass against the old `index.html`; `console.css` is referenced by the old shell, so `assert "console.css" not in html` fails until Task 3 lands — that is expected; mark it `xfail(strict=False)` only if the harness must be green before Task 3, otherwise leave it and let Task 3 turn it green).

- [ ] **Step 5: Gate**

Run: `uv run ruff check tests/thymira/test_web_js.py tests/thymira/test_web_static.py && uv run ruff format --check tests && uv run python scripts/ty_ratchet.py`
Expected: clean.

---

### Task 3: Shell markup and the design system

**Files:**
- Rewrite: `apps/web/src/thymira/web/static/index.html` (C1)
- Create: `static/css/tokens.css`, `base.css`, `layout.css`, `components.css`, `thread.css`, `panels.css`, `motion.css`
- Keep: `static/favicon.svg`; `static/console.css` stays until Task 6 deletes it (nothing links it any more)

**Interfaces:** consumes C1/C2 names; produces the stylesheet set the shell links.

- [ ] **Step 1: Write `tokens.css`** exactly per spec §12 (light on `:root`, dark under `:root[data-theme="dark"]` and under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { … } }`), plus spacing (`--s-1: 4px` … `--s-6: 32px`), radii (`--r-1: 6px; --r-2: 10px; --r-3: 14px; --r-pill: 999px`), shadows, `--sidebar-width: 280px`, `--inspector-width: 420px`, `--column: 840px`, fonts, durations and easings from §13.

- [ ] **Step 2: Write `base.css`** — reset (`box-sizing`, margins), `html, body { height: 100% }`, body font/colours from tokens, `a`, headings, `code`/`pre` mono, `[hidden] { display: none !important }`, `:focus-visible` ring, `::selection`, the `.tone-*` classes, scrollbar styling (`scrollbar-width: thin; scrollbar-color`), `.muted .small .mono .eyebrow .kbd .divider`.

- [ ] **Step 3: Write `layout.css`** — `.shell` as a CSS grid `grid-template-columns: var(--sidebar-width) minmax(0, 1fr) var(--inspector-width)`; `[data-sidebar="collapsed"]` → 56px first column (`.icon-rail` visible, everything else hidden); `[data-inspector="closed"]` → third column 0 and `.inspector` hidden; sidebar sections per C2; `.inspector` with head, tabs strip (`overflow-x: auto`, no scrollbar), body scrolling, a 6px `.inspector-resize` handle on its left edge (`cursor: col-resize`); `.banner` fixed top; `.toasts` fixed bottom-center; `.palette-host` fixed overlay with `.palette` centered at 12vh; responsive rules per spec §4 (`@media (max-width: 1279px)` inspector becomes a fixed right sheet with `.scrim`; `@media (max-width: 899px)` sidebar becomes a fixed left drawer shown by `[data-sidebar="drawer-open"]`, `.menu-button` visible).

- [ ] **Step 4: Write `components.css`** — buttons (34px height, `--r-2`, primary = `--thy` background with white text, ghost = transparent, danger = block outline turning solid on hover, approve = ok, icon = 32×32 square), inputs/textarea (surface, 1px border, focus border `--thy` + `box-shadow: 0 0 0 3px color-mix(in srgb, var(--thy) 20%, transparent)`), `.chip`, `.tag`, `.status`, `.sq`, `.card`, `.kv`, tables (`.grid` with sticky uppercase mono headers, row hover, `.row-detail`), `.skeleton` shimmer lines, `.empty/.loading`, `.notice`, `.copyable/.copy`, `.avatar` (28px round; `thy` = orange gradient, `mira` = teal gradient, `user` = surface-3 with initials, `agent` = surface-2 outline, `tool` = square 22px), `.dialog` (surface, `--r-3`, backdrop `rgb(0 0 0 / .45)` + `backdrop-filter: blur(6px)`), `.field/.field-label/.check/.form-actions/.form-status`.

- [ ] **Step 5: Write `thread.css`** — `.thread` column layout (`grid-template-rows: auto 1fr auto`, header sticky), `.stepper` (dots joined by `.step-line`s that fill with `--thy` when done; current dot pulses), `.transcript` scroll area with `.transcript-column` (`max-width: var(--column)`, centered, 24px padding), `.turn` blocks (`grid-template-columns: 28px 1fr; column-gap: 12px; padding-block: 10px`), `.user-card` (surface-2, `--r-2`, 12px 14px), `.prose` typography (15px/1.6, code inline chips, `.code-block` with head bar), `.stage-divider` (hairline with centered mono label), `.agent-group` as `<details>` with a summary row (avatar, name, objective clipped, status tag, chevron rotating when open) and indented `.agent-children` with a 2px left rule, `.tool-row` (one line, hover surface, `data-status` colours the leading icon: ok/warn/block/active), `.tool-detail` (two columns on wide screens), `.review-card` (border-left 3px `--review`; `.is-answerable` glows), `.risk-card` and `.plan-card` (MIRA teal / THY orange accents), `.audit-group`, `.outcome-card`, `.working-row` with `.dots` (three 6px dots bouncing), `.scroll-pill` (floating pill above the composer), `.composer` (sticky bottom, surface, 1px border, `--r-3`, shadow; `.composer-context` slot above the field for the question/review; `.composer-field` textarea without its own border; `.composer-toolbar` with chips left and hint+send right; `.composer-send` 36px round primary button; `.composer-review` bar variant with the two decision buttons), `.home` (centered hero, cards grid, recent list).

- [ ] **Step 6: Write `panels.css`** — port every rule from the old `console.css` that the tab renderers rely on (panels, columns, summary strips, tallies, findings, cards, callouts, tables for events/csv, artifacts split, usage bars, code, control legend, inputs/datasets/context editor, settings, project page) and restyle them with the tokens (radius `--r-2`, surface hierarchy, mono uppercase eyebrows at 10.5px). In the inspector `.artifacts` is a single column (`grid-template-columns: 1fr`), list capped at 32vh and preview below.

- [ ] **Step 7: Write `motion.css`** — the keyframes from §13 (`rise`, `fade`, `pulse`, `shimmer`, `spin`, `pop`, `dots`, `slide-in-right`, `slide-in-left`), the transitions on `.shell` columns, `.sidebar`, `.inspector`, `.btn`, `.thread-item`, `.turn[data-enter]` (applied by JS on new items with `animation-delay` from `--i`), `.toast`, `.banner`, `.palette`, `.dialog[open]`, and the `prefers-reduced-motion` block that zeroes every duration.

- [ ] **Step 8: Write `index.html`** exactly as C1 (keep the connect dialog copy and ids from the current file).

- [ ] **Step 9: Verify** — open the shell against the current JS is not possible before Task 4; instead: `uv run pytest tests/thymira/test_web_static.py -q` (asset references resolve, no `console.css`), `uv run pytest tests/thymira/test_web.py -q` (index served with CSP), and a manual read of each CSS file for unknown-token typos: `grep -o "var(--[a-z0-9-]*)" static/css/*.css | sort -u` must only list tokens defined in `tokens.css` (write a one-off shell loop; do not add it to the repo).

---

### Task 4: Shell views and bootstrap

**Files:**
- Rewrite: `static/js/main.js`
- Create: `static/js/views/sidebar.js`, `static/js/views/home.js`, `static/js/views/project.js`
- Rewrite: `static/js/views/settings.js`
- Modify: `static/js/views/inputs.js` (class names only where the new CSS differs; keep behaviour)
- Do not touch: `views/thread.js`, `transcript.js`, `composer.js`, `inspector.js`, `tabs/*` (Task 5). Until Task 5 lands, `main.js` imports `./views/thread.js` — Task 5 creates it; coordinate by importing exactly `ThreadView` from `./views/thread.js` with the C5 signature.

**Interfaces:** consumes C3, C5 (`ThreadView`), `Composer` from `./views/composer.js` (Task 5; use it in `HomeView` with `mode: "new"`).

- [ ] **Step 1: `main.js`** — in order: `applyTheme(currentTheme())`; read prefs (`sidebar`, `sidebarWidth`, `inspectorWidth`, `inspectorPanel`) onto `#shell`; construct `Sidebar`, `CommandPalette`, shortcuts (`ctrl+k` palette, `ctrl+b` sidebar, `ctrl+i` → the current `ThreadView`'s inspector toggle if any, `?` → a shortcuts sheet built with `confirmDialog`-like markup listing the bindings, `escape` → close palette/drawer/inspector sheet); health polling every 15 s (unchanged) driving `sidebar.setApi` and the `#banner`; `api.project()` once after connect (404 → `setProject(null)`); the connect dialog and `thymira:unauthorized` flow exactly as today; routing via `parseRoute(location.hash)`: `home` → `HomeView`, `project` → `ProjectView`, `settings` → `SettingsView`, `thread` → reuse the live `ThreadView` when `runId` matches (`setRoute(panel, item)`) else construct; `#main` gets a `.view` child with `data-enter` for the rise animation; `hashchange` → route. Provide `onRunCreated` → `sidebar.upsert(run)` + `navigate({ view: "thread", runId: run.id })` + `toast("Thread started")`.

- [ ] **Step 2: `views/sidebar.js`** per spec §5. Data: `listAllRuns()` (all statuses, once), client-side search + filter pills, grouping by day buckets (`Today`, `Yesterday`, `This week`, `Older` computed from `created_at` vs local midnight), items as `h("a", { class: "thread-item", href: href({ view: "thread", runId }), "aria-current": selected ? "page" : null })`. Active statuses get `data-live` on the dot. Footer: refresh (`iconButton("refresh")`, `data-busy` while loading → spin), connect/disconnect (label from `hasToken`), settings link, theme toggle (`cycleTheme()` → icon `monitor|sun|moon`, `toast("Theme: dark")`). Collapsed state shows `.icon-rail` with four icon buttons. The "Loading runs…" copy stays ("The API verifies each run's event log…").

- [ ] **Step 3: `views/home.js`** per spec §6 using `Composer` (`mode: "new"`); `datasets` come from `api.projectDatasets()` (names); send → `api.createRun(text)` → `onCreated(body.run ?? body)`; draft in `sessionStorage` `thymira.console.draft` (saved on input, cleared on send); recent list from `recent()` (top 5 by `created_at`); the three "how a thread runs" cards with icons `search`, `alert`, `shield`.

- [ ] **Step 4: `views/project.js`** per spec §9: header (`.project-head` with `avatar("mira")`? no — use the `database` icon; name, domain chip, framework chips, `audit_required` tag), a "New thread" button (`navigate({ view: "home" })`), then `renderProjectInputs()`.

- [ ] **Step 5: `views/settings.js`** — keep the model-routing form logic verbatim; wrap in `.settings` with three `.settings-section` cards: Model routing, Appearance (three `.pill` buttons for `THEMES`, `aria-pressed`, calling `applyTheme`), Connection (API state line + Connect/Disconnect buttons calling the hooks).

- [ ] **Step 6: Verify** — `uv run pytest tests/thymira/test_web_static.py -q` (imports resolve; if Task 5's files are not there yet, the import test fails on `./views/thread.js` and `./views/composer.js` — acceptable until Task 5 lands, say so in the report). Run `node --check` on each new file.

---

### Task 5: Thread view, transcript, composer, inspector

**Files:**
- Create: `static/js/views/thread.js` (port the Run record/event/SSE/catch-up/batching logic from `views/run.js` — copy it, keep the comments, keep `const resumable = isResumableState(state);` and `resumable ? button("Resume", …)` verbatim), `views/transcript.js`, `views/composer.js`, `views/inspector.js`
- Modify: `views/tabs/overview.js` (drop `interviewCallout`/`reviewCallout` and the `.attention` block), other `tabs/*.js` only for class names the new CSS renamed (none required if Task 3 ported the names).
- Do not touch: `main.js`, `sidebar.js`, `home.js`, `project.js`, `settings.js` (Task 4).

**Interfaces:** consumes C3, C4; produces C5 `ThreadView`, `Transcript`, `Composer`, `Inspector`.

- [ ] **Step 1: `views/composer.js`** — one root `.composer` with `.composer-context` (empty unless `answer`/`review`), `.composer-field` (`textarea`, auto-grow between 3 and 14 rows by setting `style.height` from `scrollHeight`), `.composer-toolbar` (left slot: dataset chips in `new` mode; right: `.composer-hint` "Enter to send · Shift+Enter for a new line" and `.composer-send` button with the `send` icon), `.composer-error`. `setState` switches the surface per spec §7.3: `review` replaces the field with `.composer-review` (tool name in `mono`, first argument summary `clip(JSON.stringify(args), 80)`, "Show the call" ghost button → `onShowCall()`, a one-line note input, `Reject` (`btn-danger`) and `Approve this call` (`btn-approve`)); `paused` shows a line and a `Resume` primary button; `working` disables the field with `data-working` (shimmer) and placeholder `THY is working · <stage>` or `MIRA is auditing…` in the auditing stage; `ended` enables the field with the follow-up placeholder and a secondary `Run again`; `answer` fills `.composer-context` with `Question n · field` and the question text. Keys: `Enter` (without Shift) sends when the field is enabled and non-empty; `Shift+Enter` newline; `Ctrl/Meta+Enter` sends; `Escape` blurs. Send disables the surface (`setBusy(true)`) until the caller calls `setBusy(false)`.

- [ ] **Step 2: `views/transcript.js`** — `render(items, opts)` walks the items recursively; each item maps to an element via `renderItem(kind)`; elements are cached in `Map<key, { version, node }>`; unchanged versions keep their node (and therefore their open/closed state), changed ones are rebuilt in place, new ones appended with `data-enter` and `--i` stagger; containers (`agent`, `audit`) are `<details class="agent-group" open>` while status is running/parked and keep whatever the user toggled afterwards (record a `userToggled` flag on the `toggle` event). Item renderers:
  - `prompt`: `.turn[data-kind=prompt]` with `avatar("user")`, name "You", time, `.user-card` text.
  - `question`: avatar `mira`, name "MIRA", eyebrow `Question n · field`, `.prose` from `renderMarkdown(plainQuestion(text))`.
  - `answer`: `live_human` → like `prompt` with the answer; `project_context` → `.stage-divider`-like quiet line "Answered from context.md · field".
  - `risk`: `.risk-card` with `tag(level.toUpperCase(), riskTone(level))`, category, `percent(confidence)`, "human review required" tag when set, chips of factors.
  - `plan`: `.plan-card` that calls `deps.loadPlan()` (an `asyncBlock`) and lists items as `.plan-item[data-state]` rows (ordinal, text, state tag) plus goal and revision; re-fetches when `version` changes.
  - `agent`: `<details>` summary row (avatar `agent` with the first letter, name, objective clipped to 90 chars, status tag with `outcomeTone`, elapsed `relative`), children rendered inside `.agent-children`.
  - `message`: avatar `thy` (name THY) or `agent`; `structured` → collapsed `<details>` "Structured output" with `codeBlock(JSON.parse(text))`; else `renderMarkdown(text)`.
  - `tool`: `.tool-row` (icon `tool`, `.tool-name` mono, `.tool-summary` first argument line, status tag, artifact count chip) expanding to `.tool-detail` (arguments `codeBlock`, attempts table, error/reason notices, artifact links to `href({ view: "thread", runId, panel: "artifacts", item })`).
  - `review`: `.review-card` with the same content as today's `reviewCard`; when `opts.answerable && item.decisionId === opts.focusDecisionId` add `.is-answerable` and inline Reject/Approve buttons calling `deps.onReject/onApprove(decisionId, note)`; resolved → "Approved/Rejected by actor · note".
  - `agent_summary`: `.agent-summary` prose.
  - `audit`: `<details class="audit-group">` (avatar `mira`, "MIRA audit", status tag), nested agents, `findingList(findings)`.
  - `stage`: `.stage-divider` with `stageLabel`.
  - `outcome`: `.outcome-card.tone-<ok|block>` with `statusTag`, `decisionTag`, error notice, and a `Run again` button → `deps.onRunAgain()`.
  - `setWorking(label)` appends/removes `.working-row` (`avatar("thy")`, `.dots`, label).
  - Auto-scroll and `.scroll-pill` per spec §7.2 (the scroll container is the transcript root).

- [ ] **Step 3: `views/inspector.js`** — renders into `#inspector`: `.inspector-head` (title "Inspector", `.inspector-close` icon button), `.inspector-tabs` (one `.inspector-tab` button per panel with `icon()` + label + `.inspector-badge`), `.inspector-body`. `open(panel, item)` sets `#shell.dataset.inspector = "open"`, unhides the root, marks the tab `aria-current`, renders the panel through a `PANELS_TABLE = { details: renderOverview, plan: renderPlan, agents: renderAgents, reviews: renderApprovals, tools: renderTools, artifacts: renderArtifacts, audit: renderAudit, events: renderEvents, usage: renderUsage, inputs: renderInputs, interview: renderInterview }` with `live` flags copied from today's `TABS`; `update(ctx)` calls the live panel's `update(ctx)` when it exists, else re-renders unless the body is being edited (port `isEditing` + the stale button as `.inspector-stale`). The resize handle drags between 320 px and 60 vw and reports `onWidth`.

- [ ] **Step 4: `views/thread.js`** — copy `run.js` mechanics; replace `renderHead/renderTabBar/renderBody` with: `renderHead()` (spec §7.1), `renderTranscript()` (`foldThread(run, events)` → `transcript.render(items, { run, state, focusDecisionId: foldApprovals(events).focus?.decisionId, answerable: run.status === "WAITING_FOR_APPROVAL" })` + `setWorking`), `renderComposer()` (compute the composer state: loading → answer (uses `interviewStatus()` promise; while pending show `working`) → review → paused → working → ended), `inspector.update(context())` and `inspector.setBadges({...})` (counts as today's `renderTabBar`). Actions: approve/reject/answer/resume/cancel/run again go through `mutate()` like today and toast on success. `Cancel` uses `confirmDialog({ title: "Cancel this thread?", body: "Its pending reviews stop being answerable.", confirmLabel: "Cancel thread", danger: true })`. `setRoute(panel, item)` opens or closes the inspector and remembers the last panel in prefs. `dispose()` aborts the controller, clears timers and closes the inspector.

- [ ] **Step 5: `tabs/overview.js`** — remove the two callouts and the `.attention` wrapper; keep the panels.

- [ ] **Step 6: Verify** — `node --check` each new file; `uv run pytest tests/thymira/test_web_static.py tests/thymira/test_web_js.py -q`; then start the API and console (`uv run thymira-api --workspace examples/credit-risk` and `uv run thymira-web --api-url http://127.0.0.1:8000`), open `http://127.0.0.1:8080`, connect with the token from `examples/credit-risk/.thymira/runtime/api-token`, open the thread `run_635b0c4c9ce64cdab27aaaba6d82fd25` and check: prompt, questions/answers, risk card, plan card, `data` and `coding` agent groups with tool rows and review cards, the MIRA audit group with two findings, the outcome card; the inspector on Events shows 449 events; the composer is in `ended` state. Report what you saw, with the browser console free of errors.

---

### Task 6: Integration, cleanup, docs

**Files:**
- Delete: `static/console.css`, `static/js/views/rail.js`, `static/js/views/run.js`, `static/js/views/new-run.js`
- Modify: `tests/thymira/test_web.py` (the resume-invariant test reads `/static/js/views/thread.js`; add a test that `/static/css/tokens.css` is served as `text/css`)
- Modify: `apps/web/README.md` (describe the shell: sidebar, thread, composer, inspector, project page, shortcuts, theme; keep the security paragraphs)
- Modify: `CHANGELOG.md` `[Unreleased]` → `### Changed`: "**The web console is thread-first.** …" (one paragraph: sidebar of threads under the project, transcript, state-driven composer, inspector, design system with dark/light and motion, command palette and shortcuts, `GET /project`).
- Move: `.agents/notes/proposed/web-console-thread-first-shell.md` → `.agents/notes/implemented/`

- [ ] **Step 1: Delete the replaced files and fix every import** (`grep -rn "rail.js\|run.js\|new-run.js\|console.css" apps/web/src` must return nothing).
- [ ] **Step 2: Update `tests/thymira/test_web.py`**:

```python
def test_thread_header_only_offers_resume_for_a_resumable_event_state() -> None:
    """The console must not race the active dispatcher with a second resume request."""
    with _console(_RecordingApi()) as client:
        response = client.get("/static/js/views/thread.js")

    assert response.status_code == 200
    source = response.text
    assert "const resumable = isResumableState(state);" in source
    assert re.search(r'resumable\s+\?\s+button\("Resume"', source)
    assert 'active ? button("Resume"' not in source


def test_console_stylesheets_are_served_as_css() -> None:
    with _console(_RecordingApi()) as client:
        response = client.get("/static/css/tokens.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
```

- [ ] **Step 3: README and CHANGELOG** as above; move the note.
- [ ] **Step 4: Gate** — `just check` must be green; paste its tail in the report.
- [ ] **Step 5: Visual verification** (parent session, with the browser): home, thread, inspector panels, project page, settings, light/dark, collapsed sidebar, `Ctrl+K`, a narrow viewport (≈ 800 px).

---

## Self-review

- Spec coverage: §3 routes → Task 2 (`router.js`) + Task 4 (`main.js`); §4 shell → Tasks 3/4; §5 sidebar → Task 4; §6 home → Task 4; §7 thread/transcript/composer → Task 5 (+ fold in Task 2); §8 inspector → Task 5; §9 project → Task 4; §10 settings/connect/palette/toasts/shortcuts → Tasks 2/4; §11 markdown → Task 2; §12–13 design system/motion → Task 3; §14 API → Task 1; §15 layout → all; §16 tests → Tasks 1/2/6; §17 accessibility → each view's brief (aria attributes named in C2/C5).
- Placeholders: none; each step names concrete files, names and assertions.
- Type consistency: `href(route)` returns a string used by sidebar links and inspector artifact links; `foldThread(run, events)` returns the C4 shapes consumed by `Transcript.render`; `Composer.setState` kinds match `thread.js`'s computation; `Inspector.update(ctx)` receives the same `ctx` shape as today's tab renderers.
