import * as THREE from "three";

/**
 * Materials for solar-system bodies, built on real surface imagery.
 *
 * **Why maps rather than procedural surfaces.** The first version generated surfaces from noise,
 * on the reasoning that a rendering is obviously a rendering while a photograph might be mistaken
 * for one. That reasoning was backwards. A procedurally generated Earth has invented continents:
 * it is a *fabrication*, and it is the exact thing this project exists to avoid. A real map is
 * observed data, and observed data gets a source — see `public/textures/ATTRIBUTION.md`.
 *
 * What the maps do not carry is any number. Position, orbit, distance, illumination direction,
 * axial tilt and rotation rate all come from the science service with their own provenance. A
 * texture only ever answers "what does this surface look like".
 */

/** Shared loader, so a texture is fetched once however many materials want it. */
const loader = new THREE.TextureLoader();

const cache = new Map<string, THREE.Texture>();

/** Load a texture from `public/textures`, cached, with colour space set for albedo maps. */
function texture(file: string, colour = true): THREE.Texture {
  const key = `${file}:${colour}`;
  const existing = cache.get(key);
  if (existing) return existing;

  const loaded = loader.load(`/textures/${file}`);
  // Albedo maps are authored in sRGB. Without this they render washed out and too bright, which
  // is one of the commonest reasons a textured planet looks like a toy.
  if (colour) loaded.colorSpace = THREE.SRGBColorSpace;
  loaded.anisotropy = 8;
  cache.set(key, loaded);
  return loaded;
}

const VERTEX_GLSL = /* glsl */ `
varying vec2 vUv;
varying vec3 vNormalW;
varying vec3 vWorld;

void main(){
  vUv = uv;
  vNormalW = normalize(mat3(modelMatrix) * normal);
  vec4 worldPosition = modelMatrix * vec4(position, 1.0);
  vWorld = worldPosition.xyz;
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
}
`;

/**
 * Standard lit body: an albedo map with a physically placed terminator.
 *
 * A plain lambert step produces a hard, obviously synthetic edge. Real terminators are softened
 * by the Sun being an extended source rather than a point, and by atmosphere where there is one,
 * so the falloff is smoothed slightly.
 */
const SURFACE_FRAGMENT_GLSL = /* glsl */ `
uniform sampler2D uMap;
uniform vec3  uLightDir;
uniform float uSoftness;
uniform float uAmbient;

varying vec2 vUv;
varying vec3 vNormalW;
varying vec3 vWorld;

void main(){
  vec3 albedo = texture2D(uMap, vUv).rgb;
  vec3 normal = normalize(vNormalW);

  float incidence = dot(normal, normalize(uLightDir));
  float lambert = smoothstep(-uSoftness, uSoftness, incidence);

  gl_FragColor = vec4(albedo * (uAmbient + (1.0 - uAmbient) * lambert), 1.0);
}
`;

/**
 * Earth: day map, night lights, and a cloud layer, blended across the terminator.
 *
 * The night side is what makes it read as Earth rather than as a blue ball — city lights appear
 * exactly where the Sun has set, which is a real consequence of the illumination geometry the
 * service computes.
 */
const EARTH_FRAGMENT_GLSL = /* glsl */ `
uniform sampler2D uDay;
uniform sampler2D uNight;
uniform sampler2D uClouds;
uniform vec3  uLightDir;
uniform float uCloudOffset;

varying vec2 vUv;
varying vec3 vNormalW;
varying vec3 vWorld;

void main(){
  vec3 normal = normalize(vNormalW);
  float incidence = dot(normal, normalize(uLightDir));

  float day = smoothstep(-0.12, 0.18, incidence);

  vec3 surface = texture2D(uDay, vUv).rgb;
  vec3 lights  = texture2D(uNight, vUv).rgb;

  // Clouds are sampled at an offset longitude so they are not pinned to the ground beneath them.
  // They are a fixed snapshot, not current weather - the panel says so.
  float cloud = texture2D(uClouds, vec2(vUv.x + uCloudOffset, vUv.y)).r;

  vec3 lit = surface * (0.03 + 0.97 * day);
  lit = mix(lit, vec3(1.0) * (0.05 + 0.95 * day), cloud * 0.7);

  // City lights only where it is genuinely night, and dimmed under cloud.
  lit += lights * (1.0 - day) * 1.6 * (1.0 - cloud * 0.8);

  gl_FragColor = vec4(lit, 1.0);
}
`;

/** The Sun: emissive, unlit, with limb darkening so it is not a flat disc. */
const SUN_FRAGMENT_GLSL = /* glsl */ `
uniform sampler2D uMap;
uniform float uTime;

varying vec2 vUv;
varying vec3 vNormalW;
varying vec3 vWorld;

void main(){
  vec3 surface = texture2D(uMap, vec2(vUv.x + uTime * 0.004, vUv.y)).rgb;

  vec3 viewDir = normalize(cameraPosition - vWorld);
  float facing = abs(dot(normalize(vNormalW), viewDir));

  // Limb darkening: the Sun is measurably dimmer at its edge, because the line of sight there
  // passes through cooler, higher photosphere.
  float limb = 0.62 + 0.38 * pow(facing, 0.55);

  gl_FragColor = vec4(surface * limb * 1.3, 1.0);
}
`;

/**
 * Additive limb glow for a body with an atmosphere, and for the Sun's corona.
 *
 * Rendered on the back faces of a slightly larger shell. The normals of those faces point away
 * from the camera, so `max(dot(n, v), 0.0)` clamps to zero across the whole disc rather than
 * only at the limb, and the glow floods the body — which is exactly how the first version came
 * out. `abs` measures how edge-on a surface is regardless of which way it faces.
 */
const GLOW_FRAGMENT_GLSL = /* glsl */ `
uniform vec3  uColor;
uniform vec3  uLightDir;
uniform float uIntensity;
uniform float uPower;
uniform float uLitOnly;

varying vec2 vUv;
varying vec3 vNormalW;
varying vec3 vWorld;

void main(){
  vec3 normal = normalize(vNormalW);
  vec3 viewDir = normalize(cameraPosition - vWorld);

  float facing = abs(dot(normal, viewDir));
  float rim = pow(1.0 - facing, uPower);

  // An atmosphere only glows where sunlight passes through it; a corona glows all round.
  float lit = mix(1.0, smoothstep(-0.45, 0.35, dot(normal, normalize(uLightDir))), uLitOnly);

  gl_FragColor = vec4(uColor, clamp(rim * lit * uIntensity, 0.0, 1.0));
}
`;

/** Saturn's rings: a real radial transmission strip, with the planet's shadow cast across it. */
const RING_FRAGMENT_GLSL = /* glsl */ `
uniform sampler2D uMap;
uniform vec3  uLightDir;
uniform float uInner;
uniform float uOuter;
uniform float uPlanetRadius;

varying vec3 vLocal;
varying float vRadius;

void main(){
  float t = clamp((vRadius - uInner) / max(uOuter - uInner, 1e-6), 0.0, 1.0);
  vec4 sampled = texture2D(uMap, vec2(t, 0.5));

  // The planet's shadow on the rings. A point is shadowed when it lies behind the planet along
  // the sunward direction and within its radius of that axis. The geometry is simple enough to
  // do exactly, and the dark wedge it produces is one of the most recognisable things about
  // Saturn seen from close up.
  vec3 toSun = normalize(uLightDir);
  float along = dot(vLocal, toSun);
  float perpendicular = length(vLocal - toSun * along);
  float shadow = (along < 0.0 && perpendicular < uPlanetRadius)
    ? smoothstep(uPlanetRadius, uPlanetRadius * 0.72, perpendicular)
    : 0.0;

  gl_FragColor = vec4(sampled.rgb * (1.0 - shadow * 0.88), sampled.a);
}
`;

const RING_VERTEX_GLSL = /* glsl */ `
varying vec3 vLocal;
varying float vRadius;

void main(){
  vRadius = length(position.xy);
  vLocal = position;

  gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
}
`;

/** Per-body appearance. Tilts and rotation periods are real measured properties. */
export type BodyAppearance = {
  /** Surface map, relative to `public/textures`. */
  map: string;
  /** Marker and orbit-line colour, chosen to match the body's real appearance. */
  colour: string;
  /** Axial tilt in degrees. */
  tiltDeg: number;
  /** Sidereal rotation period in hours; negative is retrograde. */
  rotationHours: number;
  /** Atmospheric limb, where the body has an atmosphere worth drawing. */
  atmosphere?: { colour: string; intensity: number };
  /** Ring extent, in multiples of the body's radius. */
  rings?: { inner: number; outer: number };
};

export const APPEARANCE: Record<string, BodyAppearance> = {
  sun: { map: "sun.jpg", colour: "#ffcf6b", tiltDeg: 7.25, rotationHours: 609.12 },
  mercury: { map: "mercury.jpg", colour: "#9c8f84", tiltDeg: 0.034, rotationHours: 1407.6 },
  venus: {
    map: "venus_atmosphere.jpg",
    colour: "#e8c07d",
    tiltDeg: 177.36,
    rotationHours: -5832.5,
    atmosphere: { colour: "#f5e2b0", intensity: 0.4 },
  },
  earth: {
    map: "earth_daymap.jpg",
    colour: "#6b9bd6",
    tiltDeg: 23.44,
    rotationHours: 23.934,
    atmosphere: { colour: "#6fb0ff", intensity: 0.5 },
  },
  moon: { map: "moon.jpg", colour: "#b0aca6", tiltDeg: 6.68, rotationHours: 655.7 },
  mars: {
    map: "mars.jpg",
    colour: "#c1502e",
    tiltDeg: 25.19,
    rotationHours: 24.623,
    atmosphere: { colour: "#e0a17a", intensity: 0.2 },
  },
  jupiter: {
    map: "jupiter.jpg",
    colour: "#d8ca9d",
    tiltDeg: 3.13,
    rotationHours: 9.925,
    atmosphere: { colour: "#e8d3ad", intensity: 0.35 },
  },
  saturn: {
    map: "saturn.jpg",
    colour: "#e3d9a5",
    tiltDeg: 26.73,
    rotationHours: 10.656,
    atmosphere: { colour: "#f0e2be", intensity: 0.3 },
    rings: { inner: 1.24, outer: 2.27 },
  },
  uranus: {
    map: "uranus.jpg",
    colour: "#a6d8e0",
    tiltDeg: 97.77,
    rotationHours: -17.24,
    atmosphere: { colour: "#9fe3ec", intensity: 0.32 },
  },
  neptune: {
    map: "neptune.jpg",
    colour: "#5b7fd4",
    tiltDeg: 28.32,
    rotationHours: 16.11,
    atmosphere: { colour: "#6d97ef", intensity: 0.34 },
  },
};

/** Build the lit surface material for a body. */
export function createSurfaceMaterial(body: string): THREE.ShaderMaterial {
  if (body === "sun") {
    return new THREE.ShaderMaterial({
      vertexShader: VERTEX_GLSL,
      fragmentShader: SUN_FRAGMENT_GLSL,
      uniforms: {
        uMap: { value: texture("sun.jpg") },
        uTime: { value: 0 },
      },
    });
  }

  if (body === "earth") {
    return new THREE.ShaderMaterial({
      vertexShader: VERTEX_GLSL,
      fragmentShader: EARTH_FRAGMENT_GLSL,
      uniforms: {
        uDay: { value: texture("earth_daymap.jpg") },
        uNight: { value: texture("earth_nightmap.jpg") },
        uClouds: { value: texture("earth_clouds.jpg", false) },
        uLightDir: { value: new THREE.Vector3(1, 0, 0) },
        uCloudOffset: { value: 0 },
      },
    });
  }

  const appearance = APPEARANCE[body];

  return new THREE.ShaderMaterial({
    vertexShader: VERTEX_GLSL,
    fragmentShader: SURFACE_FRAGMENT_GLSL,
    uniforms: {
      uMap: { value: texture(appearance.map) },
      uLightDir: { value: new THREE.Vector3(1, 0, 0) },
      // A thicker atmosphere scatters light further round the limb, so its terminator is softer.
      uSoftness: { value: appearance.atmosphere ? 0.16 : 0.06 },
      uAmbient: { value: 0.025 },
    },
  });
}

/** Build an additive limb glow. `litOnly` distinguishes an atmosphere from a corona. */
export function createGlowMaterial(
  colour: string,
  intensity: number,
  power: number,
  litOnly: boolean,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: VERTEX_GLSL,
    fragmentShader: GLOW_FRAGMENT_GLSL,
    uniforms: {
      uColor: { value: new THREE.Color(colour) },
      uLightDir: { value: new THREE.Vector3(1, 0, 0) },
      uIntensity: { value: intensity },
      uPower: { value: power },
      uLitOnly: { value: litOnly ? 1 : 0 },
    },
    transparent: true,
    blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
    depthWrite: false,
  });
}

/** Build Saturn's ring material. */
export function createRingMaterial(
  inner: number,
  outer: number,
  planetRadius: number,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: RING_VERTEX_GLSL,
    fragmentShader: RING_FRAGMENT_GLSL,
    uniforms: {
      uMap: { value: texture("saturn_ring.png") },
      uLightDir: { value: new THREE.Vector3(1, 0, 0) },
      uInner: { value: inner },
      uOuter: { value: outer },
      uPlanetRadius: { value: planetRadius },
    },
    transparent: true,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
}
