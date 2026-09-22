/**
 * Report whether this browser can create a WebGL context.
 *
 * Some review environments disable GPU contexts entirely, including hardened browsers, remote
 * desktops and virtual machines. Checking before a renderer is constructed prevents three.js
 * or Cesium from throwing and taking over the page.
 */
type WebGlCanvas = {
  getContext: (kind: "webgl2" | "webgl") => unknown;
};

export function webGlAvailable(createCanvas?: () => WebGlCanvas): boolean {
  if (!createCanvas && typeof document === "undefined") return false;

  try {
    const canvas = createCanvas?.() ?? document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}
