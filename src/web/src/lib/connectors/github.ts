import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import os from "node:os";

// GitHub connector for /code. Local self-hosted tool → a Personal Access Token
// is the right auth (per GitHub's guidance: PATs for local automation; OAuth is
// for redirect/act-as-user flows). The token is stored SERVER-SIDE only
// (~/.jarvis/connectors.json, chmod 600) and never sent to the browser — the
// client only ever learns the connected login + uses the server routes.

const FILE = path.join(os.homedir(), ".jarvis", "connectors.json");
const GH = "https://api.github.com";

type Connectors = {
  github?: { token: string; login: string; connectedAt: number };
};

export type GithubIssue = {
  number: number;
  title: string;
  body: string;
  repo: string;
  url: string;
  updated_at: string;
};

export type GithubRepo = {
  full_name: string;
  private: boolean;
  default_branch: string;
  pushed_at: string;
  url: string;
};

async function load(): Promise<Connectors> {
  try {
    return JSON.parse(await fs.readFile(FILE, "utf8")) as Connectors;
  } catch {
    return {};
  }
}

async function save(c: Connectors): Promise<void> {
  await fs.mkdir(path.dirname(FILE), { recursive: true });
  await fs.writeFile(FILE, JSON.stringify(c, null, 2), { mode: 0o600 });
  await fs.chmod(FILE, 0o600).catch(() => {});
}

function ghHeaders(token: string): Record<string, string> {
  // Bearer works for both classic + fine-grained PATs.
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "jarvis-code",
  };
}

export async function githubStatus(): Promise<{ connected: boolean; login?: string }> {
  const c = await load();
  return c.github ? { connected: true, login: c.github.login } : { connected: false };
}

/** Validate a PAT (GET /user) and persist it on success. Never echoes the token. */
export async function connectGithub(
  token: string,
): Promise<{ ok: true; login: string } | { ok: false; error: string }> {
  const t = token.trim();
  if (!t) return { ok: false, error: "Token is empty" };
  let r: Response;
  try {
    r = await fetch(`${GH}/user`, { headers: ghHeaders(t) });
  } catch (e) {
    return { ok: false, error: `Network error: ${String(e)}` };
  }
  if (r.status === 401) return { ok: false, error: "Invalid token (401)" };
  if (!r.ok) return { ok: false, error: `GitHub error ${r.status}` };
  const user = (await r.json()) as { login?: string };
  if (!user.login) return { ok: false, error: "Unexpected GitHub response" };
  const c = await load();
  c.github = { token: t, login: user.login, connectedAt: Date.now() };
  await save(c);
  return { ok: true, login: user.login };
}

/**
 * The stored PAT, for SERVER-SIDE git operations only (e.g. cloning into a
 * container session). Never expose through a route response.
 */
export async function getGithubToken(): Promise<string | null> {
  const c = await load();
  return c.github?.token ?? null;
}

export async function disconnectGithub(): Promise<void> {
  const c = await load();
  delete c.github;
  await save(c);
}

/**
 * Secret for verifying inbound GitHub webhook deliveries (X-Hub-Signature-256).
 * From GITHUB_WEBHOOK_SECRET, else the connector file. Null → webhooks are
 * rejected (no unauthenticated triggers).
 */
export async function getGithubWebhookSecret(): Promise<string | null> {
  if (process.env.GITHUB_WEBHOOK_SECRET) return process.env.GITHUB_WEBHOOK_SECRET;
  const c = await load();
  return (c.github as { webhookSecret?: string } | undefined)?.webhookSecret ?? null;
}

/** Open issues assigned to / created by the authenticated user, across repos. */
export async function listGithubIssues(): Promise<
  { ok: true; issues: GithubIssue[] } | { ok: false; error: string }
> {
  const c = await load();
  if (!c.github) return { ok: false, error: "GitHub not connected" };
  let r: Response;
  try {
    r = await fetch(`${GH}/issues?filter=all&state=open&per_page=30&sort=updated`, {
      headers: ghHeaders(c.github.token),
    });
  } catch (e) {
    return { ok: false, error: `Network error: ${String(e)}` };
  }
  if (r.status === 401) return { ok: false, error: "GitHub token no longer valid — reconnect." };
  if (!r.ok) return { ok: false, error: `GitHub error ${r.status}` };
  const raw = (await r.json()) as Array<Record<string, unknown>>;
  const issues: GithubIssue[] = raw
    .filter((i) => !i.pull_request) // /issues includes PRs; drop them
    .map((i) => ({
      number: Number(i.number),
      title: String(i.title ?? ""),
      body: String(i.body ?? "").slice(0, 4000),
      repo:
        (i.repository as { full_name?: string } | undefined)?.full_name ??
        String(i.repository_url ?? "").replace("https://api.github.com/repos/", ""),
      url: String(i.html_url ?? ""),
      updated_at: String(i.updated_at ?? ""),
    }));
  return { ok: true, issues };
}

/** The authenticated user's repos (owner + collaborator + org), recently pushed first. */
export async function listGithubRepos(): Promise<
  { ok: true; repos: GithubRepo[] } | { ok: false; error: string }
> {
  const c = await load();
  if (!c.github) return { ok: false, error: "GitHub not connected" };
  let r: Response;
  try {
    r = await fetch(
      `${GH}/user/repos?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member`,
      { headers: ghHeaders(c.github.token) },
    );
  } catch (e) {
    return { ok: false, error: `Network error: ${String(e)}` };
  }
  if (r.status === 401) return { ok: false, error: "GitHub token no longer valid — reconnect." };
  if (!r.ok) return { ok: false, error: `GitHub error ${r.status}` };
  const raw = (await r.json()) as Array<Record<string, unknown>>;
  const repos: GithubRepo[] = raw.map((x) => ({
    full_name: String(x.full_name ?? ""),
    private: Boolean(x.private),
    default_branch: String(x.default_branch ?? "main"),
    pushed_at: String(x.pushed_at ?? ""),
    url: String(x.html_url ?? ""),
  }));
  return { ok: true, repos };
}

/** A PR's unified diff (capped for the model context). Null on error/empty. */
export async function getPrDiff(repo: string, number: number, cap = 60000): Promise<string | null> {
  const c = await load();
  if (!c.github) return null;
  try {
    const r = await fetch(`${GH}/repos/${repo}/pulls/${number}`, {
      headers: { ...ghHeaders(c.github.token), Accept: "application/vnd.github.v3.diff" },
    });
    if (!r.ok) return null;
    const diff = await r.text();
    return diff.slice(0, cap);
  } catch {
    return null;
  }
}

/** Post a comment on a PR/issue. Returns the comment URL on success. */
export async function postPrComment(
  repo: string,
  number: number,
  body: string,
): Promise<{ ok: true; url: string } | { ok: false; error: string }> {
  const c = await load();
  if (!c.github) return { ok: false, error: "GitHub not connected" };
  try {
    const r = await fetch(`${GH}/repos/${repo}/issues/${number}/comments`, {
      method: "POST",
      headers: ghHeaders(c.github.token),
      body: JSON.stringify({ body }),
    });
    if (!r.ok) return { ok: false, error: `GitHub error ${r.status}` };
    const j = (await r.json()) as { html_url?: string };
    return { ok: true, url: String(j.html_url ?? "") };
  } catch (e) {
    return { ok: false, error: `Network error: ${String(e)}` };
  }
}

export type PrStatus = {
  pr: { number: number; url: string; state: string; draft: boolean } | null;
  checks: { total: number; passed: number; failed: number; pending: number; failing: string[] } | null;
  /** Head commit SHA — lets the client auto-fix at most once per failing commit. */
  sha: string | null;
};

/**
 * PR + CI status for `<repo>` branch `<branch>` (the /code Diff panel). Finds
 * the PR opened from the branch, then summarizes its head commit's check runs.
 * Returns nulls (not an error) when nothing is open yet so the panel can poll.
 */
export async function githubPrStatus(
  repo: string,
  branch: string,
): Promise<{ ok: true; status: PrStatus } | { ok: false; error: string }> {
  const c = await load();
  if (!c.github) return { ok: false, error: "GitHub not connected" };
  const owner = repo.split("/")[0];
  const h = ghHeaders(c.github.token);
  try {
    const pr = await fetch(
      `${GH}/repos/${repo}/pulls?head=${owner}:${branch}&state=all&per_page=1`,
      { headers: h },
    );
    if (!pr.ok) return { ok: false, error: `GitHub error ${pr.status}` };
    const prs = (await pr.json()) as Array<Record<string, unknown>>;
    const p = prs[0];
    if (!p) return { ok: true, status: { pr: null, checks: null, sha: null } };
    const prInfo = {
      number: Number(p.number),
      url: String(p.html_url ?? ""),
      state: String(p.state ?? "open"),
      draft: Boolean(p.draft),
    };
    const sha = (p.head as { sha?: string } | undefined)?.sha;
    let checks: PrStatus["checks"] = null;
    if (sha) {
      const cr = await fetch(`${GH}/repos/${repo}/commits/${sha}/check-runs`, { headers: h });
      if (cr.ok) {
        const runs = ((await cr.json()) as { check_runs?: Array<Record<string, unknown>> }).check_runs ?? [];
        const failing: string[] = [];
        let passed = 0;
        let failed = 0;
        let pending = 0;
        for (const r of runs) {
          const done = r.status === "completed";
          const ok = r.conclusion === "success" || r.conclusion === "neutral" || r.conclusion === "skipped";
          if (!done) pending++;
          else if (ok) passed++;
          else {
            failed++;
            failing.push(String(r.name ?? "check"));
          }
        }
        checks = { total: runs.length, passed, failed, pending, failing };
      }
    }
    return { ok: true, status: { pr: prInfo, checks, sha: sha ?? null } };
  } catch (e) {
    return { ok: false, error: `Network error: ${String(e)}` };
  }
}
