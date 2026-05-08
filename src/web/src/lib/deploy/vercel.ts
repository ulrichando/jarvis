import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import { workspaceRoot } from "@/lib/workspace/storage";

// Vercel REST API client — minimal surface. Only the endpoints the
// workbench Settings tab actually consumes:
//   - createProject       → first-time setup
//   - createDeployment    → ship the workspace
//   - listDeployments     → history feed
//   - getDeployment       → poll status of an in-flight deploy
//   - addDomain           → custom domain attach
//   - listDomains         → show what's connected
//   - removeDomain        → detach
//
// Auth via Bearer token (the user's VERCEL_TOKEN env var). Team scope
// passed as ?teamId= when the workspace's deploy config has one.
//
// Reference: https://vercel.com/docs/rest-api

const VERCEL_API = "https://api.vercel.com";

type VercelInit = {
  token: string;
  teamId?: string;
};

function teamSuffix(teamId?: string): string {
  return teamId ? `?teamId=${encodeURIComponent(teamId)}` : "";
}

function teamAndExtra(teamId: string | undefined, extra?: string): string {
  if (!teamId && !extra) return "";
  const params = new URLSearchParams();
  if (teamId) params.set("teamId", teamId);
  if (extra) {
    const x = new URLSearchParams(extra);
    for (const [k, v] of x.entries()) params.set(k, v);
  }
  return `?${params.toString()}`;
}

async function vercelFetch<T>(
  init: VercelInit,
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const r = await fetch(`${VERCEL_API}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${init.token}`,
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });
  const text = await r.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    /* non-json body — leave json null and surface text below */
  }
  if (!r.ok) {
    const j = json as { error?: { message?: string; code?: string } } | null;
    const message = j?.error?.message ?? text ?? r.statusText;
    throw new Error(`Vercel ${r.status}: ${message}`);
  }
  return json as T;
}

// ── Project ────────────────────────────────────────────────────────────

export type VercelProject = {
  id: string;
  name: string;
  framework?: string | null;
};

export async function createProject(
  init: VercelInit,
  args: { name: string; framework?: string | null },
): Promise<VercelProject> {
  return vercelFetch<VercelProject>(init, `/v10/projects${teamSuffix(init.teamId)}`, {
    method: "POST",
    body: JSON.stringify({
      name: args.name,
      framework: args.framework ?? null,
    }),
  });
}

export async function getProject(
  init: VercelInit,
  idOrName: string,
): Promise<VercelProject | null> {
  try {
    return await vercelFetch<VercelProject>(
      init,
      `/v10/projects/${encodeURIComponent(idOrName)}${teamSuffix(init.teamId)}`,
    );
  } catch (err) {
    if (String(err).includes("404")) return null;
    throw err;
  }
}

// ── Deployments ────────────────────────────────────────────────────────

export type VercelDeployment = {
  uid: string;
  url: string;
  state:
    | "BUILDING"
    | "QUEUED"
    | "READY"
    | "ERROR"
    | "CANCELED"
    | "INITIALIZING";
  createdAt: number;
  target?: string | null;
  inspectorUrl?: string;
};

/**
 * Create a deployment by uploading the workspace files inline. Vercel's
 * /v13/deployments accepts a `files` array of `{ file, data }` entries
 * where `data` is base64. Suitable for projects under ~100MB.
 *
 * Skips: node_modules, .next, dist, .git, .jarvis (build artifacts +
 * local-only state). Also skips any file > 8MB to avoid hitting the
 * per-deployment size cap.
 */
export async function createDeployment(
  init: VercelInit,
  args: {
    projectId: string;
    projectName: string;
    workspaceId: string;
    target?: "production" | "preview";
  },
): Promise<VercelDeployment> {
  const root = workspaceRoot(args.workspaceId);
  const files = await collectFiles(root);
  return vercelFetch<VercelDeployment>(
    init,
    `/v13/deployments${teamSuffix(init.teamId)}`,
    {
      method: "POST",
      body: JSON.stringify({
        name: args.projectName,
        project: args.projectId,
        target: args.target ?? "production",
        files: files.map((f) => ({
          file: f.relPath,
          data: f.base64,
          encoding: "base64",
        })),
        projectSettings: {
          // Auto-detect framework from package.json. Vercel does this
          // server-side too but explicit is safer.
          framework: null,
        },
      }),
    },
  );
}

export async function getDeployment(
  init: VercelInit,
  uid: string,
): Promise<VercelDeployment> {
  return vercelFetch<VercelDeployment>(
    init,
    `/v13/deployments/${encodeURIComponent(uid)}${teamSuffix(init.teamId)}`,
  );
}

export async function listDeployments(
  init: VercelInit,
  args: { projectId: string; limit?: number },
): Promise<VercelDeployment[]> {
  const r = await vercelFetch<{ deployments?: VercelDeployment[] }>(
    init,
    `/v6/deployments${teamAndExtra(
      init.teamId,
      `projectId=${args.projectId}&limit=${args.limit ?? 10}`,
    )}`,
  );
  return r.deployments ?? [];
}

// ── Domains ────────────────────────────────────────────────────────────

export type VercelDomain = {
  name: string;
  verified: boolean;
  verification?: Array<{ type: string; domain: string; value: string }>;
};

export async function listDomains(
  init: VercelInit,
  projectId: string,
): Promise<VercelDomain[]> {
  const r = await vercelFetch<{ domains?: VercelDomain[] }>(
    init,
    `/v9/projects/${encodeURIComponent(projectId)}/domains${teamSuffix(init.teamId)}`,
  );
  return r.domains ?? [];
}

export async function addDomain(
  init: VercelInit,
  projectId: string,
  domain: string,
): Promise<VercelDomain> {
  return vercelFetch<VercelDomain>(
    init,
    `/v10/projects/${encodeURIComponent(projectId)}/domains${teamSuffix(init.teamId)}`,
    {
      method: "POST",
      body: JSON.stringify({ name: domain }),
    },
  );
}

export async function removeDomain(
  init: VercelInit,
  projectId: string,
  domain: string,
): Promise<void> {
  await vercelFetch(
    init,
    `/v9/projects/${encodeURIComponent(projectId)}/domains/${encodeURIComponent(domain)}${teamSuffix(init.teamId)}`,
    { method: "DELETE" },
  );
}

// ── Internal: file collection for deployment payload ──────────────────

const SKIP_DIRS = new Set([
  "node_modules",
  ".next",
  ".turbo",
  ".cache",
  ".git",
  ".jarvis",
  "dist",
  "build",
  "out",
  ".pnpm-store",
  ".yarn",
]);

const MAX_FILE_BYTES = 8 * 1024 * 1024; // 8MB per file
const MAX_TOTAL_BYTES = 90 * 1024 * 1024; // safe under Vercel's 100MB inline limit

async function collectFiles(
  root: string,
): Promise<{ relPath: string; base64: string }[]> {
  const out: { relPath: string; base64: string }[] = [];
  let total = 0;

  async function walk(dir: string, rel: string) {
    let entries;
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const abs = path.join(dir, e.name);
      const r = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) {
        if (SKIP_DIRS.has(e.name)) continue;
        await walk(abs, r);
        continue;
      }
      if (!e.isFile()) continue;
      let stat;
      try {
        stat = await fs.stat(abs);
      } catch {
        continue;
      }
      if (stat.size > MAX_FILE_BYTES) {
        // Single file too large — skip rather than fail the whole deploy.
        // The user sees a warning in the deploy log; common cause is a
        // committed binary that should have been in .gitignore.
        continue;
      }
      if (total + stat.size > MAX_TOTAL_BYTES) {
        throw new Error(
          `Workspace exceeds Vercel inline-deploy size cap (${Math.round(MAX_TOTAL_BYTES / 1024 / 1024)}MB). Add large assets to .vercelignore or move them to a CDN.`,
        );
      }
      total += stat.size;
      const buf = await fs.readFile(abs);
      out.push({ relPath: r, base64: buf.toString("base64") });
    }
  }

  await walk(root, "");
  return out;
}
