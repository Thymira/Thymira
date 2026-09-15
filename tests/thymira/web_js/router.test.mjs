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
