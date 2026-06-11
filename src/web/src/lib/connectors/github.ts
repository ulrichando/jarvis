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

export async function disconnectGithub(): Promise<void> {
  const c = await load();
  delete c.github;
  await save(c);
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
