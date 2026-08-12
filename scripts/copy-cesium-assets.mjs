/**
 * Copy Cesium's static assets into public/cesium.
 *
 * Cesium loads its web workers, shaders, and widget CSS at runtime from a base URL rather than
 * through the bundler, so those files have to exist as static assets. Without them the globe
 * fails at runtime with worker 404s rather than at build time, which is a slow way to find out.
 *
 * Run automatically before dev and build. The copy is skipped when it is already current, so it
 * costs nothing on repeat runs.
 */

import { cp, mkdir, readdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..");

const source = join(repoRoot, "node_modules", "cesium", "Build", "Cesium");
const destination = join(repoRoot, "public", "cesium");

/** Subdirectories Cesium fetches at runtime. */
const REQUIRED = ["Workers", "Assets", "Widgets", "ThirdParty"];

/**
 * Report whether the destination already holds every required directory.
 *
 * @returns True when nothing needs copying.
 */
async function alreadyCopied() {
  if (!existsSync(destination)) return false;

  const present = new Set(await readdir(destination));
  return REQUIRED.every((name) => present.has(name));
}

async function main() {
  if (!existsSync(source)) {
    console.error(
      "cesium build assets not found - run `npm install` first (looked in " + source + ")",
    );
    process.exit(1);
  }

  if (await alreadyCopied()) {
    console.log("cesium assets already present");
    return;
  }

  await mkdir(destination, { recursive: true });

  for (const name of REQUIRED) {
    const from = join(source, name);
    if (!existsSync(from)) continue;
    await cp(from, join(destination, name), { recursive: true });
  }

  const { size } = await stat(destination).catch(() => ({ size: 0 }));
  console.log(`copied cesium assets to public/cesium (${REQUIRED.join(", ")})`, size ? "" : "");
}

await main();
