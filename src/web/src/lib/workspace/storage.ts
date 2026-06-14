import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";
import { randomUUID } from "node:crypto";
import { gitInit } from "./git";

// Each workspace is just a directory under ~/.jarvis/workspaces/<id>/.
// Metadata (name, createdAt) lives in workspaces.json next to it.
// No DB row — these are local scratchpads, not chat artifacts.

export const WORKSPACES_ROOT =
  process.env.JARVIS_WORKSPACES_ROOT ??
  path.join(os.homedir(), ".jarvis", "workspaces");

const META_FILE = path.join(WORKSPACES_ROOT, "_meta.json");

export type WorkspaceKind = "design" | "workbench";

export type Workspace = {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  /** Origin tab. Determines which tab's project picker lists it.
   *  Legacy workspaces with no `kind` are treated as "design" (the
   *  original Workspace tab was used purely for design mocks). */
  kind?: WorkspaceKind;
  /** Most-recent conversation for this workspace. Persisted server-
   *  side (in this _meta.json) so refresh / close / different-browser
   *  all show the same chat history. Without this the link lives only
   *  in localStorage which empties when the user clears site data or
   *  opens the workspace from another device. */
  conversationId?: string;
  /** Workspace-scoped system-prompt addendum. Same role as Cursor's
   *  `.cursorrules` or Claude Code's `CLAUDE.md` — gets appended to
   *  the system prompt for every chat turn in this workspace. Stays
   *  small (we trim at 8K chars on save) so it doesn't dominate the
   *  context window. */
  customInstructions?: string;
  /** Workspace-scoped environment variables — exposed to the sandbox
   *  on container start. Keys are uppercased on save; values are
   *  stored as-is. SECRET-CLASS values (anything looking like a key,
   *  token, password, dsn) get masked in API responses by default;
   *  the editor must opt in to "reveal" to see them. */
  envVars?: Record<string, string>;
  /** Override for the dev-server start command. When set, replaces
   *  the default `bun run dev`. Must bind 0.0.0.0:5173 — the
   *  workbench container only exposes that port to the host. */
  devCommand?: string;
  /** Deploy target configuration. Currently Vercel-only; expanding to
   *  Netlify / Cloudflare Pages / Fly is a `provider` switch + a new
   *  adapter under lib/deploy/. The token itself is NOT stored here —
   *  it lives in envVars (as VERCEL_TOKEN) so the secret-masking
   *  pipeline applies the same way it does to runtime env vars. */
  deploy?: {
    provider: "vercel";
    /** Vercel team scope. Optional — null/undefined uses the personal
     *  account associated with VERCEL_TOKEN. */
    teamId?: string;
    /** Vercel project ID (vc_xxx). Created lazily on the first deploy
     *  if absent. Stable across deploys for the same workspace. */
    projectId?: string;
    /** Vercel project NAME — what shows up in the dashboard. Defaults
     *  to the workspace name on first create; user can override. */
    projectName?: string;
    /** Latest deployment ID + URL, cached so the UI doesn't have to
     *  hit the API on every render. */
    latestDeploymentId?: string;
    productionUrl?: string;
  };
  /** Authentication config for the deployed app. The "Scaffold Auth"
   *  button writes next-auth boilerplate based on this — it's a config
   *  store, not the live auth layer. */
  auth?: {
    providers: Array<"credentials" | "magic-link" | "google" | "github">;
    sessionMins: number;
    cookieSecure: boolean;
    cookieSameSite: "lax" | "strict" | "none";
    /** Set true after the scaffold endpoint has written next-auth files
     *  to the workspace; the UI uses this to label "Scaffolded" vs
     *  "Configure". */
    scaffolded?: boolean;
  };
  /** Read-only public share link. The token is unguessable; the public
   *  /share/<token> page renders ONLY the deployed site (if any) — never
   *  source files or secrets. Expires, and the owner can revoke. */
  share?: {
    token: string;
    createdAt: number;
    expiresAt: number;
  };
};

type Meta = { workspaces: Workspace[] };

const META_BAK = META_FILE + ".bak";
const META_TMP = META_FILE + ".tmp";

async function loadMeta(): Promise<Meta> {
  let primaryErr: NodeJS.ErrnoException | undefined;
  try {
    return JSON.parse(await fs.readFile(META_FILE, "utf8")) as Meta;
  } catch (e) {
    primaryErr = e as NodeJS.ErrnoException;
  }
  // Primary missing or corrupt. Try the backup before giving up so a torn
  // write / truncated file doesn't orphan every workspace — _meta.json is
  // the single source of truth and is not otherwise backed up.
  try {
    const recovered = JSON.parse(await fs.readFile(META_BAK, "utf8")) as Meta;
    if (primaryErr?.code !== "ENOENT") {
      console.warn(
        "[workspace] _meta.json unreadable; recovered from .bak:",
        primaryErr?.message,
      );
    }
    return recovered;
  } catch {
    // ENOENT on the primary is normal (first run). Anything else is real
    // corruption with no usable backup — surface it loudly rather than
    // silently presenting an empty workspace list.
    if (primaryErr && primaryErr.code !== "ENOENT") {
      console.error(
        "[workspace] _meta.json AND .bak unreadable; starting empty:",
        primaryErr.message,
      );
    }
    return { workspaces: [] };
  }
}

async function saveMeta(meta: Meta) {
  await fs.mkdir(WORKSPACES_ROOT, { recursive: true });
  const data = JSON.stringify(meta, null, 2);
  // Atomic write: write a temp file then rename over the target, so a
  // crash mid-write can never leave a half-written _meta.json. Back up
  // the previous good file first (best-effort) so loadMeta can recover.
  await fs.writeFile(META_TMP, data);
  try {
    await fs.copyFile(META_FILE, META_BAK);
  } catch {
    /* no prior file on first write — nothing to back up */
  }
  await fs.rename(META_TMP, META_FILE);
}

// Serialize every read-modify-write of _meta.json. All mutating helpers
// below run their load→mutate→save through this single promise chain so
// two concurrent requests can't clobber each other's changes (the file
// is the single source of truth and has no transactions of its own).
// Readers (listWorkspaces / getWorkspace) don't need the lock: the atomic
// rename means a reader sees either the old or the new complete file,
// never a torn one.
let metaWriteChain: Promise<unknown> = Promise.resolve();
function withMetaLock<T>(fn: () => Promise<T>): Promise<T> {
  const result = metaWriteChain.then(fn, fn);
  // Keep the chain alive regardless of this op's outcome; swallow here so
  // one failed op doesn't reject the next op waiting on the chain.
  metaWriteChain = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const meta = await loadMeta();
  return meta.workspaces.sort((a, b) => b.updatedAt - a.updatedAt);
}

/**
 * Tab-scoped listing.
 * - kind="design" returns design workspaces (legacy `kind === undefined`
 *   counts as design too — original tab was design-only).
 * - kind="workbench" returns ONLY workspaces explicitly tagged workbench.
 *   Legacy untagged workspaces stay out so existing design projects
 *   don't bleed into the new Workbench list.
 */
export async function listWorkspacesOfKind(
  kind: WorkspaceKind,
): Promise<Workspace[]> {
  const all = await listWorkspaces();
  if (kind === "design") return all.filter((w) => w.kind !== "workbench");
  return all.filter((w) => w.kind === "workbench");
}

export async function createWorkspace(
  name: string,
  kind: WorkspaceKind = "design",
): Promise<Workspace> {
  const id = randomUUID();
  const now = Date.now();
  const ws: Workspace = {
    id,
    name: name.trim() || "untitled",
    createdAt: now,
    updatedAt: now,
    kind,
  };
  await fs.mkdir(path.join(WORKSPACES_ROOT, id), { recursive: true });
  await withMetaLock(async () => {
    const meta = await loadMeta();
    meta.workspaces.push(ws);
    await saveMeta(meta);
  });
  // Init git so every workspace is a real repo from turn 1. Failures
  // here aren't fatal — the workspace still works without git, the
  // commit endpoint will retry init on first commit. Most likely cause
  // of failure is missing `git` binary, which is rare on dev machines.
  try {
    await gitInit(id);
  } catch (err) {
    console.warn("[workspace] git init failed:", err);
  }
  return ws;
}

export async function getWorkspace(id: string): Promise<Workspace | null> {
  const meta = await loadMeta();
  return meta.workspaces.find((w) => w.id === id) ?? null;
}

export async function touchWorkspace(id: string) {
  await withMetaLock(async () => {
    const meta = await loadMeta();
    const ws = meta.workspaces.find((w) => w.id === id);
    if (!ws) return;
    ws.updatedAt = Date.now();
    await saveMeta(meta);
  });
}

/**
 * Pin the conversation for a workspace. Called from the chat route on
 * every workspace-scoped turn so refresh / browser-close / different-
 * device all resolve the same chat history without relying on
 * localStorage. Idempotent — repeated calls with the same id no-op.
 */
export async function setWorkspaceConversation(
  workspaceId: string,
  conversationId: string,
) {
  await withMetaLock(async () => {
    const meta = await loadMeta();
    const ws = meta.workspaces.find((w) => w.id === workspaceId);
    if (!ws) return;
    if (ws.conversationId === conversationId) return;
    ws.conversationId = conversationId;
    ws.updatedAt = Date.now();
    await saveMeta(meta);
  });
}

export async function renameWorkspace(id: string, name: string): Promise<Workspace | null> {
  const trimmed = name.trim();
  if (!trimmed) return null;
  return withMetaLock(async () => {
    const meta = await loadMeta();
    const ws = meta.workspaces.find((w) => w.id === id);
    if (!ws) return null;
    ws.name = trimmed;
    ws.updatedAt = Date.now();
    await saveMeta(meta);
    return ws;
  });
}

/**
 * Generic workspace-meta updater. Accepts a partial patch + applies
 * field-level validation: customInstructions trims to 8K, envVars
 * uppercases keys, devCommand trims. Returns the updated workspace
 * or null if not found.
 */
export async function updateWorkspaceMeta(
  id: string,
  patch: {
    customInstructions?: string;
    envVars?: Record<string, string>;
    /** Explicit env-var deletions. The editor can't round-trip masked
     *  secret values, so deletes come through here instead of by sending
     *  a full envVars map that omits them (which used to silently drop
     *  every unrevealed secret). */
    removeEnvKeys?: string[];
    devCommand?: string;
    deploy?: Workspace["deploy"];
    auth?: Workspace["auth"];
  },
): Promise<Workspace | null> {
  return withMetaLock(async () => {
    const meta = await loadMeta();
    const ws = meta.workspaces.find((w) => w.id === id);
    if (!ws) return null;
    if (typeof patch.customInstructions === "string") {
      const trimmed = patch.customInstructions.slice(0, 8192);
      ws.customInstructions = trimmed.length > 0 ? trimmed : undefined;
    }
    // Env vars: MERGE into the existing set (never wipe keys the caller
    // didn't send). Secret values are masked in API responses, so the
    // editor literally cannot round-trip them — replacing wholesale would
    // silently drop every unrevealed secret. Deletions come through the
    // explicit removeEnvKeys list.
    if (
      (patch.envVars && typeof patch.envVars === "object") ||
      Array.isArray(patch.removeEnvKeys)
    ) {
      const merged: Record<string, string> = { ...(ws.envVars ?? {}) };
      if (patch.envVars && typeof patch.envVars === "object") {
        for (const [k, v] of Object.entries(patch.envVars)) {
          const key = String(k).trim().toUpperCase();
          // Reject empty keys + keys with shell-unsafe chars (newlines,
          // equals, quotes). Docker --env doesn't permit them either.
          if (!key || !/^[A-Z_][A-Z0-9_]*$/.test(key)) continue;
          const val = String(v ?? "");
          if (val.length > 4096) continue;
          merged[key] = val;
        }
      }
      if (Array.isArray(patch.removeEnvKeys)) {
        for (const rk of patch.removeEnvKeys) {
          delete merged[String(rk).trim().toUpperCase()];
        }
      }
      ws.envVars = Object.keys(merged).length > 0 ? merged : undefined;
    }
    if (typeof patch.devCommand === "string") {
      const cmd = patch.devCommand.trim().slice(0, 512);
      ws.devCommand = cmd.length > 0 ? cmd : undefined;
    }
    if (patch.deploy !== undefined) {
      // Caller can pass null to clear, or a partial to merge. Validate
      // provider explicitly so a typo doesn't poison the meta.
      if (patch.deploy === null) {
        ws.deploy = undefined;
      } else if (patch.deploy.provider === "vercel") {
        ws.deploy = {
          ...(ws.deploy ?? {}),
          ...patch.deploy,
          provider: "vercel",
        };
      }
    }
    if (patch.auth !== undefined) {
      if (patch.auth === null) {
        ws.auth = undefined;
      } else {
        // Validate provider list — drop any unknown values silently.
        const validProviders: Array<
          "credentials" | "magic-link" | "google" | "github"
        > = ["credentials", "magic-link", "google", "github"];
        const providers = Array.isArray(patch.auth.providers)
          ? patch.auth.providers.filter((p) => validProviders.includes(p))
          : ws.auth?.providers ?? [];
        const sessionMins = Number.isFinite(patch.auth.sessionMins)
          ? Math.max(5, Math.min(43200, Math.floor(patch.auth.sessionMins)))
          : ws.auth?.sessionMins ?? 1440;
        const cookieSameSite =
          patch.auth.cookieSameSite === "strict" ||
          patch.auth.cookieSameSite === "none" ||
          patch.auth.cookieSameSite === "lax"
            ? patch.auth.cookieSameSite
            : ws.auth?.cookieSameSite ?? "lax";
        ws.auth = {
          providers,
          sessionMins,
          cookieSecure: !!patch.auth.cookieSecure,
          cookieSameSite,
          scaffolded: patch.auth.scaffolded ?? ws.auth?.scaffolded,
        };
      }
    }
    ws.updatedAt = Date.now();
    await saveMeta(meta);
    return ws;
  });
}

// Heuristic: which env-var values should be masked in API responses.
// Anything containing a JWT-ish blob, a hex >32 chars, or matching a
// common secret key name. Conservative — when in doubt, mask.
const SECRET_KEY_PATTERN =
  /(KEY|TOKEN|SECRET|PASSWORD|PWD|API|DSN|URL|CONNECTION)$/;

export function isLikelySecret(key: string, value: string): boolean {
  if (SECRET_KEY_PATTERN.test(key)) return true;
  if (/^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}$/.test(value))
    return true;
  if (/^[a-fA-F0-9]{32,}$/.test(value)) return true;
  if (/^[A-Z][A-Z0-9_]+:\/\//.test(value) && value.includes("@"))
    return true; // postgres://user:pw@host
  return false;
}

export function maskEnvVars(
  envVars: Record<string, string> | undefined,
): Record<string, { value: string; masked: boolean }> {
  if (!envVars) return {};
  const out: Record<string, { value: string; masked: boolean }> = {};
  for (const [k, v] of Object.entries(envVars)) {
    const masked = isLikelySecret(k, v);
    out[k] = {
      value: masked ? "••••••••" : v,
      masked,
    };
  }
  return out;
}

// ── Public share links ─────────────────────────────────────────────────

const SHARE_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

/** Mint (or refresh) a read-only share token for a workspace. */
export async function setShareToken(
  id: string,
  ttlMs = SHARE_TTL_MS,
): Promise<Workspace["share"] | null> {
  return withMetaLock(async () => {
    const meta = await loadMeta();
    const ws = meta.workspaces.find((w) => w.id === id);
    if (!ws) return null;
    const now = Date.now();
    ws.share = {
      token: randomUUID().replace(/-/g, ""),
      createdAt: now,
      expiresAt: now + ttlMs,
    };
    ws.updatedAt = now;
    await saveMeta(meta);
    return ws.share;
  });
}

/** Revoke a workspace's share link. */
export async function clearShareToken(id: string) {
  await withMetaLock(async () => {
    const meta = await loadMeta();
    const ws = meta.workspaces.find((w) => w.id === id);
    if (!ws || !ws.share) return;
    ws.share = undefined;
    ws.updatedAt = Date.now();
    await saveMeta(meta);
  });
}

/** Resolve a workspace by an active (non-expired) share token. Returns
 *  null for unknown or expired tokens. Read-only — no lock needed. */
export async function getWorkspaceByShareToken(
  token: string,
): Promise<Workspace | null> {
  if (!token) return null;
  const meta = await loadMeta();
  const ws = meta.workspaces.find((w) => w.share?.token === token);
  if (!ws || !ws.share) return null;
  if (ws.share.expiresAt < Date.now()) return null;
  return ws;
}

export async function deleteWorkspace(id: string) {
  const dir = path.join(WORKSPACES_ROOT, id);
  await fs.rm(dir, { recursive: true, force: true });
  await withMetaLock(async () => {
    const meta = await loadMeta();
    meta.workspaces = meta.workspaces.filter((w) => w.id !== id);
    await saveMeta(meta);
  });
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

/**
 * Flat list of every file path inside a workspace, recursive. Skips the
 * usual heavy/build dirs (node_modules etc.) and dotfiles. Used by the
 * chat route to tell the model what files already exist when the user
 * is iterating on a design — without it, the model treats every turn
 * as a fresh ask and writes brand-new unrelated files.
 */
export async function listAllFiles(
  id: string,
  rel = "",
  acc: string[] = [],
): Promise<string[]> {
  const dir = resolveSafe(id, rel);
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const e of entries) {
    if (e.name.startsWith(".") && e.name !== ".env") continue;
    if (e.isDirectory() && IGNORE_DIRS.has(e.name)) continue;
    const childRel = path.posix.join(rel, e.name);
    if (e.isDirectory()) {
      await listAllFiles(id, childRel, acc);
    } else if (e.isFile()) {
      acc.push(childRel);
    }
  }
  return acc;
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
