## Problem

The web console presents a Run as a record with eleven tabs and a status-filtered rail. An
operator waiting on a risk-interview question or a tool review has to find the right tab, and the
work THY and MIRA did is only visible as tables of events. The console looks like an admin
screen, not like the thread-first workspaces operators already use (Claude Code, Codex, Cursor),
and it has no design system, no motion and no keyboard model.

## Decision

Restructure the console as a three-column IDE shell: a sidebar of threads under the project, a
transcript folded from the Run's event log with one state-driven composer, and an inspector that
hosts the existing evidence renderers as panels. Add a pure `foldThread` over the redacted event
projections, a safe DOM-only markdown renderer, a token-based dark/light design system with a
motion spec, a command palette and shortcuts, and one read-only `GET /project` route so the
sidebar can name the project. Everything stays vanilla ES modules under the same CSP; the console
still holds no Run state and adds no authority (`docs/superpowers/specs/2026-09-15-web-console-thread-first-design.md`).

## Alternatives considered

- **Restyle the existing tabs in place.** Rejected: the operator's next action would still live
  behind a tab; the goal is a transcript and a composer, which is a different information
  architecture, not a theme.
- **Adopt a framework and a bundler (React/Vite).** Rejected: the console ships inside the wheel
  with no build step and a `default-src 'none'` CSP; a toolchain adds a supply chain and a build
  to a client that must stay trivially auditable.
- **Render assistant text with a markdown library and `innerHTML`.** Rejected: Run data reaches
  the page as text nodes by rule; a DOM-only renderer keeps that invariant and needs no third
  party.
- **Poll the run list on a timer to keep the sidebar live.** Rejected again: listing verifies
  every Run's log; the open thread updates its own row through `upsert`.
- **Derive the project name from the context document.** Rejected: `config.yaml` already
  declares it; one read-only route is cheaper and exact, and the console degrades to "Workspace"
  on an older API.

## Consequences

The console gains a transcript, a composer, an inspector, a design system, motion and a keyboard
model; `rail.js`, `run.js`, `new-run.js` and `console.css` are replaced. The evidence renderers
under `views/tabs/` are reused unchanged in contract, so nothing an operator could see before is
lost. Tests gain node-run unit tests for the pure folds and static checks that every module
import resolves. The API gains one read-only route documented in the contract.
