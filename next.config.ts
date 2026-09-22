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
   * Browser hardening that is safe for both renderers and the model-backed API.
   *
   * A strict CSP needs nonce plumbing because Next.js emits bootstrap scripts and Cesium uses
   * blob workers. The headers below still close framing, MIME-sniffing, referrer and ambient
   * browser-feature risks without pretending that a brittle CSP is safer two days before launch.
   */
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
          },
        ],
      },
    ];
  },

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
