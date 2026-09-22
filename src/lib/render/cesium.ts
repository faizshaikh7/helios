type CesiumRuntime = typeof import("cesium");

const CESIUM_BASE_URL = "/cesium/";
const CESIUM_SCRIPT_URL = `${CESIUM_BASE_URL}Cesium.js`;
const CESIUM_STYLES_URL = `${CESIUM_BASE_URL}Widgets/widgets.css`;
const LOAD_TIMEOUT_MS = 20_000;

let cesiumLoadPromise: Promise<CesiumRuntime> | null = null;

/** Add Cesium's widget stylesheet once, before the viewer creates its controls. */
function ensureCesiumStyles(): void {
  if (document.getElementById("helios-cesium-styles")) return;

  const link = document.createElement("link");
  link.id = "helios-cesium-styles";
  link.rel = "stylesheet";
  link.href = CESIUM_STYLES_URL;
  document.head.appendChild(link);
}

/**
 * Load Cesium's browser build from the same-origin static asset directory.
 *
 * The package's ESM entry embeds WebAssembly byte strings that Turbopack 16 can emit as an
 * invalid template literal (`Octal escape sequences are not allowed in template strings`).
 * Cesium's supported prebuilt browser bundle avoids that transformation and also keeps the
 * multi-megabyte renderer out of Next.js's application chunks.
 */
export function loadCesium(): Promise<CesiumRuntime> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Cesium can only load in a browser"));
  }

  const browserWindow = window as typeof window & {
    CESIUM_BASE_URL?: string;
    Cesium?: CesiumRuntime;
  };
  if (browserWindow.Cesium) return Promise.resolve(browserWindow.Cesium);
  if (cesiumLoadPromise) return cesiumLoadPromise;

  browserWindow.CESIUM_BASE_URL = CESIUM_BASE_URL;
  ensureCesiumStyles();

  cesiumLoadPromise = new Promise<CesiumRuntime>((resolve, reject) => {
    const script = document.createElement("script");
    const timeout = window.setTimeout(() => {
      script.remove();
      reject(new Error("Cesium did not load within 20 seconds"));
    }, LOAD_TIMEOUT_MS);

    script.id = "helios-cesium-runtime";
    script.src = CESIUM_SCRIPT_URL;
    script.async = true;
    script.onload = () => {
      window.clearTimeout(timeout);
      if (browserWindow.Cesium) {
        resolve(browserWindow.Cesium);
      } else {
        reject(new Error("Cesium loaded without exposing its browser API"));
      }
    };
    script.onerror = () => {
      window.clearTimeout(timeout);
      reject(new Error("Cesium's renderer bundle could not be downloaded"));
    };
    document.head.appendChild(script);
  }).catch((error) => {
    cesiumLoadPromise = null;
    throw error;
  });

  return cesiumLoadPromise;
}
