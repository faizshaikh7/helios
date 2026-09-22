import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { webGlAvailable } from "./webgl.ts";

describe("WebGL capability detection", () => {
  it("accepts a WebGL 2 context", () => {
    assert.equal(webGlAvailable(() => ({ getContext: () => ({}) })), true);
  });

  it("falls back to WebGL 1", () => {
    assert.equal(
      webGlAvailable(() => ({
        getContext: (kind) => (kind === "webgl" ? {} : null),
      })),
      true,
    );
  });

  it("reports unavailable when context creation fails", () => {
    assert.equal(webGlAvailable(() => ({ getContext: () => null })), false);
    assert.equal(
      webGlAvailable(() => {
        throw new Error("blocked by browser policy");
      }),
      false,
    );
  });
});
