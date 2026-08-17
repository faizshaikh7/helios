import * as THREE from "three";
import type { AsteroidPosition, Star } from "@/lib/types";

/**
 * Point clouds for the catalogued stars and asteroids.
 *
 * These replace the two remaining invented things in the solar-system view: a decorative
 * starfield and no asteroids at all. Both are now real catalogues — the Yale Bright Star
 * Catalogue and JPL's Small-Body Database — so the constellations are the actual constellations
 * and the Kirkwood gaps appear because they are in the data.
 */

/** Obliquity of the ecliptic at J2000, degrees. */
const OBLIQUITY_DEG = 23.4392911;

/**
 * Convert a J2000 equatorial direction to the scene's ecliptic axes.
 *
 * Star catalogues are equatorial; the scene is ecliptic, because that is the plane the planets
 * orbit in. Skipping this rotation is the same 23-degree error that once threw every planet off
 * its own orbit — it would leave the constellations tilted against the ecliptic.
 *
 * @param raDeg - Right ascension, degrees.
 * @param decDeg - Declination, degrees.
 * @returns A unit vector in three.js axes, where y is out of the ecliptic plane.
 */
function equatorialToSceneDirection(raDeg: number, decDeg: number): THREE.Vector3 {
  const ra = THREE.MathUtils.degToRad(raDeg);
  const dec = THREE.MathUtils.degToRad(decDeg);
  const obliquity = THREE.MathUtils.degToRad(OBLIQUITY_DEG);

  const xEquatorial = Math.cos(dec) * Math.cos(ra);
  const yEquatorial = Math.cos(dec) * Math.sin(ra);
  const zEquatorial = Math.sin(dec);

  const yEcliptic = yEquatorial * Math.cos(obliquity) + zEquatorial * Math.sin(obliquity);
  const zEcliptic = -yEquatorial * Math.sin(obliquity) + zEquatorial * Math.cos(obliquity);

  // Scene axes: ecliptic z is up, which three.js calls y.
  return new THREE.Vector3(xEquatorial, zEcliptic, yEcliptic);
}

/**
 * Approximate a star's colour from its B−V colour index.
 *
 * A rough fit, not a spectrum: hot blue stars sit near B−V = −0.3, the Sun at 0.65, and cool red
 * ones beyond 1.5. The point is that Betelgeuse looks red and Rigel looks blue, which is a real
 * property of the catalogue rather than decoration.
 *
 * @param bv - Colour index, or null when the catalogue does not give one.
 * @returns A display colour.
 */
function colourFromIndex(bv: number | null): THREE.Color {
  // Without an index, white is the honest default: it asserts no temperature.
  if (bv === null) return new THREE.Color(0.95, 0.95, 0.95);

  const clamped = Math.max(-0.4, Math.min(2.0, bv));
  const warmth = (clamped + 0.4) / 2.4;

  return new THREE.Color(
    0.62 + warmth * 0.38,
    0.75 + warmth * 0.12 - warmth * warmth * 0.22,
    1.0 - warmth * 0.62,
  );
}

/**
 * Magnitude bands.
 *
 * Point size has to vary with brightness or the sky reads as uniform confetti, and a single
 * `PointsMaterial` carries one size for all its points. Splitting the catalogue into a few bands,
 * each its own draw call, gets that variation from three.js's own materials.
 *
 * The alternative was a custom shader with a per-vertex size attribute, which is what this
 * originally did -- and it silently rendered nothing at all. Built-in materials are patched by
 * three.js for whatever the renderer is configured with (here, a logarithmic depth buffer);
 * a hand-written one has to reproduce all of that correctly or it fails invisibly. Three draw
 * calls is a small price for not owning that.
 */
const MAGNITUDE_BANDS = [
  { brighterThan: -2.0, fainterThan: 1.5, size: 4.2, opacity: 1.0 },
  { brighterThan: 1.5, fainterThan: 3.0, size: 2.8, opacity: 0.95 },
  { brighterThan: 3.0, fainterThan: 4.5, size: 1.9, opacity: 0.85 },
  { brighterThan: 4.5, fainterThan: 99.0, size: 1.3, opacity: 0.7 },
] as const;

/**
 * Build the star field from a real catalogue.
 *
 * Stars are placed on a unit sphere and the whole group is scaled and moved with the camera, so
 * it behaves as a sky at infinity rather than as objects at a fixed distance.
 *
 * @param stars - Catalogue records.
 * @returns A group of point clouds, one per magnitude band.
 */
export function buildStars(stars: Star[]): THREE.Group {
  const group = new THREE.Group();

  for (const band of MAGNITUDE_BANDS) {
    const members = stars.filter(
      (star) => star.vmag >= band.brighterThan && star.vmag < band.fainterThan,
    );
    if (members.length === 0) continue;

    const positions = new Float32Array(members.length * 3);
    const colours = new Float32Array(members.length * 3);

    members.forEach((star, index) => {
      const direction = equatorialToSceneDirection(star.ra_deg, star.dec_deg);
      positions[index * 3] = direction.x;
      positions[index * 3 + 1] = direction.y;
      positions[index * 3 + 2] = direction.z;

      // Brightness within the band, so a magnitude 5.9 star is not as bright as a 4.6 one.
      // Five magnitudes is a hundredfold in received light; the exponent is gentler than that
      // because a screen cannot show the real range and the faint majority would vanish.
      const brightness = Math.min(1, Math.pow(2.512, -(star.vmag - band.brighterThan) * 0.32));

      const colour = colourFromIndex(star.bv).multiplyScalar(0.45 + brightness * 0.55);
      colours[index * 3] = colour.r;
      colours[index * 3 + 1] = colour.g;
      colours[index * 3 + 2] = colour.b;
    });

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));

    group.add(
      new THREE.Points(
        geometry,
        new THREE.PointsMaterial({
          vertexColors: true,
          size: band.size,
          sizeAttenuation: false,
          transparent: true,
          opacity: band.opacity,
          depthWrite: false,
        }),
      ),
    );
  }

  return group;
}

/**
 * Build the asteroid cloud.
 *
 * One point per catalogued object at its computed position — not a scattered ring. The belt's
 * real structure, including the resonance gaps, is a consequence of the data rather than of
 * anything drawn.
 *
 * @param asteroids - Positions in heliocentric ecliptic AU.
 * @param scale - Maps an AU distance to scene units, matching the planets' mapping.
 * @returns Points ready to add to the scene.
 */
export function buildAsteroids(
  asteroids: AsteroidPosition[],
  scale: (x: number, y: number, z: number) => THREE.Vector3,
): THREE.Points {
  const positions = new Float32Array(asteroids.length * 3);
  const colours = new Float32Array(asteroids.length * 3);

  const colour = new THREE.Color();

  asteroids.forEach((asteroid, index) => {
    const placed = scale(asteroid.x_au, asteroid.y_au, asteroid.z_au);
    positions[index * 3] = placed.x;
    positions[index * 3 + 1] = placed.y;
    positions[index * 3 + 2] = placed.z;

    // Warmer for the inner belt, cooler further out, so the structure is legible without
    // implying a composition the data does not carry.
    const warmth = Math.max(0, Math.min(1, (asteroid.semi_major_axis_au - 2.0) / 2.0));
    colour.setRGB(0.78 - warmth * 0.22, 0.7 - warmth * 0.1, 0.58 + warmth * 0.22);

    colours[index * 3] = colour.r;
    colours[index * 3 + 1] = colour.g;
    colours[index * 3 + 2] = colour.b;
  });

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));

  return new THREE.Points(
    geometry,
    new THREE.PointsMaterial({
      vertexColors: true,
      size: 2.0,
      sizeAttenuation: false,
      transparent: true,
      opacity: 0.75,
      depthWrite: false,
    }),
  );
}
