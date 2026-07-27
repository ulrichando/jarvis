import "server-only";
import { mkdirSync, realpathSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";

// Seed-bundle store for local-only-repo teleport/ultraplan sessions.
//
// The CLI uploads `git bundle --all` output via POST /api/v1/files, passes the
// returned id as session_context.seed_bundle_file_id on session create, and
// launchContainerSession bind-mounts the file + `git clone`s it into the
// workspace — no GitHub remote needed (stock-Claude-Code bundle mode).
//
// CRITICAL deploy fact: the Next app runs INSIDE web-web-1 but launches
// per-session containers via the HOST docker daemon, so `-v <path>:...` mounts
// resolve on the HOST. Only PASSTHROUGH bind-mounts (same path host+container)
// work: /srv/jarvis/workspaces and /opt/jarvis. ~/.jarvis is a docker VOLUME
// (different host path) → a bundle stored there would silently mount an empty
// host path. So the store MUST default under the workspaces root.
export const BUNDLES_ROOT =
  process.env.JARVIS_BUNDLES_ROOT ??
  path.join(
    process.env.JARVIS_WORKSPACES_ROOT ??
      path.join(os.homedir(), ".jarvis", "workspaces"),
    ".bundles",
  );

/** Persist an uploaded bundle; returns the file id the CLI hands back on
 *  session create (as seed_bundle_file_id). */
export function writeBundle(buf: Buffer): string {
  const id = randomUUID();
  mkdirSync(BUNDLES_ROOT, { recursive: true });
  writeFileSync(path.join(BUNDLES_ROOT, `${id}.bundle`), buf);
  return id;
}

/** Host path of a stored bundle, or null when the id is invalid, the file is
 *  missing, or the path escapes BUNDLES_ROOT. Traversal-safe like
 *  workspace/storage.ts resolveSafe: validate the id as a bare segment, then
 *  lexical containment, then realpath containment (a symlink planted in the
 *  store can't point the docker mount outside the root). Returns the
 *  as-configured path (not the realpath) — it must resolve on the HOST via the
 *  passthrough workspaces mount. */
export function bundlePath(id: string): string | null {
  if (!/^[0-9a-f-]{36}$/.test(id)) return null;
  const resolved = path.resolve(BUNDLES_ROOT, `${id}.bundle`);
  if (!resolved.startsWith(BUNDLES_ROOT + path.sep)) return null;
  try {
    const realRoot = realpathSync(BUNDLES_ROOT);
    const real = realpathSync(resolved); // throws when missing
    if (real !== realRoot && !real.startsWith(realRoot + path.sep)) return null;
  } catch {
    return null;
  }
  return resolved;
}
