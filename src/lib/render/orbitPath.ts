import * as THREE from "three";

/**
 * Close a sampled orbit and insert the live body position into its nearest segment.
 *
 * A sampled curve is rendered as straight chords. Even when the mathematical trajectory and
 * body agree, a point between samples can therefore appear slightly outside the visible line at
 * close zoom. The live ephemeris point is valid trajectory data, so making it an explicit vertex
 * removes the rendering artefact without moving or approximating the body.
 */
export function anchorOrbitPath(
  points: THREE.Vector3[],
  anchor?: THREE.Vector3,
): THREE.Vector3[] {
  if (points.length < 2) return points;

  const closed = [...points, points[0].clone()];
  if (!anchor) return closed;

  const segment = new THREE.Line3();
  const closest = new THREE.Vector3();
  let nearestIndex = 0;
  let nearestDistanceSq = Number.POSITIVE_INFINITY;

  for (let index = 0; index < closed.length - 1; index += 1) {
    segment.set(closed[index], closed[index + 1]);
    segment.closestPointToPoint(anchor, true, closest);
    const distanceSq = closest.distanceToSquared(anchor);
    if (distanceSq < nearestDistanceSq) {
      nearestDistanceSq = distanceSq;
      nearestIndex = index;
    }
  }

  return [
    ...closed.slice(0, nearestIndex + 1),
    anchor.clone(),
    ...closed.slice(nearestIndex + 1),
  ];
}
