"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import {
  APPEARANCE,
  createAtmosphereMaterial,
  createCoronaMaterial,
  createRingMaterial,
  createSurfaceMaterial,
} from "@/lib/render/bodyMaterials";
import type { OrbitsResponse, SnapshotResponse } from "@/lib/types";

/**
 * How body radii are mapped to rendered size.
 *
 * `true` is honest and nearly unreadable; `legible` is readable and distorts relative sizes.
 * Both are offered, and which one is active is stated on screen, because every solar-system
 * picture ever printed makes this compromise silently and readers reasonably assume the sizes
 * mean something.
 */
export type SizeMode = "legible" | "true";

/** Radial mapping. Distances are real in both; `log` only compresses the radial axis. */
export type DistanceMode = "linear" | "log";

/** Scene units per AU in linear mode. */
const UNITS_PER_AU = 1.0;

/** Metres per AU, matching the service. */
const AU_M = 1.495978707e11;

/** The Sun's true radius in AU, the reference every other body is scaled against. */
const SUN_RADIUS_AU = 6.957e8 / AU_M;

/**
 * Compressive exponent for legible mode.
 *
 * The Sun's radius is about 16,000 times the Moon's. Raising the ratio to this power pulls that
 * down to roughly 5, which fits both in one view. Sizes after this are meaningless as ratios --
 * which is precisely what the on-screen caption says.
 */
const LEGIBLE_EXPONENT = 0.28;

const SUN_RADIUS_LINEAR = 0.09;
const SUN_RADIUS_LOG = 0.8;

/**
 * Map a body's radius to a rendered radius in scene units.
 *
 * The exaggeration is deliberately smaller in true-distance mode. Mercury orbits at 0.39 AU, so
 * a Sun drawn at the compressed view's size would visually engulf it -- the picture would assert
 * an overlap that does not exist, which is a worse lie than the size distortion it was meant to
 * fix.
 */
function renderedRadius(radiusM: number, sizeMode: SizeMode, distanceMode: DistanceMode): number {
  if (sizeMode === "true") return (radiusM / AU_M) * UNITS_PER_AU;

  const base = distanceMode === "linear" ? SUN_RADIUS_LINEAR : SUN_RADIUS_LOG;
  return base * Math.pow(radiusM / AU_M / SUN_RADIUS_AU, LEGIBLE_EXPONENT);
}

/**
 * Remap a heliocentric ecliptic point for display.
 *
 * Only the radial magnitude is ever changed. Direction — and therefore the whole angular
 * arrangement of the system, which is the part carrying real information — is preserved exactly
 * in both modes.
 */
function place(x: number, y: number, z: number, mode: DistanceMode): THREE.Vector3 {
  // Ecliptic z is "up" out of the orbital plane; three.js uses y for up, so the axes are
  // swapped here rather than in the service, which reports a real frame.
  const vector = new THREE.Vector3(x, z, y);
  const distance = vector.length();
  if (distance === 0) return vector;

  const scaled = mode === "linear" ? distance * UNITS_PER_AU : Math.log1p(distance) * 8.0;
  return vector.multiplyScalar(scaled / distance);
}

/**
 * Three.js view of the solar system.
 *
 * The second renderer, alongside Cesium: Cesium owns the Earth, where a geodetic datum and a
 * real ellipsoid matter, and this owns everything beyond it, where the interesting problem is
 * scale rather than geodesy.
 *
 * Positions, orbit paths, illumination direction, axial tilts and rotation rates are all real.
 * Surface appearance is procedural and is labelled as such — see `lib/render/bodyMaterials.ts`.
 */
export function SolarSystem({
  snapshot,
  orbits,
  sizeMode,
  distanceMode,
  focus,
}: {
  snapshot: SnapshotResponse | null;
  orbits: OrbitsResponse | null;
  sizeMode: SizeMode;
  distanceMode: DistanceMode;
  focus: string | null;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const bodyGroupRef = useRef<THREE.Group | null>(null);
  const orbitGroupRef = useRef<THREE.Group | null>(null);
  const animatedRef = useRef<
    { mesh: THREE.Object3D; material: THREE.ShaderMaterial; rotationRate: number }[]
  >([]);

  // Spherical camera state, with a damped target so zoom and focus changes glide.
  const camera = useRef({
    theta: 0.85,
    phi: 1.12,
    radius: 34,
    targetRadius: 34,
    centre: new THREE.Vector3(),
    targetCentre: new THREE.Vector3(),
  });

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    sceneRef.current = scene;

    const perspective = new THREE.PerspectiveCamera(
      42,
      mount.clientWidth / Math.max(mount.clientHeight, 1),
      0.0005,
      8000,
    );

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    // Filmic tone mapping keeps the Sun from clipping to a flat white disc while leaving the
    // dim outer planets visible — the dynamic range here is enormous.
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    mount.appendChild(renderer.domElement);

    const bodyGroup = new THREE.Group();
    const orbitGroup = new THREE.Group();
    scene.add(bodyGroup, orbitGroup);
    bodyGroupRef.current = bodyGroup;
    orbitGroupRef.current = orbitGroup;

    scene.add(buildStarfield());

    const clock = new THREE.Clock();
    let frame = 0;

    const render = () => {
      const state = camera.current;

      // Exponential damping toward the target, so wheel and focus changes read as motion
      // rather than as teleports.
      state.radius += (state.targetRadius - state.radius) * 0.12;
      state.centre.lerp(state.targetCentre, 0.12);

      perspective.position.set(
        state.centre.x + state.radius * Math.sin(state.phi) * Math.cos(state.theta),
        state.centre.y + state.radius * Math.cos(state.phi),
        state.centre.z + state.radius * Math.sin(state.phi) * Math.sin(state.theta),
      );
      perspective.lookAt(state.centre);

      const elapsed = clock.getElapsedTime();
      for (const item of animatedRef.current) {
        item.material.uniforms.uTime.value = elapsed;
        item.mesh.rotateY(item.rotationRate);
      }

      renderer.render(scene, perspective);
      frame = requestAnimationFrame(render);
    };
    render();

    let dragging = false;
    let lastX = 0;
    let lastY = 0;

    const onPointerDown = (event: PointerEvent) => {
      dragging = true;
      lastX = event.clientX;
      lastY = event.clientY;
    };
    const onPointerMove = (event: PointerEvent) => {
      if (!dragging) return;
      const state = camera.current;
      state.theta -= (event.clientX - lastX) * 0.005;
      state.phi = Math.min(
        Math.PI - 0.04,
        Math.max(0.04, state.phi - (event.clientY - lastY) * 0.005),
      );
      lastX = event.clientX;
      lastY = event.clientY;
    };
    const onPointerUp = () => {
      dragging = false;
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const state = camera.current;
      state.targetRadius = Math.min(
        600,
        Math.max(0.004, state.targetRadius * (1 + event.deltaY * 0.0015)),
      );
    };

    const element = renderer.domElement;
    element.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    element.addEventListener("wheel", onWheel, { passive: false });

    const onResize = () => {
      if (!mount.clientWidth) return;
      perspective.aspect = mount.clientWidth / Math.max(mount.clientHeight, 1);
      perspective.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(mount);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      element.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      element.removeEventListener("wheel", onWheel);
      renderer.dispose();
      if (element.parentNode === mount) mount.removeChild(element);
    };
  }, []);

  // Orbit paths. Rebuilt only when the paths or the radial mapping change — not on every date
  // step, since an orbit is the same curve whichever point of it a planet currently occupies.
  useEffect(() => {
    const group = orbitGroupRef.current;
    if (!group || !orbits) return;

    disposeChildren(group);

    for (const [body, path] of Object.entries(orbits.orbits)) {
      const points = path.map((point) =>
        place(point.x_au, point.y_au, point.z_au, distanceMode),
      );
      // Closed: the last sample is one step short of a full revolution.
      points.push(points[0].clone());

      const appearance = APPEARANCE[body];
      group.add(
        new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(points),
          new THREE.LineBasicMaterial({
            color: new THREE.Color(appearance?.colours[1] ?? "#8899bb"),
            transparent: true,
            opacity: 0.28,
          }),
        ),
      );
    }
  }, [orbits, distanceMode]);

  // Bodies.
  useEffect(() => {
    const group = bodyGroupRef.current;
    if (!group || !snapshot) return;

    disposeChildren(group);
    animatedRef.current = [];

    snapshot.bodies.forEach((body, index) => {
      const appearance = APPEARANCE[body.body];
      if (!appearance) return;

      const position = place(
        body.ecliptic_x_au,
        body.ecliptic_y_au,
        body.ecliptic_z_au,
        distanceMode,
      );
      const radius = Math.max(renderedRadius(body.radius_m, sizeMode, distanceMode), 1e-8);

      // A pivot carries the body's real axial tilt, so the spin axis leans correctly — Uranus
      // is on its side, Venus is upside down, and both are visible facts rather than trivia.
      const pivot = new THREE.Group();
      pivot.position.copy(position);
      pivot.rotation.z = THREE.MathUtils.degToRad(appearance.tiltDeg);
      group.add(pivot);

      const surface = createSurfaceMaterial(body.body, index * 13.37);
      // Sunlight direction at this body: from the Sun, which sits at the origin.
      const toSun = position.clone().negate().normalize();
      if (position.lengthSq() === 0) toSun.set(1, 0, 0);
      surface.uniforms.uLightDir.value.copy(toSun);

      const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 64, 48), surface);
      pivot.add(mesh);

      // Rotation rate scaled to something watchable. Direction and *relative* speed are real:
      // Jupiter visibly outruns Venus, and Venus turns backwards.
      const rotationRate = (2 * Math.PI) / (appearance.rotationHours * 60);
      animatedRef.current.push({ mesh, material: surface, rotationRate });

      if (body.body === "sun") {
        const corona = createCoronaMaterial();
        const coronaMesh = new THREE.Mesh(
          new THREE.SphereGeometry(radius * 1.9, 48, 32),
          corona,
        );
        pivot.add(coronaMesh);
        animatedRef.current.push({ mesh: coronaMesh, material: corona, rotationRate: 0 });

        // A real light source, so the shaded bodies and the Sun agree about where light is.
        const light = new THREE.PointLight(0xfff0d0, 3.0, 0, 0);
        pivot.add(light);
      }

      if (appearance.atmosphere) {
        const shell = createAtmosphereMaterial(
          appearance.atmosphere.colour,
          appearance.atmosphere.intensity,
        );
        shell.uniforms.uLightDir.value.copy(toSun);
        pivot.add(new THREE.Mesh(new THREE.SphereGeometry(radius * 1.055, 48, 32), shell));
      }

      if (appearance.rings) {
        const inner = radius * appearance.rings.inner;
        const outer = radius * appearance.rings.outer;
        const ringMaterial = createRingMaterial(appearance.rings.colours, inner, outer);
        ringMaterial.uniforms.uLightDir.value.copy(toSun);

        const ring = new THREE.Mesh(
          new THREE.RingGeometry(inner, outer, 192, 1),
          ringMaterial,
        );
        // RingGeometry is built in the xy-plane; the rings lie in the body's equatorial plane,
        // which the pivot's tilt then carries.
        ring.rotation.x = Math.PI / 2;
        pivot.add(ring);
      }
    });
  }, [snapshot, sizeMode, distanceMode]);

  // Focus: glide the camera to a body and zoom to a sensible distance for its size.
  useEffect(() => {
    const state = camera.current;

    if (!focus || !snapshot) {
      state.targetCentre.set(0, 0, 0);
      state.targetRadius = distanceMode === "linear" ? 62 : 34;
      return;
    }

    const body = snapshot.bodies.find((item) => item.body === focus);
    if (!body) return;

    state.targetCentre.copy(
      place(body.ecliptic_x_au, body.ecliptic_y_au, body.ecliptic_z_au, distanceMode),
    );
    state.targetRadius = Math.max(
      renderedRadius(body.radius_m, sizeMode, distanceMode) * 7.5,
      0.006,
    );
  }, [focus, snapshot, sizeMode, distanceMode]);

  return (
    <div
      ref={mountRef}
      className="h-[520px] w-full cursor-grab touch-none overflow-hidden rounded-lg bg-[#03050b] active:cursor-grabbing"
      role="img"
      aria-label="Three-dimensional view of the solar system at the selected instant"
    />
  );
}

/** Remove and dispose every child of a group, so date stepping does not leak GPU memory. */
function disposeChildren(group: THREE.Group): void {
  for (const child of [...group.children]) {
    group.remove(child);
    child.traverse((node) => {
      if (node instanceof THREE.Mesh || node instanceof THREE.Line) {
        node.geometry.dispose();
        const material = node.material;
        if (Array.isArray(material)) material.forEach((item) => item.dispose());
        else material.dispose();
      }
    });
  }
}

/**
 * Decorative starfield.
 *
 * Explicitly not a star catalogue: these are not real stars at real positions, which the panel
 * states. Magnitudes and colours are varied only so it does not read as uniform noise — a sky
 * of identical dots looks more artificial than one with structure.
 */
function buildStarfield(): THREE.Points {
  const count = 4200;
  const positions = new Float32Array(count * 3);
  const colours = new Float32Array(count * 3);
  const sizes = new Float32Array(count);

  const colour = new THREE.Color();

  for (let index = 0; index < count; index += 1) {
    // A Fibonacci sphere, so the distribution is even and deterministic — no reshuffling
    // between mounts, and no clustering at the poles.
    const y = 1 - (2 * (index + 0.5)) / count;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const angle = index * 2.399963229728653;

    const distance = 2200;
    positions[index * 3] = Math.cos(angle) * radius * distance;
    positions[index * 3 + 1] = y * distance;
    positions[index * 3 + 2] = Math.sin(angle) * radius * distance;

    // Deterministic pseudo-random brightness and hue from the index.
    const jitter = Math.abs(Math.sin(index * 12.9898) * 43758.5453) % 1;
    const brightness = 0.35 + Math.pow(jitter, 2.4) * 0.65;
    colour.setHSL(0.55 + (jitter - 0.5) * 0.14, 0.28, brightness);

    colours[index * 3] = colour.r;
    colours[index * 3 + 1] = colour.g;
    colours[index * 3 + 2] = colour.b;
    sizes[index] = 0.8 + Math.pow(jitter, 6) * 3.4;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colours, 3));
  geometry.setAttribute("size", new THREE.BufferAttribute(sizes, 1));

  const material = new THREE.PointsMaterial({
    vertexColors: true,
    size: 2.0,
    sizeAttenuation: false,
    transparent: true,
    opacity: 0.9,
    depthWrite: false,
  });

  return new THREE.Points(geometry, material);
}
