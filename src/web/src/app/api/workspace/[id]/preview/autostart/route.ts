import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import {
  dockerStatus,
  execInRuntime,
  getRuntime,
  spawnDetached,
  startRuntime,
} from "@/lib/workspace/docker";
import { resolveSafe } from "@/lib/workspace/storage";

export const runtime = "nodejs";

/**
 * POST /api/workspace/[id]/preview/autostart
 *
 * Boot the dev server inside the workspace's container without making
 * the user type anything. The Preview tab fires this when:
 *   - the runtime is up, AND
 *   - nothing is yet listening on the exposed port (5173)
 *
 * What it does, in order:
 *   1. Detect the framework from package.json (next / vite / generic).
 *   2. Patch the `dev` script if it doesn't bind to 0.0.0.0:5173. The
 *      workbench container only exposes port 5173 to the host — any
 *      other port and the Preview tab can't see it.
 *   3. Run `bun install` if node_modules is missing.
 *   4. Spawn `bun run dev` detached. The Preview tab's polling picks
 *      up the new listener within 2s.
 */
export async function POST(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/preview/autostart">,
) {
  const { id } = await ctx.params;

  const status = await dockerStatus();
  if (!status.available || !status.imageReady) {
    return NextResponse.json(
      { ok: false, reason: "docker_unavailable" },
      { status: 503 },
    );
  }

  let rt = await getRuntime(id);
  if (rt.state !== "running") {
    rt = await startRuntime(id);
  }

  // Read package.json off disk so we can detect framework + patch the
  // dev script. Fail gracefully if there isn't one yet — caller should
  // tell the user to scaffold a project first.
  const pkgPath = resolveSafe(id, "package.json");
  let pkg: {
    name?: string;
    scripts?: Record<string, string>;
    dependencies?: Record<string, string>;
    devDependencies?: Record<string, string>;
  };
  try {
    const raw = await fs.readFile(pkgPath, "utf8");
    pkg = JSON.parse(raw);
  } catch {
    return NextResponse.json(
      { ok: false, reason: "no_package_json" },
      { status: 400 },
    );
  }

  // Framework detection — for the dev-script fix below.
  const allDeps = { ...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {}) };
  const hasNext = Boolean(allDeps["next"]);
  const hasVite = Boolean(allDeps["vite"]);

  // Patch the dev script so it binds to 5173 + 0.0.0.0 AND has polling
  // file-watch env vars. We rewrite when ANY of: missing port, missing
  // host bind, or missing polling vars (the third one being the Docker
  // bind-mount hot-reload fix).
  pkg.scripts = pkg.scripts ?? {};
  const original = pkg.scripts.dev ?? "";
  let patched = original;
  const reachesHost =
    /\b(?:-p|--port)\s+5173\b/.test(original) ||
    /\bPORT\s*=\s*5173\b/.test(original);
  const bindsAll =
    /\b(?:-H|--host(?:name)?)\b\s*0?\.?0?\.?0?\.?0?\.?0\.0\.0\.0/.test(original) ||
    /--host\s+0\.0\.0\.0/.test(original) ||
    /-H\s+0\.0\.0\.0/.test(original);
  const hasPolling =
    /CHOKIDAR_USEPOLLING\s*=\s*true/.test(original) ||
    /WATCHPACK_POLLING\s*=\s*true/.test(original);
  if (!reachesHost || !bindsAll || !hasPolling) {
    // Polling env vars are inlined into the script directly. Setting them
    // via `docker exec -e` or shell `export` was getting lost between bun
    // and next dev (likely bun's env normalization). Putting them here in
    // the script string is the only reliable way to guarantee they reach
    // the actual file watcher inside the dev server. Without polling, the
    // dev server doesn't see file edits across the Docker bind mount, so
    // jarvis's writes don't trigger hot-reload.
    const POLL = "CHOKIDAR_USEPOLLING=true CHOKIDAR_INTERVAL=300 WATCHPACK_POLLING=true NEXT_TELEMETRY_DISABLED=1";
    if (hasNext) {
      patched = `${POLL} next dev -p 5173 -H 0.0.0.0`;
    } else if (hasVite) {
      patched = `${POLL} vite --host 0.0.0.0 --port 5173`;
    } else if (!original) {
      // Fall back to bun's runner — useful for plain Express/Hono
      // projects where dev runs the entry directly.
      patched = `PORT=5173 HOST=0.0.0.0 ${POLL} bun run --hot src/index.ts`;
    } else {
      // Don't touch a dev script we don't recognize — just log it.
      patched = original;
    }
  }
  if (patched && patched !== original) {
    pkg.scripts.dev = patched;
    await fs.writeFile(pkgPath, JSON.stringify(pkg, null, 2));
  }

  // For Next.js projects, also drop a next.config.js with explicit
  // webpack.watchOptions polling. Stack Overflow + GitHub issues
  // consensus: Next 13/14 inside Docker bind mounts ignores
  // CHOKIDAR_USEPOLLING / WATCHPACK_POLLING env vars in many cases;
  // the only 100% reliable fix is config-level webpack watchOptions.
  // We only write it if no next.config.* already exists — never
  // overwrite the user's config.
  if (hasNext) {
    const candidates = [
      "next.config.js",
      "next.config.mjs",
      "next.config.ts",
      "next.config.cjs",
    ];
    let configExists = false;
    for (const name of candidates) {
      try {
        await fs.access(resolveSafe(id, name));
        configExists = true;
        break;
      } catch {
        /* doesn't exist, keep looking */
      }
    }
    if (!configExists) {
      const configPath = resolveSafe(id, "next.config.js");
      const configBody = `// Auto-generated by JARVIS workbench autostart for Docker bind-mount
// hot-reload reliability. The env vars CHOKIDAR_USEPOLLING /
// WATCHPACK_POLLING aren't always honored by Next 14's webpack watcher,
// so we set the polling options at the config level too. Safe to edit /
// extend with your own settings; jarvis won't overwrite a config that
// already exists.
module.exports = {
  reactStrictMode: true,
  webpack: (config) => {
    config.watchOptions = {
      poll: 1000,
      aggregateTimeout: 300,
      ignored: ['**/node_modules/**', '**/.next/**', '**/.jarvis/**'],
    };
    return config;
  },
};
`;
      await fs.writeFile(configPath, configBody);
    }
  }

  // Install deps if missing. Run synchronously so the spawn below
  // doesn't race a half-installed tree.
  const cwd = "/workspace";
  const hasNodeModules = await fs
    .stat(path.join(resolveSafe(id, ""), "node_modules"))
    .then(() => true)
    .catch(() => false);
  if (!hasNodeModules) {
    const inst = await execInRuntime(id, `cd ${cwd} && bun install`, {
      timeoutMs: 180_000,
    });
    if (inst.exitCode !== 0) {
      // Don't claim success when deps didn't install — the dev server
      // would just crash-loop and the Preview tab would spin forever.
      // Surface the failure so the UI can show a real error + the tail
      // of the install log.
      return NextResponse.json(
        {
          ok: false,
          reason: "install_failed",
          details: (inst.stdout + "\n" + inst.stderr).slice(-2000),
        },
        { status: 200 },
      );
    }
  }

  // Spawn the dev server detached. The Preview tab polls /preview every
  // 2s and will pick it up on the next tick.
  const result = await spawnDetached(id, `cd ${cwd} && bun run dev`);

  return NextResponse.json({
    ok: true,
    devScript: pkg.scripts.dev,
    patched: patched !== original,
    execId: result.execId,
  });
}
