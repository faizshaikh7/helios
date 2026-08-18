"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import type { SnapshotResponse } from "@/lib/types";

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

/**
 * Compressive exponent for legible mode.
 *
 * The Sun's radius is about 16,000 times the Moon's. Raising the ratio to this power pulls that
 * down to roughly 5, which fits both in one view. Sizes after this are meaningless as ratios --
 * which is precisely what the on-screen caption says.
 */
const LEGIBLE_EXPONENT = 0.28;

/** The Sun's radius in the true-distance view. */
const SUN_RADIUS_LINEAR = 0.1;

/** The Sun's radius in the compressed-distance view, where there is more room. */
const SUN_RADIUS_LOG = 0.85;

/** The Sun's true radius in AU, the reference every other body is scaled against. */
const SUN_RADIUS_AU = 6.957e8 / AU_M;

/**
 * Map a body's radius to a rendered radius in scene units.
 *
 * The exaggeration is deliberately smaller in true-distance mode. Mercury orbits at 0.39 AU, so
 * a Sun drawn at the compressed view's size would visually engulf it -- the picture would assert
 * an overlap that does not exist, which is a worse lie than the size distortion it was meant to
 * fix. The exaggeration is therefore kept below the innermost orbit in both modes.
 */
function renderedRadius(radiusM: number, sizeMode: SizeMode, distanceMode: DistanceMode): number {
  if (sizeMode === "true") return (radiusM / AU_M) * UNITS_PER_AU;

  const base = distanceMode === "linear" ? SUN_RADIUS_LINEAR : SUN_RADIUS_LOG;
  return base * Math.pow(radiusM / AU_M / SUN_RADIUS_AU, LEGIBLE_EXPONENT);
}

/** Map a barycentric distance in AU to a radial scene distance. */
function renderedDistance(au: number, mode: DistanceMode): number {
  if (mode === "linear") return au * UNITS_PER_AU;

  // log1p keeps the Sun at the origin rather than sending it to negative infinity, and pulls
  // Neptune (30 AU) and Mercury (0.39 AU) into the same view without either vanishing.
  return Math.log1p(au) * 8.0;
}

/**
 * Three.js view of the solar system at a given instant.
 *
 * The second renderer, alongside Cesium: Cesium owns the Earth, where a geodetic datum and a
 * real ellipsoid matter, and this owns everything beyond it, where the interesting problem is
 * scale rather than geodesy.
 *
 * Positions here are real — the same values the API returns, to the same measured accuracy.
 * Sizes are the thing that cannot be honest and legible at once, so the mode is a control rather
 * than a silent decision.
 */
export function SolarSystem({
  snapshot,
  sizeMode,
  distanceMode,
}: {
  snapshot: SnapshotResponse | null;
  sizeMode: SizeMode;
  distanceMode: DistanceMode;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const bodiesRef = useRef<THREE.Group | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);

  // Camera state as spherical coordinates, driven by pointer drag and wheel.
  const cameraState = useRef({ theta: 0.9, phi: 1.05, radius: 34 });

  // Set up the scene once. Rebuilding it per render would drop the user's camera position on
  // every date change, which makes the time control useless for watching motion.
  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    scene.background = null;
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(
      45,
      mount.clientWidth / Math.max(mount.clientHeight, 1),
      0.001,
      5000,
    );
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    // The Sun is the only light source, which is physically right and also renders the phase of
    // each body correctly — the lit hemisphere faces the Sun, as it does in life.
    scene.add(new THREE.PointLight(0xffffff, 3.5, 0, 0));
    scene.add(new THREE.AmbientLight(0xffffff, 0.12));

    const bodies = new THREE.Group();
    scene.add(bodies);
    bodiesRef.current = bodies;

    // A static starfield. Decorative, and labelled as such in the caption: these are not
    // catalogued stars at real positions, and pretending otherwise would be exactly the kind of
    // quiet fiction this project exists to avoid.
    const starGeometry = new THREE.BufferGeometry();
    const starCount = 1800;
    const positions = new Float32Array(starCount * 3);
    for (let index = 0; index < starCount; index += 1) {
      // Deterministic placement from the index, so the field does not reshuffle on every mount.
      const a = index * 2.399963;
      const z = 1 - (2 * (index + 0.5)) / starCount;
      const r = Math.sqrt(Math.max(0, 1 - z * z));
      positions[index * 3] = Math.cos(a) * r * 900;
      positions[index * 3 + 1] = Math.sin(a) * r * 900;
      positions[index * 3 + 2] = z * 900;
    }
    starGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    scene.add(
      new THREE.Points(
        starGeometry,
        new THREE.PointsMaterial({ color: 0x8899bb, size: 1.6, sizeAttenuation: false }),
      ),
    );

    let frame = 0;
    const render = () => {
      const state = cameraState.current;
      camera.position.set(
        state.radius * Math.sin(state.phi) * Math.cos(state.theta),
        state.radius * Math.cos(state.phi),
        state.radius * Math.sin(state.phi) * Math.sin(state.theta),
      );
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
      frame = requestAnimationFrame(render);
    };
    render();

    // Hand-rolled orbit control: drag to rotate, wheel to zoom. OrbitControls lives in three's
    // examples directory, which needs extra build configuration for one small behaviour.
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
      const state = cameraState.current;
      state.theta -= (event.clientX - lastX) * 0.005;
      state.phi = Math.min(Math.PI - 0.05, Math.max(0.05, state.phi - (event.clientY - lastY) * 0.005));
      lastX = event.clientX;
      lastY = event.clientY;
    };
    const onPointerUp = () => {
      dragging = false;
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const state = cameraState.current;
      state.radius = Math.min(400, Math.max(0.02, state.radius * (1 + event.deltaY * 0.0012)));
    };

    const element = renderer.domElement;
    element.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    element.addEventListener("wheel", onWheel, { passive: false });

    const onResize = () => {
      if (!mount.clientWidth) return;
      camera.aspect = mount.clientWidth / Math.max(mount.clientHeight, 1);
      camera.updateProjectionMatrix();
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
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, []);

  // Rebuild the bodies whenever the data or either scale mode changes.
  useEffect(() => {
    const group = bodiesRef.current;
    if (!group || !snapshot) return;

    // Dispose rather than just detach: without this, dragging the date slider leaks a geometry
    // and a material per body per frame.
    for (const child of [...group.children]) {
      group.remove(child);
      if (child instanceof THREE.Mesh || child instanceof THREE.Line) {
        child.geometry.dispose();
        const material = child.material;
        if (Array.isArray(material)) material.forEach((item) => item.dispose());
        else material.dispose();
      }
    }

    for (const body of snapshot.bodies) {
      const distanceAu = body.distance_from_barycentre_au;
      const scaled = renderedDistance(distanceAu, distanceMode);

      // Direction is preserved exactly; only the radial magnitude is remapped, so the angular
      // arrangement of the planets on screen is real in both distance modes.
      const direction =
        distanceAu > 0
          ? new THREE.Vector3(body.x_au, body.z_au, body.y_au).normalize()
          : new THREE.Vector3(0, 0, 0);

      const radius = Math.max(renderedRadius(body.radius_m, sizeMode, distanceMode), 1e-7);
      const geometry = new THREE.SphereGeometry(radius, 32, 24);

      const isSun = body.body === "sun";
      const material = isSun
        ? new THREE.MeshBasicMaterial({ color: body.colour })
        : new THREE.MeshStandardMaterial({ color: body.colour, roughness: 0.9, metalness: 0 });

      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(direction.multiplyScalar(scaled));
      mesh.name = body.body;
      group.add(mesh);

      // A ring at the body's *current* distance. Deliberately not an orbit path: the real orbit
      // is an ellipse and drawing a circle through one sampled point would assert a shape the
      // data does not contain. The caption says which it is.
      if (!isSun && distanceAu > 0) {
        const points: THREE.Vector3[] = [];
        for (let step = 0; step <= 128; step += 1) {
          const angle = (step / 128) * Math.PI * 2;
          points.push(new THREE.Vector3(Math.cos(angle) * scaled, 0, Math.sin(angle) * scaled));
        }
        group.add(
          new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(points),
            new THREE.LineBasicMaterial({ color: 0x445066, transparent: true, opacity: 0.45 }),
          ),
        );
      }
    }
  }, [snapshot, sizeMode, distanceMode]);

  return (
    <div
      ref={mountRef}
      className="h-[440px] w-full cursor-grab touch-none overflow-hidden rounded-lg border border-edge bg-[#05070d] active:cursor-grabbing"
      role="img"
      aria-label="Three-dimensional view of the solar system at the selected instant"
    />
  );
}
