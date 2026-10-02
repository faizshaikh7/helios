import assert from "node:assert/strict";
import { describe, it } from "node:test";
import * as THREE from "three";
import { anchorOrbitPath } from "./orbitPath.ts";

describe("rendered orbit anchoring", () => {
  it("closes an unanchored sampled path", () => {
    const points = [
      new THREE.Vector3(1, 0, 0),
      new THREE.Vector3(0, 0, 1),
      new THREE.Vector3(-1, 0, 0),
    ];

    const closed = anchorOrbitPath(points);

    assert.equal(closed.length, points.length + 1);
    assert.deepEqual(closed.at(-1)?.toArray(), points[0].toArray());
  });

  it("makes the live ephemeris position an exact path vertex", () => {
    const points = [
      new THREE.Vector3(1, 0, 0),
      new THREE.Vector3(0, 0, 1),
      new THREE.Vector3(-1, 0, 0),
      new THREE.Vector3(0, 0, -1),
    ];
    const anchor = new THREE.Vector3(0.45, 0.02, 0.56);

    const anchored = anchorOrbitPath(points, anchor);

    assert.equal(anchored.length, points.length + 2);
    assert.ok(anchored.some((point) => point.equals(anchor)));
    assert.notEqual(anchored.find((point) => point.equals(anchor)), anchor);
    assert.deepEqual(anchored.at(-1)?.toArray(), points[0].toArray());
  });
});
