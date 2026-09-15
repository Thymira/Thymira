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
