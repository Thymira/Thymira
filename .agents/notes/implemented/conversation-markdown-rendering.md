# Render assistant Markdown as safe conversation nodes

## Problem

Released assistant messages contain Markdown, but the conversation view displayed paragraphs as
plain text. Artifact links were therefore not clickable, and parsing model text as HTML would
allow untrusted content to create active markup.

## Decision

Render the Markdown subset used by the conversation UI with DOM nodes and text nodes. Support
headings, paragraphs, lists, tables, quotes, code and links. Allow only existing in-app artifact
routes and absolute HTTP(S) links; keep unsupported or unsafe link syntax as text. Explain that
the terminal diagnostic button prepares a user-authored Ask draft and that an assistant
explanation appears only after the user sends it.

## Alternatives considered

- Keeping all output as plain text leaves Markdown markers and artifact routes unreadable.
- Assigning model output to `innerHTML` allows message content to create active markup.
- Adding a Markdown package expands the client dependency and trusted rendering surface for a
  small, known subset.

## Consequences

Conversation text keeps its structured JSON rendering and renders common Markdown without
interpreting HTML. The focused UI test checks formatting, artifact navigation and rejected active
content; the complete web UI suite confirms existing draft and refresh behavior remains intact.
Markdown syntax outside the supported subset stays readable as text and does not receive rich
formatting.
