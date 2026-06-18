import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import {
  resolveSafe,
  workspaceRoot,
  listAllFiles,
  writeFile as writeWorkspaceFile,
} from "@/lib/workspace/storage";

// Per-turn checkpoint + rollback. Before any file-editing turn, the
// runtime snapshots the workspace's source files into
// .jarvis/checkpoints/<id>.json on the host filesystem (bind-mounted,
// so it's host-side and doesn't depend on docker). The chat UI
// surfaces an "Undo" button per assistant message that POSTs to
// restore. This is what Bolt / Replit / Lovable all ship as the
// safety net for "model broke my project, give me back the working
// version."
//
// Storage strategy: simple JSON dumps of {path → content} for every
// non-build file. Skips node_modules, .next, .git, data/*.db (binary
// + huge), .jarvis itself. Caps individual files at 256 KB to avoid
// snapshotting bundled assets.

export type Checkpoint = {
  id: string;            // matches the assistantId of the turn it precedes
  label: string;         // user-facing label, e.g. "before turn 3"
  createdAt: number;     // unix ms
  fileCount: number;
  totalBytes: number;
};

const CHECKPOINTS_REL = ".jarvis/checkpoints";
const MAX_CHECKPOINTS = 20;       // auto-prune oldest beyond this
const MAX_FILE_BYTES = 256 * 1024; // skip files bigger than this
const MAX_SNAPSHOT_BYTES = 25 * 1024 * 1024; // aggregate cap per checkpoint
const MAX_SNAPSHOT_FILES = 5000;  // secondary guard on total file count

// File / dir patterns we never snapshot. Mirrors the IGNORE_DIRS list
// in storage.ts but adds binary/build outputs that change on every run.
const SKIP_DIR_NAMES = new Set([
  "node_modules",
  ".next",
  "dist",
  "build",
  ".turbo",
  ".cache",
  ".git",
  ".jarvis",          // don't recurse into our own checkpoints
  ".pnpm-store",
]);

function shouldSkipFile(rel: string): boolean {
  // Skip data/*.db and similar binary-ish blobs. Users iterate on these,
  // and bytes are large + binary doesn't restore cleanly.
  if (/\bdata\/.*\.(db|db-(?:journal|wal|shm))$/.test(rel)) return true;
  return false;
}

async function readSourceTree(
  id: string,
  rel = "",
): Promise<{ path: string; content: string; bytes: number }[]> {
  const out: { path: string; content: string; bytes: number }[] = [];
  const root = workspaceRoot(id);
  let total = 0;
  let capped = false;
  async function walk(currentRel: string) {
    if (capped) return;
    const dir = path.join(root, currentRel);
    let entries;
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (capped) return;
      if (e.name.startsWith(".") && e.name !== ".env" && e.name !== ".gitignore") continue;
      const childRel = currentRel
        ? path.posix.join(currentRel, e.name)
        : e.name;
      if (e.isDirectory()) {
        if (SKIP_DIR_NAMES.has(e.name)) continue;
        await walk(childRel);
      } else if (e.isFile()) {
        if (shouldSkipFile(childRel)) continue;
        try {
          const stat = await fs.stat(path.join(root, childRel));
          if (stat.size > MAX_FILE_BYTES) continue;
          // Aggregate cap: stop once the snapshot would exceed the total
          // byte / file-count ceiling, so a pathological workspace can't
          // fill the disk with one checkpoint (20 are retained at a time).
          if (
            total + stat.size > MAX_SNAPSHOT_BYTES ||
            out.length >= MAX_SNAPSHOT_FILES
          ) {
            capped = true;
            console.warn(
              `[checkpoints] snapshot for ${id} hit the cap; truncated at ${out.length} files / ${total} bytes`,
            );
            return;
          }
          const content = await fs.readFile(path.join(root, childRel), "utf8");
          out.push({ path: childRel, content, bytes: stat.size });
          total += stat.size;
        } catch {
          /* unreadable / non-text — skip */
        }
      }
    }
  }
  await walk(rel);
  return out;
}

async function ensureCheckpointsDir(id: string): Promise<string> {
  const abs = resolveSafe(id, CHECKPOINTS_REL);
  await fs.mkdir(abs, { recursive: true });
  return abs;
}

/**
 * Take a snapshot of the workspace's source files. Returns the
 * checkpoint metadata. The full file map is written to disk; we only
 * surface the metadata to clients (file count + size).
 */
export async function saveCheckpoint({
  workspaceId,
  id,
  label,
}: {
  workspaceId: string;
  id: string;
  label: string;
}): Promise<Checkpoint> {
  const dir = await ensureCheckpointsDir(workspaceId);
  const files = await readSourceTree(workspaceId);
  const totalBytes = files.reduce((acc, f) => acc + f.bytes, 0);
  const cp: Checkpoint = {
    id,
    label,
    createdAt: Date.now(),
    fileCount: files.length,
    totalBytes,
  };
  // Two files per checkpoint: <id>.meta.json (lightweight, listable)
  // and <id>.files.json (the actual snapshot, only read on restore).
  await fs.writeFile(
    path.join(dir, `${id}.meta.json`),
    JSON.stringify(cp, null, 2),
  );
  await fs.writeFile(
    path.join(dir, `${id}.files.json`),
    JSON.stringify(
      Object.fromEntries(files.map((f) => [f.path, f.content])),
    ),
  );
  // Auto-prune old checkpoints. Sort by createdAt asc, drop everything
  // beyond MAX_CHECKPOINTS.
  const all = await listCheckpoints(workspaceId);
  if (all.length > MAX_CHECKPOINTS) {
    const toDelete = all
      .sort((a, b) => a.createdAt - b.createdAt)
      .slice(0, all.length - MAX_CHECKPOINTS);
    for (const old of toDelete) {
      await fs
        .rm(path.join(dir, `${old.id}.meta.json`), { force: true })
        .catch(() => {});
      await fs
        .rm(path.join(dir, `${old.id}.files.json`), { force: true })
        .catch(() => {});
    }
  }
  return cp;
}

export async function listCheckpoints(
  workspaceId: string,
): Promise<Checkpoint[]> {
  const dir = await ensureCheckpointsDir(workspaceId);
  let entries: string[];
  try {
    entries = await fs.readdir(dir);
  } catch {
    return [];
  }
  const out: Checkpoint[] = [];
  for (const f of entries) {
    if (!f.endsWith(".meta.json")) continue;
    try {
      const text = await fs.readFile(path.join(dir, f), "utf8");
      out.push(JSON.parse(text) as Checkpoint);
    } catch {
      /* skip unreadable */
    }
  }
  return out.sort((a, b) => b.createdAt - a.createdAt);
}

/**
 * Restore the workspace to a previously-saved checkpoint:
 *   - Files in the snapshot get their old content written back.
 *   - Files NOT in the snapshot but PRESENT now get deleted (covers
 *     the case where the model created brand-new files this turn that
 *     the user wants to undo).
 *   - Skipped paths (node_modules, .git, .next, data/*.db) are left
 *     alone — they're either rebuildable or user data.
 */
export async function restoreCheckpoint({
  workspaceId,
  id,
}: {
  workspaceId: string;
  id: string;
}): Promise<{ restored: number; deleted: number }> {
  const dir = await ensureCheckpointsDir(workspaceId);
  const filesPath = path.join(dir, `${id}.files.json`);
  const raw = await fs.readFile(filesPath, "utf8");
  const snapshot = JSON.parse(raw) as Record<string, string>;
  // Restore every file from the snapshot.
  let restored = 0;
  for (const [rel, content] of Object.entries(snapshot)) {
    await writeWorkspaceFile(workspaceId, rel, content);
    restored += 1;
  }
  // Delete files that exist NOW but didn't in the snapshot. Use the
  // workspace's current file list for that diff.
  const currentFiles = await listAllFiles(workspaceId);
  const snapshotPaths = new Set(Object.keys(snapshot));
  let deleted = 0;
  for (const cur of currentFiles) {
    if (snapshotPaths.has(cur)) continue;
    if (shouldSkipFile(cur)) continue;
    try {
      await fs.rm(resolveSafe(workspaceId, cur), { force: true });
      deleted += 1;
    } catch {
      /* skip */
    }
  }
  return { restored, deleted };
}
