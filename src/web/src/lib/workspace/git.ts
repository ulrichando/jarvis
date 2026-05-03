import "server-only";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { promises as fs } from "node:fs";
import path from "node:path";
import { workspaceRoot } from "./storage";

// Git-backed workspace history. Each workspace is its own git repo;
// every successful artifact drain produces a commit. This gives us
// real history (replacing custom checkpoints over time), branch-based
// experiments, and a clean export-to-GitHub path — matching what
// Lovable / Replit Agent / Copilot Workspace ship.

const execFileP = promisify(execFile);

const DEFAULT_GITIGNORE = [
  "# Build / dependency caches — never commit these",
  "node_modules/",
  ".next/",
  ".turbo/",
  ".cache/",
  ".pnpm-store/",
  ".yarn/cache/",
  "dist/",
  "build/",
  "out/",
  "",
  "# Jarvis runtime — checkpoints, dev.log, etc.",
  ".jarvis/",
  "",
  "# Logs and local env",
  "*.log",
  ".env.local",
  ".env*.local",
  "",
  "# OS junk",
  ".DS_Store",
  "Thumbs.db",
  "",
].join("\n");

const GIT_ENV: Record<string, string> = {
  GIT_AUTHOR_NAME: "jarvis",
  GIT_AUTHOR_EMAIL: "jarvis@local",
  GIT_COMMITTER_NAME: "jarvis",
  GIT_COMMITTER_EMAIL: "jarvis@local",
};

async function git(
  cwd: string,
  args: string[],
): Promise<{ stdout: string; stderr: string }> {
  const { stdout, stderr } = await execFileP("git", args, {
    cwd,
    env: { ...process.env, ...GIT_ENV },
    maxBuffer: 16 * 1024 * 1024,
  });
  return { stdout, stderr };
}

export async function isGitRepo(workspaceId: string): Promise<boolean> {
  const dir = workspaceRoot(workspaceId);
  try {
    await fs.access(path.join(dir, ".git"));
    return true;
  } catch {
    return false;
  }
}

export async function gitInit(workspaceId: string): Promise<void> {
  const dir = workspaceRoot(workspaceId);
  if (await isGitRepo(workspaceId)) return;
  // -b main so we don't get the deprecation warning on systems with
  // older default-branch config; matches GitHub's modern default too.
  await git(dir, ["init", "-b", "main"]);
  const ignorePath = path.join(dir, ".gitignore");
  try {
    await fs.access(ignorePath);
  } catch {
    await fs.writeFile(ignorePath, DEFAULT_GITIGNORE, "utf8");
  }
  // Initial empty commit so HEAD always exists. Without it, gitCommit
  // on a fresh repo with no changes would have no parent to compare
  // against and certain operations (reset, log) edge-case differently.
  await git(dir, ["add", ".gitignore"]);
  await git(dir, ["commit", "-m", "init", "--allow-empty"]);
}

export type CommitInfo = {
  sha: string;
  shortSha: string;
  subject: string;
  ts: number;
};

function parseLogLine(line: string): CommitInfo | null {
  const [sha, shortSha, subject, ts] = line.split("\t");
  if (!sha || !shortSha) return null;
  return {
    sha,
    shortSha,
    subject: subject ?? "",
    ts: Number(ts) * 1000,
  };
}

const LOG_FORMAT = "--pretty=format:%H%x09%h%x09%s%x09%ct";

export async function gitCommit(
  workspaceId: string,
  message: string,
): Promise<CommitInfo | null> {
  const dir = workspaceRoot(workspaceId);
  if (!(await isGitRepo(workspaceId))) await gitInit(workspaceId);
  await git(dir, ["add", "-A"]);
  const { stdout: status } = await git(dir, ["status", "--porcelain"]);
  if (!status.trim()) return null;
  const subject = (message.trim() || "update").slice(0, 200);
  await git(dir, ["commit", "-m", subject]);
  const { stdout: log } = await git(dir, ["log", "-1", LOG_FORMAT]);
  return parseLogLine(log.trim());
}

export async function gitLog(
  workspaceId: string,
  limit = 50,
): Promise<CommitInfo[]> {
  if (!(await isGitRepo(workspaceId))) return [];
  const dir = workspaceRoot(workspaceId);
  const { stdout } = await git(dir, ["log", `-n${limit}`, LOG_FORMAT]);
  return stdout
    .split("\n")
    .map((l) => parseLogLine(l))
    .filter((c): c is CommitInfo => c !== null);
}

export async function gitPush(args: {
  workspaceId: string;
  ownerRepo: string; // "<owner>/<repo>"
  token: string;
}): Promise<{ url: string }> {
  const { workspaceId, ownerRepo, token } = args;
  if (!/^[\w.-]+\/[\w.-]+$/.test(ownerRepo)) {
    throw new Error("invalid <owner>/<repo>");
  }
  if (!token) throw new Error("missing github token");
  if (!(await isGitRepo(workspaceId))) await gitInit(workspaceId);
  const dir = workspaceRoot(workspaceId);

  // Authenticated remote URL. The token is embedded in the URL so git
  // can push without a credential helper or interactive prompt. We
  // never log this URL — only the public form `https://github.com/<owner>/<repo>.git`.
  const authUrl = `https://x-access-token:${encodeURIComponent(token)}@github.com/${ownerRepo}.git`;
  const publicUrl = `https://github.com/${ownerRepo}.git`;

  // Configure or replace the `origin` remote. `git remote add` errors
  // if it exists; `set-url` is idempotent — try add, fall back to
  // set-url. Either way the END state is `origin → publicUrl` (we set
  // a non-token URL after pushing so the working tree never persists
  // the secret on disk).
  try {
    await git(dir, ["remote", "add", "origin", authUrl]);
  } catch {
    await git(dir, ["remote", "set-url", "origin", authUrl]);
  }

  try {
    await git(dir, ["push", "-u", "origin", "main"]);
  } finally {
    // Always strip the token from the saved remote so it doesn't sit
    // in `.git/config` plaintext after the push.
    await git(dir, ["remote", "set-url", "origin", publicUrl]).catch(
      () => {},
    );
  }

  return { url: publicUrl };
}

export async function gitRestore(
  workspaceId: string,
  sha: string,
): Promise<void> {
  if (!(await isGitRepo(workspaceId))) {
    throw new Error("workspace is not a git repo");
  }
  // Refuse anything that isn't a hex sha. We use execFile (no shell)
  // so injection isn't a concern, but rejecting garbage early is
  // friendlier than letting git error with a cryptic message.
  if (!/^[a-f0-9]{4,40}$/i.test(sha)) {
    throw new Error("invalid commit sha");
  }
  const dir = workspaceRoot(workspaceId);
  await git(dir, ["reset", "--hard", sha]);
}
