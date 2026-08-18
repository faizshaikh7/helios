import * as THREE from "three";

/**
 * Procedural surface materials for solar-system bodies.
 *
 * **Why procedural rather than photographic textures.** Real NASA surface maps are the obvious
 * alternative, and they would look better still. They are also tens of megabytes per body, which
 * would dominate the deploy bundle, and they are photographs — a reader could not tell a real
 * map from a decorative one, which is the confusion this project exists to remove. These are
 * unambiguously *renderings*: the band structure, cloud cover and albedo patterns are generated,
 * not observed, and the panel says so.
 *
 * What is real is everything the science service computes: position, orbit, illumination
 * direction, axial tilt and rotation rate. The surface is the only part that is an artist's
 * impression, and it is kept visually distinct from the measured quantities in the caption.
 */

/** Which surface treatment a body gets. */
export type SurfaceStyle =
  | "sun"
  | "rocky"
  | "venus"
  | "earth"
  | "mars"
  | "gasGiant"
  | "iceGiant";

/**
 * 3D simplex noise, after Ashima Arts / Stefan Gustavson (MIT licence).
 *
 * The standard implementation. Included inline because the shader must be self-contained, and
 * every procedural surface below is built from octaves of it.
 */
const NOISE_GLSL = /* glsl */ `
vec3 mod289(vec3 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
vec4 mod289(vec4 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
vec4 permute(vec4 x){ return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r){ return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v){
  const vec2 C = vec2(1.0/6.0, 1.0/3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i  = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(
             i.z + vec4(0.0, i1.z, i2.z, 1.0))
           + i.y + vec4(0.0, i1.y, i2.y, 1.0))
           + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
}

float fbm(vec3 p, int octaves, float lacunarity, float gain){
  float sum = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 8; i++){
    if (i >= octaves) break;
    sum += amp * snoise(p);
    p *= lacunarity;
    amp *= gain;
  }
  return sum;
}

/** Ridged noise, for the sharp edges of crater rims and storm boundaries. */
float ridged(vec3 p, int octaves){
  float sum = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 8; i++){
    if (i >= octaves) break;
    sum += amp * (1.0 - abs(snoise(p)));
    p *= 2.1;
    amp *= 0.5;
  }
  return sum;
}
`;

const VERTEX_GLSL = /* glsl */ `
varying vec3 vObject;
varying vec3 vNormalW;
varying vec3 vWorld;

void main(){
  vObject = normalize(position);
  vNormalW = normalize(mat3(modelMatrix) * normal);
  vec4 worldPosition = modelMatrix * vec4(position, 1.0);
  vWorld = worldPosition.xyz;
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
}
`;

const SURFACE_FRAGMENT_GLSL = /* glsl */ `
uniform float uTime;
uniform int   uStyle;
uniform vec3  uLightDir;
uniform vec3  uColorA;
uniform vec3  uColorB;
uniform vec3  uColorC;
uniform float uSeed;

varying vec3 vObject;
varying vec3 vNormalW;
varying vec3 vWorld;

${NOISE_GLSL}

/** Latitude-banded gas giant, with a domain warp so the bands churn rather than stripe. */
vec3 gasGiant(vec3 p, vec3 a, vec3 b, vec3 c){
  float warp = fbm(p * 2.2 + uSeed, 4, 2.0, 0.5) * 0.22;
  float lat = p.y + warp;

  // Several band frequencies beat against each other, which is what stops it reading as
  // wallpaper. The strong low frequency sets the major belts and zones.
  float bands = sin(lat * 18.0) * 0.5 + sin(lat * 41.0) * 0.28 + sin(lat * 7.0) * 0.5;
  bands = bands * 0.5 + 0.5;

  float turbulence = fbm(p * 6.0 + vec3(uTime * 0.008, 0.0, uSeed), 5, 2.1, 0.55);
  bands = clamp(bands + turbulence * 0.16, 0.0, 1.0);

  vec3 colour = mix(a, b, smoothstep(0.25, 0.75, bands));
  colour = mix(colour, c, smoothstep(0.72, 0.98, bands) * 0.7);

  // A long-lived oval storm, placed in the southern hemisphere like Jupiter's.
  vec2 storm = vec2(atan(p.z, p.x) - 1.1, (p.y + 0.24) * 2.9);
  float oval = 1.0 - smoothstep(0.0, 0.42, length(vec2(storm.x * 0.55, storm.y)));
  colour = mix(colour, vec3(0.78, 0.36, 0.24), oval * 0.75);

  // Poles are darker and less banded on every gas giant.
  colour *= 1.0 - smoothstep(0.72, 1.0, abs(p.y)) * 0.35;
  return colour;
}

void main(){
  vec3 p = vObject;
  vec3 colour;
  float roughness = 1.0;

  if (uStyle == 0) {
    // Sun: granulation plus supergranulation, brightening toward the limb.
    float granule = fbm(p * 26.0 + vec3(uTime * 0.05, uTime * 0.03, uSeed), 5, 2.2, 0.5);
    float supergranule = fbm(p * 6.0 - vec3(uTime * 0.02), 3, 2.0, 0.5);
    float intensity = 0.72 + granule * 0.24 + supergranule * 0.18;
    colour = mix(uColorA, uColorB, clamp(intensity, 0.0, 1.0));
    colour += uColorC * pow(clamp(intensity, 0.0, 1.0), 4.0) * 0.5;
    gl_FragColor = vec4(colour, 1.0);
    return;

  } else if (uStyle == 1) {
    // Rocky and cratered. Ridged noise gives rims; a second octave set gives the maria.
    float craters = ridged(p * 9.0 + uSeed, 5);
    float maria = fbm(p * 2.4 - uSeed, 4, 2.0, 0.5);
    float shade = 0.55 + craters * 0.32 + maria * 0.22;
    colour = mix(uColorA, uColorB, clamp(shade, 0.0, 1.0));
    colour *= 0.86 + 0.14 * fbm(p * 30.0, 3, 2.0, 0.5);

  } else if (uStyle == 2) {
    // Venus: opaque, featureless sulphuric cloud deck with slow zonal streaks.
    float streak = fbm(vec3(p.x * 3.0, p.y * 9.0, p.z * 3.0) + vec3(uTime * 0.02, 0.0, uSeed), 5, 2.1, 0.55);
    float deck = fbm(p * 4.5 + vec3(uTime * 0.01), 4, 2.0, 0.5);
    colour = mix(uColorA, uColorB, clamp(0.5 + streak * 0.4 + deck * 0.25, 0.0, 1.0));

  } else if (uStyle == 3) {
    // Earth: ocean, land, ice caps, and a separate cloud layer above.
    float land = fbm(p * 2.1 + uSeed, 6, 2.1, 0.52);
    float detail = fbm(p * 7.0 - uSeed, 4, 2.0, 0.5);
    float elevation = land + detail * 0.22;

    float isLand = smoothstep(0.02, 0.10, elevation);
    vec3 ocean = mix(vec3(0.02, 0.10, 0.28), vec3(0.05, 0.24, 0.46), smoothstep(-0.35, 0.02, elevation));
    vec3 ground = mix(uColorB, uColorC, smoothstep(0.05, 0.32, elevation));
    colour = mix(ocean, ground, isLand);

    // Ice where it is cold: poles, and high ground at mid latitudes.
    float cold = smoothstep(0.62, 0.88, abs(p.y)) + smoothstep(0.30, 0.45, elevation) * 0.35;
    colour = mix(colour, vec3(0.92, 0.94, 0.97), clamp(cold, 0.0, 1.0) * 0.9);

    float cloud = fbm(p * 3.4 + vec3(uTime * 0.012, 0.0, 3.0), 5, 2.2, 0.55);
    colour = mix(colour, vec3(1.0), smoothstep(0.14, 0.52, cloud) * 0.62);
    roughness = mix(0.35, 1.0, isLand);

  } else if (uStyle == 4) {
    // Mars: oxidised dust, darker volcanic regions, bright polar caps.
    float dust = fbm(p * 2.6 + uSeed, 5, 2.1, 0.5);
    float terrain = ridged(p * 6.5 - uSeed, 4);
    colour = mix(uColorA, uColorB, clamp(0.45 + dust * 0.5, 0.0, 1.0));
    colour = mix(colour, uColorC, smoothstep(0.55, 0.95, terrain) * 0.45);

    float cap = smoothstep(0.80, 0.93, abs(p.y) + dust * 0.05);
    colour = mix(colour, vec3(0.94, 0.94, 0.92), cap);

  } else if (uStyle == 5) {
    colour = gasGiant(p, uColorA, uColorB, uColorC);

  } else {
    // Ice giant: nearly featureless, faint bands, deep methane blue.
    float band = sin(p.y * 11.0 + fbm(p * 3.0, 3, 2.0, 0.5) * 0.6) * 0.5 + 0.5;
    float haze = fbm(p * 5.0 + vec3(uTime * 0.006), 4, 2.0, 0.5);
    colour = mix(uColorA, uColorB, clamp(band * 0.55 + haze * 0.3 + 0.2, 0.0, 1.0));
    colour = mix(colour, uColorC, smoothstep(0.75, 1.0, band) * 0.3);
  }

  // Lighting. The Sun is the only source, so the terminator is where it physically belongs.
  vec3 normal = normalize(vNormalW);
  float incidence = dot(normal, normalize(uLightDir));

  // A slightly soft terminator: a hard step reads as a CG artefact, and real limbs are softened
  // by atmosphere and by the Sun being an extended source rather than a point.
  float lambert = smoothstep(-0.08, 0.34, incidence);

  vec3 lit = colour * (0.045 + 0.955 * lambert);

  // Weak specular on the smoother bodies only.
  vec3 viewDir = normalize(cameraPosition - vWorld);
  float specular = pow(max(dot(reflect(-normalize(uLightDir), normal), viewDir), 0.0), 26.0);
  lit += vec3(1.0) * specular * (1.0 - roughness) * 0.30 * lambert;

  gl_FragColor = vec4(lit, 1.0);
}
`;

/**
 * Additive shell that gives a body an atmospheric limb.
 *
 * Rendered on the back faces of a slightly larger sphere, so the glow appears around the edge
 * rather than washing over the disc. The rim is brightest where the atmosphere is lit and
 * viewed edge-on, which is why a real crescent planet has a bright arc rather than a uniform
 * halo.
 */
const ATMOSPHERE_FRAGMENT_GLSL = /* glsl */ `
uniform vec3 uColor;
uniform vec3 uLightDir;
uniform float uIntensity;

varying vec3 vNormalW;
varying vec3 vWorld;

void main(){
  vec3 normal = normalize(vNormalW);
  vec3 viewDir = normalize(cameraPosition - vWorld);

  // These shells render on BackSide, so the visible fragments are the *far* wall of the sphere
  // and their outward normals point away from the camera. Clamping with max(dot, 0.0) therefore
  // returns zero across the entire disc, not just at the limb, and the "rim" glow floods the
  // whole body. abs() measures how edge-on the surface is regardless of which way it faces.
  float facing = abs(dot(normal, viewDir));
  float rim = pow(1.0 - facing, 2.6);
  float lit = smoothstep(-0.35, 0.5, dot(normal, normalize(uLightDir)));

  gl_FragColor = vec4(uColor, rim * lit * uIntensity);
}
`;

/** Corona around the Sun: a wide, soft, additive falloff. */
const CORONA_FRAGMENT_GLSL = /* glsl */ `
uniform vec3 uColor;
uniform float uTime;

varying vec3 vNormalW;
varying vec3 vWorld;
varying vec3 vObject;

${NOISE_GLSL}

void main(){
  vec3 viewDir = normalize(cameraPosition - vWorld);

  // Same BackSide correction as the atmosphere shell: without abs() the corona renders as an
  // opaque disc that swallows the inner planets rather than as a halo around the limb.
  float facing = abs(dot(normalize(vNormalW), viewDir));
  float rim = 1.0 - facing;

  // A steep falloff so the glow hugs the limb and fades quickly outward, the way a corona
  // actually does, instead of filling the sphere it lives on.
  float falloff = pow(rim, 5.0);
  float flicker = 0.88 + 0.12 * fbm(vObject * 5.0 + vec3(uTime * 0.06), 3, 2.0, 0.5);

  gl_FragColor = vec4(uColor, clamp(falloff * flicker, 0.0, 1.0) * 0.55);
}
`;

/** Saturn's rings: radial density structure with a real Cassini division. */
const RING_FRAGMENT_GLSL = /* glsl */ `
uniform vec3 uColorA;
uniform vec3 uColorB;
uniform float uInner;
uniform float uOuter;
uniform vec3 uLightDir;

varying vec3 vWorld;
varying vec2 vUv;

${NOISE_GLSL}

void main(){
  // RingGeometry's uv.x runs radially outward once the geometry is built with that intent.
  float t = clamp(vUv.x, 0.0, 1.0);

  // Banding: many fine ringlets, from noise rather than a regular pattern.
  float fine = fbm(vec3(t * 220.0, 0.0, 0.0), 4, 2.0, 0.5) * 0.5 + 0.5;
  float coarse = fbm(vec3(t * 34.0, 5.0, 0.0), 3, 2.0, 0.5) * 0.5 + 0.5;

  float density = clamp(coarse * 0.7 + fine * 0.45, 0.0, 1.0);

  // The Cassini division, at roughly 68% of the way out through the main rings.
  density *= 1.0 - 0.92 * exp(-pow((t - 0.62) / 0.035, 2.0));
  // The fainter C ring inside.
  density *= mix(0.35, 1.0, smoothstep(0.0, 0.22, t));
  // Outer edge falls away rather than stopping dead.
  density *= 1.0 - smoothstep(0.88, 1.0, t);

  vec3 colour = mix(uColorA, uColorB, fine);

  gl_FragColor = vec4(colour, density * 0.85);
}
`;

const RING_VERTEX_GLSL = /* glsl */ `
varying vec3 vWorld;
varying vec2 vUv;

uniform float uInner;
uniform float uOuter;

void main(){
  // Radial parameter, recovered from the vertex's own distance from the centre. RingGeometry's
  // built-in uv is not radial, so it is computed here instead.
  float radius = length(position.xy);
  vUv = vec2((radius - uInner) / max(uOuter - uInner, 1e-6), 0.0);

  vec4 worldPosition = modelMatrix * vec4(position, 1.0);
  vWorld = worldPosition.xyz;
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
}
`;

/** Per-body surface parameters. */
export type BodyAppearance = {
  style: SurfaceStyle;
  colours: [string, string, string];
  /** Axial tilt in degrees, a real measured property of the body. */
  tiltDeg: number;
  /** Sidereal rotation period in hours; negative for retrograde rotation. */
  rotationHours: number;
  /** Atmospheric limb colour and strength, when the body has an atmosphere worth drawing. */
  atmosphere?: { colour: string; intensity: number };
  /** Ring system extent as multiples of the body's radius. */
  rings?: { inner: number; outer: number; colours: [string, string] };
};

const STYLE_INDEX: Record<SurfaceStyle, number> = {
  sun: 0,
  rocky: 1,
  venus: 2,
  earth: 3,
  mars: 4,
  gasGiant: 5,
  iceGiant: 6,
};

/**
 * Appearance of each body.
 *
 * Tilts and rotation periods are real values. Colours are chosen to match how each body actually
 * looks, but the surface *patterns* are generated — see the module docstring.
 */
export const APPEARANCE: Record<string, BodyAppearance> = {
  sun: {
    style: "sun",
    colours: ["#ff8a1e", "#ffd66b", "#fff4c4"],
    tiltDeg: 7.25,
    rotationHours: 609.12,
  },
  mercury: {
    style: "rocky",
    colours: ["#5a5049", "#a2968a", "#000000"],
    tiltDeg: 0.034,
    rotationHours: 1407.6,
  },
  venus: {
    style: "venus",
    colours: ["#b07f37", "#efd9a1", "#000000"],
    tiltDeg: 177.36,
    rotationHours: -5832.5,
    atmosphere: { colour: "#f3dda6", intensity: 1.1 },
  },
  earth: {
    style: "earth",
    colours: ["#0a2647", "#3f6b32", "#8a7a58"],
    tiltDeg: 23.44,
    rotationHours: 23.934,
    atmosphere: { colour: "#5aa9ff", intensity: 1.35 },
  },
  moon: {
    style: "rocky",
    colours: ["#4a4744", "#b9b4ad", "#000000"],
    tiltDeg: 6.68,
    rotationHours: 655.7,
  },
  mars: {
    style: "mars",
    colours: ["#7d3b1e", "#c2653a", "#4e2415"],
    tiltDeg: 25.19,
    rotationHours: 24.623,
    atmosphere: { colour: "#e0a17a", intensity: 0.45 },
  },
  jupiter: {
    style: "gasGiant",
    colours: ["#8c6b4a", "#e6d3b3", "#c9a06b"],
    tiltDeg: 3.13,
    rotationHours: 9.925,
    atmosphere: { colour: "#e8d3ad", intensity: 0.5 },
  },
  saturn: {
    style: "gasGiant",
    colours: ["#a8894f", "#f0e2be", "#d6bd8a"],
    tiltDeg: 26.73,
    rotationHours: 10.656,
    atmosphere: { colour: "#f0e2be", intensity: 0.45 },
    rings: { inner: 1.24, outer: 2.27, colours: ["#c9b48d", "#f2e8d2"] },
  },
  uranus: {
    style: "iceGiant",
    colours: ["#3f9aa8", "#a8dfe6", "#cdeff2"],
    tiltDeg: 97.77,
    rotationHours: -17.24,
    atmosphere: { colour: "#9fe3ec", intensity: 0.85 },
  },
  neptune: {
    style: "iceGiant",
    colours: ["#2a4fb5", "#5f86e0", "#8fb0f0"],
    tiltDeg: 28.32,
    rotationHours: 16.11,
    atmosphere: { colour: "#6d97ef", intensity: 0.95 },
  },
};

/** Build the procedural surface material for a body. */
export function createSurfaceMaterial(body: string, seed: number): THREE.ShaderMaterial {
  const appearance = APPEARANCE[body] ?? APPEARANCE.mercury;

  return new THREE.ShaderMaterial({
    vertexShader: VERTEX_GLSL,
    fragmentShader: SURFACE_FRAGMENT_GLSL,
    uniforms: {
      uTime: { value: 0 },
      uStyle: { value: STYLE_INDEX[appearance.style] },
      uLightDir: { value: new THREE.Vector3(1, 0, 0) },
      uColorA: { value: new THREE.Color(appearance.colours[0]) },
      uColorB: { value: new THREE.Color(appearance.colours[1]) },
      uColorC: { value: new THREE.Color(appearance.colours[2]) },
      uSeed: { value: seed },
    },
  });
}

/** Build the additive atmospheric shell for a body that has one. */
export function createAtmosphereMaterial(colour: string, intensity: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: VERTEX_GLSL,
    fragmentShader: ATMOSPHERE_FRAGMENT_GLSL,
    uniforms: {
      uColor: { value: new THREE.Color(colour) },
      uLightDir: { value: new THREE.Vector3(1, 0, 0) },
      uIntensity: { value: intensity },
    },
    transparent: true,
    blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
    depthWrite: false,
  });
}

/** Build the Sun's corona shell. */
export function createCoronaMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: VERTEX_GLSL,
    fragmentShader: CORONA_FRAGMENT_GLSL,
    uniforms: {
      uColor: { value: new THREE.Color("#ffb247") },
      uTime: { value: 0 },
    },
    transparent: true,
    blending: THREE.AdditiveBlending,
    side: THREE.BackSide,
    depthWrite: false,
  });
}

/** Build Saturn's ring material. */
export function createRingMaterial(
  colours: [string, string],
  inner: number,
  outer: number,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    vertexShader: RING_VERTEX_GLSL,
    fragmentShader: RING_FRAGMENT_GLSL,
    uniforms: {
      uColorA: { value: new THREE.Color(colours[0]) },
      uColorB: { value: new THREE.Color(colours[1]) },
      uInner: { value: inner },
      uOuter: { value: outer },
      uLightDir: { value: new THREE.Vector3(1, 0, 0) },
    },
    transparent: true,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
}
