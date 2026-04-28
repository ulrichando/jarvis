import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { randomUUID } from "node:crypto";

// Each workspace is just a directory under ~/.jarvis/workspaces/<id>/.
// Metadata (name, createdAt) lives in workspaces.json next to it.
// No DB row — these are local scratchpads, not chat artifacts.

export const WORKSPACES_ROOT =
  process.env.JARVIS_WORKSPACES_ROOT ??
  path.join(os.homedir(), ".jarvis", "workspaces");

const META_FILE = path.join(WORKSPACES_ROOT, "_meta.json");

export type Workspace = {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
};

type Meta = { workspaces: Workspace[] };

async function loadMeta(): Promise<Meta> {
  try {
    const raw = await fs.readFile(META_FILE, "utf8");
    return JSON.parse(raw);
  } catch {
    return { workspaces: [] };
  }
}

async function saveMeta(meta: Meta) {
  await fs.mkdir(WORKSPACES_ROOT, { recursive: true });
  await fs.writeFile(META_FILE, JSON.stringify(meta, null, 2));
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const meta = await loadMeta();
  return meta.workspaces.sort((a, b) => b.updatedAt - a.updatedAt);
}

export async function createWorkspace(name: string): Promise<Workspace> {
  const id = randomUUID();
  const now = Date.now();
  const ws: Workspace = { id, name: name.trim() || "untitled", createdAt: now, updatedAt: now };
  await fs.mkdir(path.join(WORKSPACES_ROOT, id), { recursive: true });
  const meta = await loadMeta();
  meta.workspaces.push(ws);
  await saveMeta(meta);
  return ws;
}

export async function getWorkspace(id: string): Promise<Workspace | null> {
  const meta = await loadMeta();
  return meta.workspaces.find((w) => w.id === id) ?? null;
}

export async function touchWorkspace(id: string) {
  const meta = await loadMeta();
  const ws = meta.workspaces.find((w) => w.id === id);
  if (!ws) return;
  ws.updatedAt = Date.now();
  await saveMeta(meta);
}

export async function deleteWorkspace(id: string) {
  const dir = path.join(WORKSPACES_ROOT, id);
  await fs.rm(dir, { recursive: true, force: true });
  const meta = await loadMeta();
  meta.workspaces = meta.workspaces.filter((w) => w.id !== id);
  await saveMeta(meta);
}

// --- Filesystem ops scoped to a single workspace ----------------

// Reject path traversal: every caller-supplied relative path must
// resolve INSIDE the workspace dir. Anything starting with `..`,
// `/`, or symlinks pointing outside is rejected.
export function workspaceRoot(id: string) {
  return path.join(WORKSPACES_ROOT, id);
}

export function resolveSafe(id: string, rel: string): string {
  const root = workspaceRoot(id);
  const cleaned = rel.replace(/^\/+/, "");
  const resolved = path.resolve(root, cleaned);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    throw new Error(`Path ${rel} escapes workspace`);
  }
  return resolved;
}

export type TreeEntry = {
  name: string;
  path: string;
  type: "file" | "dir";
  children?: TreeEntry[];
};

const IGNORE_DIRS = new Set([
  "node_modules",
  ".git",
  ".next",
  "dist",
  "build",
  ".turbo",
  ".cache",
]);

export async function readTree(id: string, rel = ""): Promise<TreeEntry[]> {
  const dir = resolveSafe(id, rel);
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const out: TreeEntry[] = [];
  for (const e of entries) {
    if (e.name.startsWith(".") && e.name !== ".env") continue;
    if (e.isDirectory() && IGNORE_DIRS.has(e.name)) continue;
    const childRel = path.posix.join(rel, e.name);
    if (e.isDirectory()) {
      out.push({ name: e.name, path: childRel, type: "dir" });
    } else if (e.isFile()) {
      out.push({ name: e.name, path: childRel, type: "file" });
    }
  }
  out.sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return out;
}

export async function readFile(id: string, rel: string): Promise<string> {
  const abs = resolveSafe(id, rel);
  return fs.readFile(abs, "utf8");
}

export async function writeFile(id: string, rel: string, content: string) {
  const abs = resolveSafe(id, rel);
  await fs.mkdir(path.dirname(abs), { recursive: true });
  await fs.writeFile(abs, content, "utf8");
  await touchWorkspace(id);
}

export async function deleteEntry(id: string, rel: string) {
  const abs = resolveSafe(id, rel);
  await fs.rm(abs, { recursive: true, force: true });
  await touchWorkspace(id);
}

export async function createEntry(id: string, rel: string, type: "file" | "dir") {
  const abs = resolveSafe(id, rel);
  if (type === "dir") {
    await fs.mkdir(abs, { recursive: true });
  } else {
    await fs.mkdir(path.dirname(abs), { recursive: true });
    await fs.writeFile(abs, "", { flag: "wx" }).catch((e) => {
      if (e.code !== "EEXIST") throw e;
    });
  }
  await touchWorkspace(id);
}
