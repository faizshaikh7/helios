"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import {
  APPEARANCE,
  createGlowMaterial,
  createRingMaterial,
  createSurfaceMaterial,
} from "@/lib/render/bodyMaterials";
import { buildAsteroids, buildStars } from "@/lib/render/skyObjects";
import type {
  AsteroidsResponse,
  MoonsResponse,
  OrbitsResponse,
  SnapshotResponse,
  StarsResponse,
} from "@/lib/types";

/** Radial mapping. Distances are real in both; `log` only compresses the radial axis. */
export type DistanceMode = "linear" | "log";

/** Scene units per AU. */
const UNITS_PER_AU = 1.0;

/** Metres per AU, matching the service. */
const AU_M = 1.495978707e11;

/**
 * Apparent diameter, in pixels, below which a body is drawn as a labelled marker instead of a
 * sphere.
 *
 * This is how the size problem is actually solved, and it removes the compromise the first
 * version agonised over. A solar-system view cannot show true relative sizes *and* stay legible
 * — so at system scale, don't draw sizes at all. Draw a marker where the body is, label it, and
 * render the real thing only once the camera is close enough that its true size covers more than
 * a few pixels. Nothing is ever exaggerated, and nothing is ever invisible.
 */
const MARKER_THRESHOLD_PX = 9;

/** A body's radius in scene units, always true to scale. */
function trueRadius(radiusM: number): number {
  return (radiusM / AU_M) * UNITS_PER_AU;
}

/**
 * Remap a heliocentric ecliptic point for display.
 *
 * Only the radial magnitude is ever changed. Direction — and therefore the entire angular
 * arrangement of the system, which is the part carrying real information — is preserved exactly
 * in both modes.
 */
function place(x: number, y: number, z: number, mode: DistanceMode): THREE.Vector3 {
  // Ecliptic z is "up" out of the orbital plane; three.js uses y for up, so the axes are swapped
  // here rather than in the service, which reports a real frame.
  const vector = new THREE.Vector3(x, z, y);
  const distance = vector.length();
  if (distance === 0) return vector;

  const scaled = mode === "linear" ? distance * UNITS_PER_AU : Math.log1p(distance) * 8.0;
  return vector.multiplyScalar(scaled / distance);
}

type BodyEntry = {
  name: string;
  pivot: THREE.Group;
  mesh: THREE.Mesh;
  shells: THREE.Object3D[];
  material: THREE.ShaderMaterial;
  extraMaterials: THREE.ShaderMaterial[];
  radius: number;
  rotationRate: number;
  label: HTMLDivElement;
  marker: HTMLDivElement;
};

/**
 * Three.js view of the solar system.
 *
 * The second renderer, alongside Cesium: Cesium owns the Earth, where a geodetic datum and a real
 * ellipsoid matter, and this owns everything beyond it.
 *
 * Everything geometric is real — positions, traced orbits, distances, illumination direction,
 * axial tilts, rotation directions, and now sizes too, since bodies are never scaled up. Surface
 * imagery is real observed data with attribution. The only liberties are the radial compression
 * option, which is labelled, and rotation speed, which is sped up to be watchable.
 */
export function SolarSystem({
  snapshot,
  orbits,
  starCatalogue,
  moons,
  asteroids,
  distanceMode,
  focus,
  onSelect,
}: {
  snapshot: SnapshotResponse | null;
  orbits: OrbitsResponse | null;
  starCatalogue: StarsResponse | null;
  moons: MoonsResponse | null;
  asteroids: AsteroidsResponse | null;
  distanceMode: DistanceMode;
  focus: string | null;
  onSelect: (body: string) => void;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const bodyGroupRef = useRef<THREE.Group | null>(null);
  const orbitGroupRef = useRef<THREE.Group | null>(null);
  const entriesRef = useRef<BodyEntry[]>([]);
  const starsRef = useRef<THREE.Group | null>(null);
  const asteroidsRef = useRef<THREE.Points | null>(null);
  const moonGroupRef = useRef<THREE.Group | null>(null);
  const moonEntriesRef = useRef<
    { name: string; mesh: THREE.Mesh; radius: number; planet: string }[]
  >([]);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const sizeRef = useRef({ width: 1, height: 1 });
  // Held in a ref so the scene effect can call the latest handler without re-running and
  // rebuilding the whole scene whenever the parent re-renders.
  const selectRef = useRef(onSelect);
  useEffect(() => {
    selectRef.current = onSelect;
  }, [onSelect]);

  const view = useRef({
    theta: 0.9,
    phi: 1.05,
    radius: 12,
    targetRadius: 12,
    centre: new THREE.Vector3(),
    targetCentre: new THREE.Vector3(),
  });

  useEffect(() => {
    const mount = mountRef.current;
    const overlay = overlayRef.current;
    if (!mount || !overlay) return;

    const scene = new THREE.Scene();

    const camera = new THREE.PerspectiveCamera(
      38,
      mount.clientWidth / Math.max(mount.clientHeight, 1),
      1e-6,
      40000,
    );
    cameraRef.current = camera;

    // Logarithmic depth is not optional at this scale. The camera has to sit 1e-3 units from
    // Saturn while Neptune is 30 units away and the stars are further still; a conventional
    // depth buffer across that range has so little precision that the front and back of a
    // sphere fight for the same depth values, which draws as concentric arcs across the planet.
    // Every custom shader includes the matching logdepthbuf chunks.
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      logarithmicDepthBuffer: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.setClearColor(0x01030a, 1);
    // Filmic tone mapping keeps the Sun from clipping to a flat white disc while leaving the dim
    // outer planets visible. The dynamic range across the solar system is enormous.
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    mount.appendChild(renderer.domElement);

    const bodyGroup = new THREE.Group();
    const orbitGroup = new THREE.Group();
    const moonGroup = new THREE.Group();
    scene.add(bodyGroup, orbitGroup, moonGroup);
    bodyGroupRef.current = bodyGroup;
    orbitGroupRef.current = orbitGroup;
    moonGroupRef.current = moonGroup;

    sceneRef.current = scene;

    const clock = new THREE.Clock();
    let elapsedTime = 0;
    const projected = new THREE.Vector3();

    // Screen positions of labels already drawn this frame, used to suppress overlaps.
    const placed: { x: number; y: number }[] = [];

    let frame = 0;

    const render = () => {
      const state = view.current;
      const { width, height } = sizeRef.current;

      // Damping is time-based, not per-frame. A fixed per-frame factor makes the flight speed
      // depend on the refresh rate: the same journey takes twice as long at 30 fps as at 60,
      // and stalls entirely when the browser throttles a backgrounded tab. Clamped so a long
      // stall cannot produce one enormous jump.
      const delta = Math.min(clock.getDelta(), 0.1);
      const damping = 1 - Math.exp(-6 * delta);

      state.radius += (state.targetRadius - state.radius) * damping;
      state.centre.lerp(state.targetCentre, damping);

      camera.position.set(
        state.centre.x + state.radius * Math.sin(state.phi) * Math.cos(state.theta),
        state.centre.y + state.radius * Math.cos(state.phi),
        state.centre.z + state.radius * Math.sin(state.phi) * Math.sin(state.theta),
      );
      // Near and far track the viewing distance. Even with a logarithmic buffer, a near plane
      // fixed at 1e-7 wastes most of the range; tying it to how far away we actually are keeps
      // precision where the geometry is.
      const near = Math.max(state.radius * 2e-4, 1e-9);
      const far = Math.max(state.radius * 4000, 400);
      if (camera.near !== near || camera.far !== far) {
        camera.near = near;
        camera.far = far;
        camera.updateProjectionMatrix();
      }

      camera.lookAt(state.centre);
      camera.updateMatrixWorld();

      // The sky rides with the camera, so it is always at a fixed distance rather than a fixed
      // position. Without this it would either clip through the far plane when zoomed in or
      // force the far plane so far out that depth precision collapses again.
      const sky = starsRef.current;
      if (sky) {
        sky.position.copy(camera.position);
        sky.scale.setScalar(Math.max(state.radius * 300, 60));
      }

      elapsedTime += delta;
      const elapsed = elapsedTime;
      // Vertical field of view in radians, used to turn a world size into a pixel size.
      const pixelsPerRadian = height / THREE.MathUtils.degToRad(camera.fov);

      placed.length = 0;

      for (const entry of entriesRef.current) {
        entry.mesh.rotateY(entry.rotationRate);

        if ("uTime" in entry.material.uniforms) {
          entry.material.uniforms.uTime.value = elapsed;
        }
        if ("uCloudOffset" in entry.material.uniforms) {
          entry.material.uniforms.uCloudOffset.value = elapsed * 0.0015;
        }

        // Decide sphere or marker from the body's real apparent size.
        const distance = camera.position.distanceTo(entry.pivot.position);
        const apparentPx = ((2 * entry.radius) / Math.max(distance, 1e-9)) * pixelsPerRadian;
        const drawSphere = apparentPx >= MARKER_THRESHOLD_PX;

        entry.mesh.visible = drawSphere;
        for (const shell of entry.shells) shell.visible = drawSphere;

        projected.copy(entry.pivot.position).project(camera);
        const onScreen =
          projected.z < 1 &&
          projected.x > -1.05 &&
          projected.x < 1.05 &&
          projected.y > -1.05 &&
          projected.y < 1.05;

        const screenX = (projected.x * 0.5 + 0.5) * width;
        const screenY = (-projected.y * 0.5 + 0.5) * height;

        entry.marker.style.display = onScreen && !drawSphere ? "block" : "none";

        // Declutter. At system scale the Moon sits within a pixel or two of the Earth, so their
        // labels overlap into an unreadable smear. Entries are ordered by significance, so a
        // label is dropped when a more significant one has already claimed that patch of screen
        // -- the same thing every real chart does, rather than drawing both and hoping.
        let visible = onScreen;
        if (visible) {
          for (const point of placed) {
            if (Math.abs(point.x - screenX) < 62 && Math.abs(point.y - screenY) < 13) {
              visible = false;
              break;
            }
          }
        }

        entry.label.style.display = visible ? "block" : "none";

        if (onScreen) {
          entry.marker.style.transform = `translate(${screenX - 5}px, ${screenY - 5}px)`;
        }
        if (visible) {
          placed.push({ x: screenX, y: screenY });
          // Labels clear the body when it is drawn as a sphere, so they never sit on top of it.
          const offset = drawSphere ? Math.min(apparentPx * 0.5 + 10, height * 0.4) : 11;
          entry.label.style.transform = `translate(${screenX + offset}px, ${screenY - 7}px)`;
        }
      }

      // Moons only appear once they are worth resolving. At system scale they sit inside their
      // planet's own marker, so drawing them there would add clutter that carries no information.
      for (const moon of moonEntriesRef.current) {
        const moonDistance = camera.position.distanceTo(moon.mesh.position);
        const moonPixels = ((2 * moon.radius) / Math.max(moonDistance, 1e-9)) * pixelsPerRadian;
        moon.mesh.visible = moonPixels >= 2.5;
      }

      // The asteroid cloud is a system-scale object. Close to a planet it is just noise across
      // the view, and every point is millions of kilometres away in any case.
      if (asteroidsRef.current) {
        asteroidsRef.current.visible = state.radius > 0.02;
      }

      renderer.render(scene, camera);
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
      const state = view.current;
      const dx = event.clientX - lastX;
      const dy = event.clientY - lastY;
      state.theta -= dx * 0.005;
      state.phi = Math.min(Math.PI - 0.03, Math.max(0.03, state.phi - dy * 0.005));
      lastX = event.clientX;
      lastY = event.clientY;
    };
    const onPointerUp = () => {
      dragging = false;
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const state = view.current;
      state.targetRadius = Math.min(
        3000,
        // The lower bound has to reach a planet's own radius, which is ~4e-5 AU for Earth,
        // otherwise flying to a body stops short of ever seeing its surface.
        Math.max(3e-5, state.targetRadius * (1 + event.deltaY * 0.0016)),
      );
    };

    const element = renderer.domElement;
    element.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    element.addEventListener("wheel", onWheel, { passive: false });

    const onResize = () => {
      if (!mount.clientWidth) return;
      sizeRef.current = { width: mount.clientWidth, height: mount.clientHeight };
      camera.aspect = mount.clientWidth / Math.max(mount.clientHeight, 1);
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    onResize();
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

  // The star catalogue. Built once when it arrives: the sky does not change with the date, and
  // rebuilding eight thousand points per frame would be absurd.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !starCatalogue) return;

    const stars = buildStars(starCatalogue.stars);
    scene.add(stars);
    starsRef.current = stars;

    return () => {
      scene.remove(stars);
      stars.traverse((node) => {
        if (node instanceof THREE.Points) {
          node.geometry.dispose();
          (node.material as THREE.Material).dispose();
        }
      });
      starsRef.current = null;
    };
  }, [starCatalogue]);

  // The asteroid cloud, rebuilt when the date or the radial mapping changes.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !asteroids) return;

    const cloud = buildAsteroids(asteroids.asteroids, (x, y, z) => place(x, y, z, distanceMode));
    scene.add(cloud);
    asteroidsRef.current = cloud;

    return () => {
      scene.remove(cloud);
      cloud.geometry.dispose();
      (cloud.material as THREE.Material).dispose();
      asteroidsRef.current = null;
    };
  }, [asteroids, distanceMode]);

  // Orbit paths. Rebuilt only when the paths or the radial mapping change.
  useEffect(() => {
    const group = orbitGroupRef.current;
    if (!group || !orbits) return;

    disposeChildren(group);

    for (const [body, path] of Object.entries(orbits.orbits)) {
      const points = path.map((point) => place(point.x_au, point.y_au, point.z_au, distanceMode));
      points.push(points[0].clone());

      group.add(
        new THREE.Line(
          new THREE.BufferGeometry().setFromPoints(points),
          new THREE.LineBasicMaterial({
            color: new THREE.Color(APPEARANCE[body]?.colour ?? "#7f8ea8"),
            transparent: true,
            opacity: 0.34,
          }),
        ),
      );
    }
  }, [orbits, distanceMode]);

  // Bodies, labels and markers.
  useEffect(() => {
    const group = bodyGroupRef.current;
    const overlay = overlayRef.current;
    if (!group || !overlay || !snapshot) return;

    disposeChildren(group);
    overlay.replaceChildren();
    entriesRef.current = [];

    // Significance order for label decluttering: the Sun and planets outrank the Moon, which
    // otherwise wins by sheer proximity to the Earth and hides it.
    const priority = [
      "sun", "jupiter", "saturn", "earth", "mars", "venus",
      "mercury", "uranus", "neptune", "moon",
    ];
    const ordered = [...snapshot.bodies].sort(
      (a, b) => priority.indexOf(a.body) - priority.indexOf(b.body),
    );

    for (const body of ordered) {
      const appearance = APPEARANCE[body.body];
      if (!appearance) continue;

      const position = place(
        body.ecliptic_x_au,
        body.ecliptic_y_au,
        body.ecliptic_z_au,
        distanceMode,
      );
      const radius = trueRadius(body.radius_m);

      const pivot = new THREE.Group();
      pivot.position.copy(position);
      // The body's real axial tilt, so the spin axis leans correctly: Uranus lies on its side
      // and Venus is upside down because they are.
      pivot.rotation.z = THREE.MathUtils.degToRad(appearance.tiltDeg);
      group.add(pivot);

      const material = createSurfaceMaterial(body.body);

      // Sunlight direction at this body. The Sun is at the origin, so it is simply the direction
      // back toward it — the same geometry that puts the terminator where it belongs.
      const toSun = position.clone().negate();
      if (toSun.lengthSq() === 0) toSun.set(1, 0, 0);
      toSun.normalize();
      if ("uLightDir" in material.uniforms) material.uniforms.uLightDir.value.copy(toSun);

      const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 96, 64), material);
      pivot.add(mesh);

      const shells: THREE.Object3D[] = [];
      const extraMaterials: THREE.ShaderMaterial[] = [];

      if (body.body === "sun") {
        const corona = createGlowMaterial("#ffb347", 0.9, 2.6, false);
        const coronaMesh = new THREE.Mesh(new THREE.SphereGeometry(radius * 2.4, 48, 32), corona);
        pivot.add(coronaMesh);
        shells.push(coronaMesh);
        extraMaterials.push(corona);
      }

      if (appearance.atmosphere) {
        const shell = createGlowMaterial(
          appearance.atmosphere.colour,
          appearance.atmosphere.intensity,
          2.0,
          true,
        );
        shell.uniforms.uLightDir.value.copy(toSun);
        // A wider shell with a gentler falloff. A thin one concentrates the glow into a hard
        // bright ring that reads as an outline drawn round the planet rather than as air.
        const shellMesh = new THREE.Mesh(
          new THREE.SphereGeometry(radius * 1.055, 64, 48),
          shell,
        );
        pivot.add(shellMesh);
        shells.push(shellMesh);
        extraMaterials.push(shell);
      }

      if (appearance.rings) {
        const inner = radius * appearance.rings.inner;
        const outer = radius * appearance.rings.outer;
        const ringMaterial = createRingMaterial(inner, outer, radius);
        ringMaterial.uniforms.uLightDir.value.copy(toSun);

        const ring = new THREE.Mesh(new THREE.RingGeometry(inner, outer, 256, 1), ringMaterial);
        // RingGeometry is built in the xy-plane; the rings lie in the body's equatorial plane,
        // and the pivot's tilt then carries them.
        ring.rotation.x = Math.PI / 2;
        pivot.add(ring);
        shells.push(ring);
        extraMaterials.push(ringMaterial);
      }

      // Overlay marker and label. HTML rather than sprites, so the type is crisp at any zoom.
      const marker = document.createElement("button");
      marker.type = "button";
      marker.className =
        "pointer-events-auto absolute left-0 top-0 h-2.5 w-2.5 rounded-full border " +
        "opacity-90 hover:opacity-100";
      marker.style.borderColor = appearance.colour;
      marker.style.background = "transparent";
      marker.setAttribute("aria-label", body.body);
      marker.addEventListener("click", () => selectRef.current(body.body));

      const label = document.createElement("button");
      label.type = "button";
      label.className =
        "pointer-events-auto absolute left-0 top-0 whitespace-nowrap text-[10px] " +
        "uppercase tracking-[0.14em] opacity-70 hover:opacity-100";
      label.style.color = appearance.colour;
      label.textContent = body.body;
      label.addEventListener("click", () => selectRef.current(body.body));

      overlay.append(marker as unknown as HTMLDivElement, label as unknown as HTMLDivElement);

      entriesRef.current.push({
        name: body.body,
        pivot,
        mesh,
        shells,
        material,
        extraMaterials,
        radius,
        // Sped up to be watchable. Only the direction and the relative rate are true: Jupiter
        // visibly outruns Venus, and Venus turns backwards.
        rotationRate: (2 * Math.PI) / (appearance.rotationHours * 90),
        label: label as unknown as HTMLDivElement,
        marker: marker as unknown as HTMLDivElement,
      });
    }
  }, [snapshot, distanceMode]);

  // Moons. Kept separate from the planets because they are positioned relative to their parent
  // and only make sense once the camera is close enough to that planet to tell them apart.
  useEffect(() => {
    const group = moonGroupRef.current;
    if (!group || !moons || !snapshot) return;

    disposeChildren(group);
    moonEntriesRef.current = [];

    const planets = new Map(snapshot.bodies.map((body) => [body.body, body]));

    for (const moon of moons.moons) {
      const parent = planets.get(moon.planet);
      if (!parent || moon.radius_m === null) continue;

      // Earth's Moon is already in the planet snapshot, so drawing it here would double it.
      if (moon.name === "Moon") continue;

      const position = place(moon.x_au, moon.y_au, moon.z_au, distanceMode);
      const radius = trueRadius(moon.radius_m);

      const material = createSurfaceMaterial("moon");
      const toSun = position.clone().negate().normalize();
      if ("uLightDir" in material.uniforms) material.uniforms.uLightDir.value.copy(toSun);

      const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 48, 32), material);
      mesh.position.copy(position);
      group.add(mesh);

      moonEntriesRef.current.push({ name: moon.name, mesh, radius, planet: moon.planet });
    }
  }, [moons, snapshot, distanceMode]);

  // Focus: glide to a body and stop at a distance that frames it.
  useEffect(() => {
    const state = view.current;

    if (!focus || !snapshot) {
      state.targetCentre.set(0, 0, 0);
      state.targetRadius = distanceMode === "linear" ? 12 : 34;
      return;
    }

    const body = snapshot.bodies.find((item) => item.body === focus);
    if (!body) return;

    const position = place(
      body.ecliptic_x_au,
      body.ecliptic_y_au,
      body.ecliptic_z_au,
      distanceMode,
    );
    state.targetCentre.copy(position);
    // Four radii out frames a body with room around it, and is far enough that the near plane
    // never clips into the surface.
    state.targetRadius = trueRadius(body.radius_m) * 4.2;

    // Approach from the sunlit side. Camera azimuth is otherwise whatever the user last left it
    // at, and arriving on a body's night side shows an unlit disc against a black sky - which
    // looks exactly like the flight having failed. The offset keeps a terminator in view rather
    // than presenting a flat fully-lit face.
    if (position.lengthSq() > 0) {
      const sunward = position.clone().negate().normalize();
      state.phi = Math.min(Math.PI - 0.25, Math.max(0.25, Math.acos(sunward.y) + 0.12));
      state.theta = Math.atan2(sunward.z, sunward.x) + 0.6;
    }
  }, [focus, snapshot, distanceMode]);

  return (
    <div className="relative h-[560px] w-full overflow-hidden">
      <div
        ref={mountRef}
        className="absolute inset-0 cursor-grab touch-none active:cursor-grabbing"
      />
      <div ref={overlayRef} className="pointer-events-none absolute inset-0 select-none" />
    </div>
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
