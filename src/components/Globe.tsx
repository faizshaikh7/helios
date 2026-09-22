"use client";

import { useEffect, useRef, useState } from "react";
import { webGlAvailable } from "@/lib/render/webgl";
import type { SatellitePass, TrackSample } from "@/lib/types";

/**
 * 3D globe showing an orbit, its ground track, and a ground station.
 *
 * **No Cesium Ion token required.** Cesium's default imagery and terrain come from Ion, which
 * needs an account; this uses OpenStreetMap tiles and the plain WGS84 ellipsoid instead, so the
 * globe works for anyone who clones the repo. Terrain is not needed here — the geometry that
 * matters is orbital, and the elevation mask is applied server-side.
 *
 * Cesium is imported dynamically because it touches `window` at module scope and would break
 * server rendering.
 */
export function Globe({
  samples,
  station,
  passes,
}: {
  samples: TrackSample[];
  station?: { lat: number; lon: number; name: string } | null;
  passes?: SatellitePass[];
}) {
  const container = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<{ destroy: () => void; isDestroyed: () => boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function boot() {
      if (!container.current) return;

      if (!webGlAvailable()) {
        setError("this browser cannot create a WebGL context");
        return;
      }

      try {
        // Tell Cesium where its workers and assets live before importing it.
        (window as unknown as { CESIUM_BASE_URL: string }).CESIUM_BASE_URL = "/cesium";
        const Cesium = await import("cesium");
        await import("cesium/Build/Cesium/Widgets/widgets.css");

        if (cancelled || !container.current) return;

        const viewer = new Cesium.Viewer(container.current, {
          // Everything Ion-backed is off: no token, no account, no network dependency on Cesium.
          baseLayerPicker: false,
          geocoder: false,
          homeButton: false,
          sceneModePicker: false,
          navigationHelpButton: false,
          animation: false,
          timeline: false,
          fullscreenButton: false,
          infoBox: false,
          selectionIndicator: false,
          // Cesium ships a low-resolution Natural Earth basemap in its own assets, so the globe
          // needs no Ion account, no API key, and no network -- it works offline and for anyone
          // who clones the repo. Detail is limited, which is the right trade: the geometry that
          // matters here is orbital, not cartographic.
          //
          // Addressed by URL template rather than TileMapServiceImageryProvider.fromUrl, which
          // requires a tilemapresource.xml that this asset directory does not ship. Without it
          // the provider yields no texture and the globe renders untextured -- which, with
          // lighting on, looks exactly like a failure to render at all.
          baseLayer: new Cesium.ImageryLayer(
            new Cesium.UrlTemplateImageryProvider({
              url:
                Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII") +
                "/{z}/{x}/{reverseY}.jpg",
              tilingScheme: new Cesium.GeographicTilingScheme(),
              maximumLevel: 2,
              credit: new Cesium.Credit("Natural Earth II (public domain)"),
            }),
            {},
          ),
        });

        // Lighting off. The day/night terminator is informative, but at this viewport size it
        // puts half the orbit in unreadable shadow -- and eclipse geometry has its own panel
        // with actual numbers, which serves that purpose better than a dark hemisphere.
        viewer.scene.globe.enableLighting = false;

        // Visible Earth even if tiles fail: an untextured globe with no base colour renders
        // black against a black sky and is indistinguishable from nothing having rendered.
        viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#0b2545");
        // Optional in Cesium's types because a scene can be constructed without it.
        if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = true;

        // Credits stay visible. OpenStreetMap's licence requires attribution, and this project
        // uses their tiles -- hiding the notice to tidy the corner would be taking the data
        // without crediting it.
        viewer.cesiumWidget.creditContainer.setAttribute(
          "style",
          "position:absolute;bottom:2px;left:6px;font-size:10px;opacity:.65",
        );

        viewerRef.current = viewer;

        // Development-only handle. A 3D scene cannot be debugged from the outside: when the
        // globe fails to draw, the only way to tell an imagery problem from a camera problem is
        // to interrogate the live viewer.
        if (process.env.NODE_ENV !== "production") {
          (window as unknown as { __heliosViewer?: unknown }).__heliosViewer = viewer;
        }

        setReady(true);
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : String(caught));
        }
      }
    }

    boot();

    return () => {
      cancelled = true;
      const viewer = viewerRef.current;
      if (viewer && !viewer.isDestroyed()) viewer.destroy();
      viewerRef.current = null;
    };
  }, []);

  // Redraw whenever the data changes, without rebuilding the viewer -- constructing a Cesium
  // viewer is expensive and would flash the globe on every query.
  useEffect(() => {
    const viewer = viewerRef.current as unknown as {
      entities: { removeAll: () => void; add: (o: unknown) => unknown };
      isDestroyed: () => boolean;
      flyTo: (target: unknown, options?: unknown) => void;
      zoomTo: (target: unknown) => void;
    } | null;

    if (!viewer || !ready || viewer.isDestroyed() || samples.length === 0) return;

    let cancelled = false;

    async function draw() {
      const Cesium = await import("cesium");
      if (cancelled || !viewer || viewer.isDestroyed()) return;

      viewer.entities.removeAll();

      // Orbit path at true altitude, plus the ground track projected onto the surface. Showing
      // both makes the relationship between them legible, which a single line does not.
      const orbit = samples.map((s) =>
        Cesium.Cartesian3.fromDegrees(s.lon, s.lat, s.alt_km * 1000),
      );
      const ground = samples.map((s) => Cesium.Cartesian3.fromDegrees(s.lon, s.lat, 0));

      viewer.entities.add({
        name: "Orbit",
        polyline: {
          positions: orbit,
          width: 2.5,
          material: Cesium.Color.fromCssColorString("#38bdf8"),
          arcType: Cesium.ArcType.NONE,
        },
      });

      viewer.entities.add({
        name: "Ground track",
        polyline: {
          positions: ground,
          width: 1.5,
          material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString("#38bdf8").withAlpha(0.55),
          }),
          arcType: Cesium.ArcType.NONE,
        },
      });

      // Current position: the first sample is "now" as far as the caller is concerned.
      const first = samples[0];
      viewer.entities.add({
        name: "Satellite",
        position: Cesium.Cartesian3.fromDegrees(first.lon, first.lat, first.alt_km * 1000),
        point: {
          pixelSize: 11,
          color: Cesium.Color.fromCssColorString("#fcd34d"),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 1,
        },
      });

      if (station) {
        viewer.entities.add({
          name: station.name,
          position: Cesium.Cartesian3.fromDegrees(station.lon, station.lat, 0),
          point: {
            pixelSize: 9,
            color: Cesium.Color.fromCssColorString("#34d399"),
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 1,
          },
          label: {
            text: station.name,
            font: "12px system-ui, sans-serif",
            fillColor: Cesium.Color.fromCssColorString("#34d399"),
            pixelOffset: new Cesium.Cartesian2(0, -18),
            showBackground: true,
            backgroundColor: Cesium.Color.BLACK.withAlpha(0.55),
          },
        });

        // Lines from the station to the satellite at each pass culmination: this is the
        // access geometry the pass table describes, drawn rather than tabulated.
        for (const item of (passes ?? []).slice(0, 8)) {
          const culmination = new Date(item.culmination_utc).getTime();
          const nearest = samples.reduce((best, sample) =>
            Math.abs(new Date(sample.t).getTime() - culmination) <
            Math.abs(new Date(best.t).getTime() - culmination)
              ? sample
              : best,
          );

          viewer.entities.add({
            name: `Pass, ${item.max_elevation_deg.toFixed(0)}° max`,
            polyline: {
              positions: [
                Cesium.Cartesian3.fromDegrees(station.lon, station.lat, 0),
                Cesium.Cartesian3.fromDegrees(nearest.lon, nearest.lat, nearest.alt_km * 1000),
              ],
              width: 1,
              material: Cesium.Color.fromCssColorString("#34d399").withAlpha(0.35),
              arcType: Cesium.ArcType.NONE,
            },
          });
        }
      }

      // Frame the station when there is one, otherwise the satellite.
      const focus = station
        ? Cesium.Cartesian3.fromDegrees(station.lon, station.lat, 12_000_000)
        : Cesium.Cartesian3.fromDegrees(first.lon, first.lat, 14_000_000);

      // Orientation must be explicit. A destination-only flyTo keeps the camera's current pitch,
      // which leaves it hovering above the target but staring at the horizon -- the Earth ends
      // up below the frustum and the view is a starfield with markers floating in it.
      (
        viewer as unknown as { camera: { flyTo: (o: unknown) => void } }
      ).camera.flyTo({
        destination: focus,
        orientation: {
          heading: 0,
          pitch: -Cesium.Math.PI_OVER_TWO,
          roll: 0,
        },
        duration: 1.2,
      });
    }

    draw();
    return () => {
      cancelled = true;
    };
  }, [samples, station, passes, ready]);

  return (
    <div className="relative">
      <div
        ref={container}
        className="h-[420px] w-full overflow-hidden rounded-lg border border-edge bg-background"
      />
      {error && (
        <p className="mt-2 font-mono text-xs text-danger">
          Globe unavailable: {error}. Run `npm run cesium:assets` if this persists.
        </p>
      )}
      {!ready && !error && (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-muted">
          loading globe…
        </p>
      )}
    </div>
  );
}
