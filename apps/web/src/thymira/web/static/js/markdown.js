// A deliberately small markdown reader for model text and agent summaries.
//
// `parseMarkdown` is a pure line-based parser: it never touches the DOM and is unit-tested with
// node. `renderMarkdown` turns its blocks into elements with h()/text nodes only. Nothing here
// parses HTML: angle brackets, entities and raw tags stay literal text, and a link is built only
// for an http(s) URL, so Run data can never introduce markup or a javascript: target.

import { h } from "./dom.js";

const HEADING_RE = /^(#{1,3})\s+(.*)$/;
const FENCE_OPEN_RE = /^```(\S*)\s*$/;
const FENCE_CLOSE_RE = /^```\s*$/;
const BULLET_RE = /^[-*]\s+(.*)$/;
const ORDERED_RE = /^\d+[.)]\s+(.*)$/;
const QUOTE_RE = /^>\s?(.*)$/;
const LINK_RE = /^\[([^\]\n]*)\]\(([^)\s]*)\)/;
const INDENT_RE = /^\s+\S/;

const isSafeHref = (href) => /^https?:\/\//i.test(href);

function text(value) {
  return { type: "text", text: value };
}

// Text runs are merged so that a paragraph is a short, stable list of inlines.
function pushText(inlines, value) {
  if (!value) return;
  const last = inlines[inlines.length - 1];
  if (last && last.type === "text") last.text += value;
  else inlines.push(text(value));
}

function closingIndex(source, from, marker) {
  const at = source.indexOf(marker, from);
  return at === from ? -1 : at;
}

export function parseInlines(source) {
  const inlines = [];
  const line = String(source ?? "");
  let index = 0;
  while (index < line.length) {
    const character = line[index];
    if (character === "`") {
      const end = closingIndex(line, index + 1, "`");
      if (end > 0) {
        inlines.push({ type: "code", text: line.slice(index + 1, end) });
        index = end + 1;
        continue;
      }
    } else if (character === "*" && line.startsWith("**", index)) {
      const end = closingIndex(line, index + 2, "**");
      if (end > 0) {
        inlines.push({ type: "strong", inlines: parseInlines(line.slice(index + 2, end)) });
        index = end + 2;
        continue;
      }
    } else if (character === "*" || character === "_") {
      const end = closingIndex(line, index + 1, character);
      if (end > 0) {
        inlines.push({ type: "em", inlines: parseInlines(line.slice(index + 1, end)) });
        index = end + 1;
        continue;
      }
    } else if (character === "[") {
      const match = LINK_RE.exec(line.slice(index));
      if (match) {
        if (isSafeHref(match[2])) inlines.push({ type: "link", href: match[2], inlines: parseInlines(match[1]) });
        else pushText(inlines, match[0]);
        index += match[0].length;
        continue;
      }
    }
    pushText(inlines, character);
    index += 1;
  }
  return inlines;
}

export function parseMarkdown(source) {
  const lines = String(source ?? "").replaceAll("\r\n", "\n").split("\n");
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = FENCE_OPEN_RE.exec(line);
    if (fence) {
      const body = [];
      index += 1;
      while (index < lines.length && !FENCE_CLOSE_RE.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      // An unterminated fence runs to the end of the text rather than swallowing the parse.
      if (index < lines.length) index += 1;
      blocks.push({ type: "code", lang: fence[1] ?? "", text: body.join("\n") });
      continue;
    }
    const heading = HEADING_RE.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, inlines: parseInlines(heading[2]) });
      index += 1;
      continue;
    }
    if (QUOTE_RE.test(line)) {
      const parts = [];
      while (index < lines.length && QUOTE_RE.test(lines[index])) {
        parts.push(QUOTE_RE.exec(lines[index])[1]);
        index += 1;
      }
      blocks.push({ type: "quote", inlines: parseInlines(parts.join(" ").trim()) });
      continue;
    }
    const ordered = ORDERED_RE.test(line);
    if (ordered || BULLET_RE.test(line)) {
      const matcher = ordered ? ORDERED_RE : BULLET_RE;
      const items = [];
      while (index < lines.length) {
        const match = matcher.exec(lines[index]);
        if (match) {
          items.push([match[1]]);
          index += 1;
          continue;
        }
        // An indented continuation line belongs to the item above it.
        if (items.length && INDENT_RE.test(lines[index])) {
          items[items.length - 1].push(lines[index].trim());
          index += 1;
          continue;
        }
        break;
      }
      blocks.push({ type: "list", ordered, items: items.map((parts) => parseInlines(parts.join(" ").trim())) });
      continue;
    }
    const paragraph = [];
    while (index < lines.length) {
      const next = lines[index];
      if (!next.trim() || FENCE_OPEN_RE.test(next) || HEADING_RE.test(next) || QUOTE_RE.test(next)) break;
      if (BULLET_RE.test(next) || ORDERED_RE.test(next)) break;
      paragraph.push(next.trim());
      index += 1;
    }
    blocks.push({ type: "paragraph", inlines: parseInlines(paragraph.join(" ")) });
  }
  return blocks;
}

function renderInlines(inlines) {
  return inlines.map((inline) => {
    if (inline.type === "code") return h("code", { class: "prose-code" }, inline.text);
    if (inline.type === "strong") return h("strong", null, renderInlines(inline.inlines));
    if (inline.type === "em") return h("em", null, renderInlines(inline.inlines));
    if (inline.type === "link") {
      return h(
        "a",
        { href: inline.href, rel: "noopener noreferrer", target: "_blank" },
        renderInlines(inline.inlines),
      );
    }
    return document.createTextNode(inline.text);
  });
}

function copyButton(value) {
  const copy = h("button", { class: "copy", type: "button", title: "Copy this block" }, "Copy");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(value);
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Select it";
    }
    setTimeout(() => {
      copy.textContent = "Copy";
    }, 1400);
  });
  return copy;
}

function renderBlock(block) {
  if (block.type === "heading") return h(`h${block.level}`, null, renderInlines(block.inlines));
  if (block.type === "quote") return h("blockquote", null, renderInlines(block.inlines));
  if (block.type === "list") {
    return h(
      block.ordered ? "ol" : "ul",
      null,
      block.items.map((item) => h("li", null, renderInlines(item))),
    );
  }
  if (block.type === "code") {
    return h(
      "div",
      { class: "code-block" },
      h(
        "div",
        { class: "code-block-head" },
        h("span", { class: "code-lang" }, block.lang || "text"),
        copyButton(block.text),
      ),
      h("pre", { class: "code" }, h("code", null, block.text)),
    );
  }
  return h("p", null, renderInlines(block.inlines));
}

export function renderMarkdown(source) {
  const fragment = document.createDocumentFragment();
  for (const block of parseMarkdown(source)) fragment.append(renderBlock(block));
  return fragment;
}
