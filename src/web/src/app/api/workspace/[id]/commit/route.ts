import { NextResponse } from "next/server";
import { gitCommit, gitLog, gitRestore } from "@/lib/workspace/git";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * GET  /api/workspace/[id]/commit
 *      → { commits: CommitInfo[] }
 *      Returns the most recent 50 commits in the workspace's git history.
 *      Empty array when the workspace isn't a git repo (legacy workspace
 *      created before git-init landed; will become a repo on first commit).
 *
 * POST /api/workspace/[id]/commit
 *      body: { message: string }
 *      → { commit: CommitInfo | null }
 *      Stages everything and commits with the given message. `commit` is
 *      null when there were no changes to commit (no-op turn). The chat
 *      layer calls this after every successful artifact drain so each
 *      turn that touched files becomes one commit on the main branch.
 *
 * POST /api/workspace/[id]/commit/restore
 *      body: { sha: string }
 *      → { ok: true }
 *      Hard-resets the workspace to the given commit. Destructive — used
 *      by the future "Project history" UI for rollback. The Undo button
 *      on individual messages still uses the checkpoint system; this is
 *      a separate, broader-grained rollback.
 */

export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/commit">,
) {
  const { id } = await ctx.params;
  try {
    const commits = await gitLog(id, 50);
    return NextResponse.json({ commits });
  } catch (err) {
    console.error("[commit] log failed:", err);
    return NextResponse.json({ commits: [] });
  }
}

export async function POST(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/commit">,
) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const message = String(body.message ?? "update").trim() || "update";
  try {
    const commit = await gitCommit(id, message);
    return NextResponse.json({ commit });
  } catch (err) {
    console.error("[commit] failed:", err);
    return NextResponse.json(
      { error: "commit_failed", message: String(err) },
      { status: 500 },
    );
  }
}
