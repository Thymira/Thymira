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
