import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findSession } from "@/lib/bridge/store";
import { githubPrStatus } from "@/lib/connectors/github";
import { authorizeSession } from "@/lib/bridge/authz";
import { bridgeError } from "@/lib/bridge/errors";

// GET /api/bridge/v1/sessions/{id}/pr-status?branch=<branch> — PR + CI check
// status for the session's branch (the Diff panel polls this). Returns empty
// status (not an error) when GitHub isn't connected or nothing is open yet.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const denied = await authorizeSession(req, sessionId);
  if (denied) return denied;
  const branch = new URL(req.url).searchParams.get("branch") ?? "";
  const empty = NextResponse.json({ pr: null, checks: null, sha: null, repo: null });
  if (!branch) return empty;
  try {
    const session = findSession(getStore(), sessionId);
    const meta = session?.container_json
      ? (JSON.parse(session.container_json) as { repo?: string })
      : null;
    if (!meta?.repo) return empty;
    const r = await githubPrStatus(meta.repo, branch);
    if (!r.ok) return empty;
    return NextResponse.json({ ...r.status, repo: meta.repo });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `pr-status failed: ${msg}`);
  }
}
