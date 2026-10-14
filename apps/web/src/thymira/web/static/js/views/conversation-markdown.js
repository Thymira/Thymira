import { h } from "../dom.js";

const INLINE_TOKEN = /(`+)(.+?)\1|\[([^\]]+)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)|\*\*(.+?)\*\*|__([^_]+?)__|\*([^*\n]+?)\*|(?<![A-Za-z0-9])_([^_\n]+?)_(?![A-Za-z0-9])/g;
const HEADING = /^ {0,3}(#{1,6})\s+(.+)$/;
const UNORDERED_ITEM = /^\s{0,3}[-+*]\s+(.+)$/;
const ORDERED_ITEM = /^\s{0,3}\d+[.)]\s+(.+)$/;
const QUOTE = /^ {0,3}> ?(.*)$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})([^`]*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;

/** Render the safe Markdown subset used by assistant messages as DOM nodes. */
export function renderConversationMarkdown(markdown) {
  return h("div", { class: "conversation-output-markdown" }, ...renderBlocks(String(markdown ?? "")));
}

function renderBlocks(markdown) {
  const lines = markdown.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
  const nodes = [];
  let index = 0;

  while (index < lines.length) {
    if (!lines[index].trim()) {
      index += 1;
      continue;
    }

    const fence = lines[index].match(FENCE);
    if (fence) {
      const marker = fence[1];
      const code = [];
      index += 1;
      while (index < lines.length && !new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`).test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      nodes.push(h("pre", { class: "conversation-output-code" }, h("code", {}, code.join("\n"))));
      continue;
    }

    const heading = lines[index].match(HEADING);
    if (heading) {
      nodes.push(h(`h${heading[1].length}`, { class: "conversation-output-heading" }, ...renderInline(heading[2])));
      index += 1;
      continue;
    }

    if (isTable(lines, index)) {
      const rendered = renderTable(lines, index);
      nodes.push(rendered.node);
      index = rendered.nextIndex;
      continue;
    }

    if (isRule(lines[index])) {
      nodes.push(h("hr", { class: "conversation-output-rule" }));
      index += 1;
      continue;
    }

    if (QUOTE.test(lines[index])) {
      const quoted = [];
      while (index < lines.length) {
        const match = lines[index].match(QUOTE);
        if (!match) break;
        quoted.push(match[1]);
        index += 1;
      }
      nodes.push(h("blockquote", { class: "conversation-output-quote" }, ...renderBlocks(quoted.join("\n"))));
      continue;
    }

    if (UNORDERED_ITEM.test(lines[index]) || ORDERED_ITEM.test(lines[index])) {
      const ordered = ORDERED_ITEM.test(lines[index]);
      const matcher = ordered ? ORDERED_ITEM : UNORDERED_ITEM;
      const items = [];
      while (index < lines.length) {
        const item = lines[index].match(matcher);
        if (!item) break;
        items.push(h("li", {}, ...renderInline(item[1])));
        index += 1;

        let nextItem = index;
        while (nextItem < lines.length && !lines[nextItem].trim()) nextItem += 1;
        if (nextItem < lines.length && matcher.test(lines[nextItem])) index = nextItem;
        else break;
      }
      nodes.push(h(ordered ? "ol" : "ul", { class: "conversation-output-list" }, ...items));
      continue;
    }

    const paragraph = [lines[index]];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsBlock(lines, index)) {
      paragraph.push(lines[index]);
      index += 1;
    }
    nodes.push(h("p", { class: "conversation-output-prose" }, ...renderInline(paragraph.join(" "))));
  }

  return nodes;
}

function startsBlock(lines, index) {
  return FENCE.test(lines[index]) || HEADING.test(lines[index]) || isRule(lines[index]) ||
    QUOTE.test(lines[index]) || UNORDERED_ITEM.test(lines[index]) || ORDERED_ITEM.test(lines[index]) ||
    isTable(lines, index);
}

function renderInline(text) {
  const nodes = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE_TOKEN)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    const [raw, ticks, code, label, href, strongStars, strongUnderscores, emphasisStars, emphasisUnderscores] = match;
    if (ticks) {
      nodes.push(h("code", { class: "conversation-output-inline-code" }, code));
    } else if (label !== undefined) {
      const safeHref = markdownHref(href);
      nodes.push(safeHref
        ? h("a", {
          class: "conversation-output-link",
          href: safeHref,
          ...(safeHref.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {}),
        }, ...renderInline(label))
        : raw);
    } else if (strongStars !== undefined || strongUnderscores !== undefined) {
      nodes.push(h("strong", {}, ...renderInline(strongStars ?? strongUnderscores)));
    } else {
      nodes.push(h("em", {}, ...renderInline(emphasisStars ?? emphasisUnderscores)));
    }
    cursor = start + raw.length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function markdownHref(value) {
  if (!value || /[\u0000-\u0020\\]/.test(value)) return null;
  if (/^#\/(?:runs\/[^/?#]+\/artifacts(?:\/[^/?#]+)?|sessions\/[^/?#]+\/artifacts\/[^/?#]+)$/.test(value)) {
    return value;
  }
  if (!/^https?:\/\//i.test(value)) return null;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) && !parsed.username && !parsed.password
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

function isTable(lines, index) {
  return index + 1 < lines.length && lines[index].includes("|") && TABLE_SEPARATOR.test(lines[index + 1]);
}

function renderTable(lines, index) {
  const headers = tableCells(lines[index]);
  const rows = [];
  index += 2;
  while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
    rows.push(tableCells(lines[index]));
    index += 1;
  }
  return {
    node: h("div", { class: "conversation-output-table-scroll" },
      h("table", { class: "conversation-output-table" },
        h("thead", {}, h("tr", {}, ...headers.map((cell) => h("th", { scope: "col" }, ...renderInline(cell))))),
        h("tbody", {}, ...rows.map((row) => h("tr", {}, ...headers.map((_, column) =>
          h("td", {}, ...renderInline(row[column] ?? "")),
        )))),
      ),
    ),
    nextIndex: index,
  };
}

function tableCells(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isRule(line) {
  return /^ {0,3}(?:\*\s*){3,}$/.test(line) || /^ {0,3}(?:-\s*){3,}$/.test(line) || /^ {0,3}(?:_\s*){3,}$/.test(line);
}
