import { h } from "../dom.js";
import { renderConversationMarkdown } from "./conversation-markdown.js";

/** Present already released Markdown and structured task results without interpreting HTML. */
export function conversationOutput(text) {
  const nodes = [];
  let prose = [];
  const flushProse = () => {
    if (prose.length) nodes.push(renderConversationMarkdown(prose.join("\n\n")));
    prose = [];
  };

  for (const part of String(text ?? "").split(/\n\n+/)) {
    let value;
    try { value = JSON.parse(part); } catch { value = null; }
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      prose.push(part);
      continue;
    }
    flushProse();
    nodes.push(h("section", { class: "conversation-output-result", "aria-label": "Recorded task result" },
      h("dl", {}, ...Object.entries(value).flatMap(([key, item]) => [
        h("dt", {}, key.replaceAll("_", " ")),
        h("dd", {}, item !== null && typeof item === "object"
          ? h("pre", { class: "conversation-output-data" }, JSON.stringify(item, null, 2))
          : String(item ?? "—")),
      ]))));
  }
  flushProse();
  return h("div", { class: "conversation-output" }, ...nodes);
}
