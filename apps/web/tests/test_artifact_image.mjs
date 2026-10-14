import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import { test } from "node:test";
import { artifactImageSource } from "../src/thymira/web/static/js/artifact-image.js";

const raw = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9f8AAAAASUVORK5CYII=", "base64");
const result = () => ({ run_id: "run_plot", encoding: "base64", content: raw.toString("base64"),
  artifact: { id: "artifact_plot", valid: true, name: "plot.png", size_bytes: raw.length, sha256: createHash("sha256").update(raw).digest("hex") } });

test("verified raster bytes can be embedded without trusting a declared MIME type", async () => {
  const value = result();
  value.artifact.media_type = null;
  assert.equal(await artifactImageSource(value, "run_plot", "artifact_plot"), `data:image/png;base64,${value.content}`);
});

test("withheld, redacted, superseded and cross-run content cannot become a plot", async () => {
  for (const change of [{ content: null }, { redacted: true }, { run_id: "run_other" }, { encoding: "utf-8" }]) {
    assert.equal(await artifactImageSource({ ...result(), ...change }, "run_plot", "artifact_plot"), null);
  }
  const invalid = result();
  invalid.artifact.valid = false;
  assert.equal(await artifactImageSource(invalid, "run_plot", "artifact_plot"), null);
});

test("a declared image MIME cannot make executable markup a preview", async () => {
  const value = result();
  const markup = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>');
  value.content = markup.toString("base64");
  value.artifact.size_bytes = markup.length;
  value.artifact.media_type = "image/png";
  value.artifact.sha256 = createHash("sha256").update(markup).digest("hex");
  assert.equal(await artifactImageSource(value, "run_plot", "artifact_plot"), null);
});

test("digest mismatch and malformed base64 stay unrendered", async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: webcrypto });
  try {
    const value = result();
    value.artifact.sha256 = "0".repeat(64);
    assert.equal(await artifactImageSource(value, "run_plot", "artifact_plot"), null);
    assert.equal(await artifactImageSource({ ...result(), content: "invalid%%" }, "run_plot", "artifact_plot"), null);
  } finally {
    if (previous) Object.defineProperty(globalThis, "crypto", previous);
    else delete globalThis.crypto;
  }
});
