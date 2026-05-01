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

  // Patch the dev script so it binds to 5173 + 0.0.0.0. We only rewrite
  // when the existing script clearly won't reach the host port.
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
  if (!reachesHost || !bindsAll) {
    if (hasNext) {
      patched = "next dev -p 5173 -H 0.0.0.0";
    } else if (hasVite) {
      patched = "vite --host 0.0.0.0 --port 5173";
    } else if (!original) {
      // Fall back to bun's runner — useful for plain Express/Hono
      // projects where dev runs the entry directly.
      patched = "PORT=5173 HOST=0.0.0.0 bun run --hot src/index.ts";
    } else {
      // Don't touch a dev script we don't recognize — just log it.
      patched = original;
    }
  }
  if (patched && patched !== original) {
    pkg.scripts.dev = patched;
    await fs.writeFile(pkgPath, JSON.stringify(pkg, null, 2));
  }

  // Install deps if missing. Run synchronously so the spawn below
  // doesn't race a half-installed tree.
  const cwd = "/workspace";
  const hasNodeModules = await fs
    .stat(path.join(resolveSafe(id, ""), "node_modules"))
    .then(() => true)
    .catch(() => false);
  if (!hasNodeModules) {
    await execInRuntime(id, `cd ${cwd} && bun install`, {
      timeoutMs: 180_000,
    });
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
