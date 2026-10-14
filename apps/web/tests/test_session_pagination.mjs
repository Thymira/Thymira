import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { listAllSessions } from "../src/thymira/web/static/js/api.js";

const originals = {};

function installBrowser(fetchImpl) {
  for (const key of ["window", "sessionStorage", "fetch"]) originals[key] = globalThis[key];
  globalThis.window = {
    location: { origin: "http://localhost:8080" },
    dispatchEvent() {},
  };
  globalThis.sessionStorage = {
    getItem: () => null,
    setItem() {},
    removeItem() {},
  };
  globalThis.fetch = fetchImpl;
}

afterEach(() => {
  for (const [key, value] of Object.entries(originals)) {
    if (value === undefined) delete globalThis[key];
    else globalThis[key] = value;
  }
});

test("session traversal reaches pages beyond the former 1000-session cap", async () => {
  const pageSize = 50;
  const total = 1001;
  const calls = [];
  installBrowser(async (input) => {
    const url = new URL(input);
    calls.push(url.searchParams.get("cursor"));
    const page = calls.length - 1;
    const start = page * pageSize;
    const items = Array.from({ length: Math.min(pageSize, total - start) }, (_, index) => ({
      id: `session-${start + index}`,
    }));
    const nextCursor = start + items.length < total ? `cursor-${page + 1}` : null;
    return new Response(JSON.stringify({ items, next_cursor: nextCursor }), { status: 200 });
  });

  const sessions = await listAllSessions();

  assert.equal(sessions.length, total);
  assert.equal(calls.length, 21);
});

test("session traversal fails explicitly when the API repeats a cursor", async () => {
  let calls = 0;
  installBrowser(async () => {
    calls += 1;
    return new Response(JSON.stringify({ items: [], next_cursor: "stuck" }), { status: 200 });
  });

  await assert.rejects(listAllSessions(), /repeated cursor/i);
  assert.equal(calls, 2);
});
