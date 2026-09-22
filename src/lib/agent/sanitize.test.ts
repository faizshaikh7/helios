import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { omitRawTle } from "./sanitize.ts";

describe("model-facing satellite lookup", () => {
  it("removes raw TLE lines while retaining structured provenance", () => {
    const result = omitRawTle({
      norad_id: 25544,
      tle: { line1: "raw-1", line2: "raw-2" },
      epoch: { value: "2026-09-22T00:00:00Z", receipt: { dataset: { source: "celestrak" } } },
    });

    assert.deepEqual(result, {
      norad_id: 25544,
      epoch: { value: "2026-09-22T00:00:00Z", receipt: { dataset: { source: "celestrak" } } },
    });
  });

  it("leaves error results unchanged", () => {
    const error = { error: true, status: 502, detail: "catalog unavailable" };
    assert.equal(omitRawTle(error), error);
  });
});
