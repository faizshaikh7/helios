"use client";

import type { TrackSample } from "@/lib/types";

const WIDTH = 720;
const HEIGHT = 360;

/** Equirectangular projection: longitude maps linearly to x, latitude to y. */
function project(lat: number, lon: number): [number, number] {
  return [((lon + 180) / 360) * WIDTH, ((90 - lat) / 180) * HEIGHT];
}

/**
 * Split a track wherever it crosses the antimeridian.
 *
 * Longitude jumps from +180 to -180 mid-orbit. Drawing that as one polyline would streak a
 * horizontal line straight across the map, which reads as a real path rather than a wrap.
 *
 * @param samples - Track samples in time order.
 * @returns Contiguous segments, each safe to draw as a single polyline.
 */
function splitAtAntimeridian(samples: TrackSample[]): TrackSample[][] {
  const segments: TrackSample[][] = [];
  let current: TrackSample[] = [];

  for (let i = 0; i < samples.length; i += 1) {
    if (i > 0 && Math.abs(samples[i].lon - samples[i - 1].lon) > 180) {
      segments.push(current);
      current = [];
    }
    current.push(samples[i]);
  }

  if (current.length) segments.push(current);
  return segments;
}

/**
 * Ground track plotted on an equirectangular graticule.
 *
 * Deliberately a graticule rather than a basemap: there is no coastline dataset in the bundle,
 * and inventing one would be worse than plotting honestly against latitude and longitude.
 *
 * @param samples - Sub-satellite points over time.
 * @param station - Optional ground station to mark.
 * @param current - Optional current sub-satellite point.
 */
export function GroundTrack({
  samples,
  station,
  current,
}: {
  samples: TrackSample[];
  station?: { lat: number; lon: number; name: string } | null;
  current?: { lat: number; lon: number } | null;
}) {
  const segments = splitAtAntimeridian(samples);

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      className="w-full rounded-lg border border-white/10 bg-black"
      role="img"
      aria-label="Satellite ground track on an equirectangular map"
    >
      {/* Graticule every 30 degrees. */}
      {[-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150].map((lon) => (
        <line
          key={`lon-${lon}`}
          x1={project(0, lon)[0]}
          y1={0}
          x2={project(0, lon)[0]}
          y2={HEIGHT}
          stroke={lon === 0 ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.06)"}
          strokeWidth={1}
        />
      ))}
      {[-60, -30, 0, 30, 60].map((lat) => (
        <line
          key={`lat-${lat}`}
          x1={0}
          y1={project(lat, 0)[1]}
          x2={WIDTH}
          y2={project(lat, 0)[1]}
          stroke={lat === 0 ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.06)"}
          strokeWidth={1}
        />
      ))}

      {/* Equator label, so the projection is readable without a legend. */}
      <text x={6} y={project(0, 0)[1] - 6} className="fill-zinc-600" fontSize={10}>
        equator
      </text>

      {segments.map((segment, index) => (
        <polyline
          key={index}
          fill="none"
          stroke="rgb(56 189 248)"
          strokeWidth={1.8}
          strokeOpacity={0.9}
          points={segment.map((s) => project(s.lat, s.lon).join(",")).join(" ")}
        />
      ))}

      {station && (
        <g>
          <circle
            cx={project(station.lat, station.lon)[0]}
            cy={project(station.lat, station.lon)[1]}
            r={4}
            className="fill-emerald-400"
          />
          <text
            x={project(station.lat, station.lon)[0] + 8}
            y={project(station.lat, station.lon)[1] + 4}
            className="fill-emerald-300"
            fontSize={11}
          >
            {station.name}
          </text>
        </g>
      )}

      {current && (
        <g>
          <circle
            cx={project(current.lat, current.lon)[0]}
            cy={project(current.lat, current.lon)[1]}
            r={5}
            className="fill-amber-300"
          />
          <circle
            cx={project(current.lat, current.lon)[0]}
            cy={project(current.lat, current.lon)[1]}
            r={11}
            className="fill-none stroke-amber-300/40"
            strokeWidth={1.5}
          />
        </g>
      )}
    </svg>
  );
}
