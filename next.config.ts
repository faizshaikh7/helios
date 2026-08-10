import type { NextConfig } from "next";

/**
 * Where the Python science service listens during local development.
 *
 * Port 8787 rather than the conventional 8000: on Windows, 8000 and 9000 sit inside ranges
 * reserved by Hyper-V and refuse to bind with WinError 10013.
 *
 * Overridable so a different port can be used without editing this file.
 */
const SCIENCE_SERVICE_URL = process.env.SCIENCE_SERVICE_URL ?? "http://127.0.0.1:8787";

const nextConfig: NextConfig = {
  /**
   * Next.js otherwise generates AGENTS.md and CLAUDE.md at the repo root. This is a public
   * portfolio repo and those files do not belong in it.
   */
  agentRules: false,

  /**
   * Proxy `/api/*` to the Python service in development.
   *
   * The two runtimes run as separate processes locally (`next dev` plus `uvicorn`), so without
   * this the browser would hit Next.js's catch-all and get a 404 for every science endpoint.
   *
   * Development only. In production the platform owns this routing, and leaving a rewrite
   * pointing at 127.0.0.1 in a deployed build would break every API call.
   */
  async rewrites() {
    if (process.env.NODE_ENV === "production") {
      return [];
    }

    return [
      {
        source: "/api/:path*",
        destination: `${SCIENCE_SERVICE_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
