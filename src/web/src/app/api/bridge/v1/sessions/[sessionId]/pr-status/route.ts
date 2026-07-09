import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findSession } from "@/lib/bridge/store";
import { freshInstallationToken } from "@/lib/bridge/gh-app-token";
import { githubPrStatus } from "@/lib/connectors/github";
import { authorizeBridgeRequest } from "@/lib/bridge/authz";
import { bridgeError } from "@/lib/bridge/errors";

// GET /api/bridge/v1/sessions/{id}/pr-status?branch=<branch> — PR + CI check
// status for the session's branch (the Diff panel polls this). Returns empty
// status (not an error) when GitHub isn't connected or nothing is open yet.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const denied = await authorizeBridgeRequest(req, { scope: "session-owner", sessionId });
  if (denied) return denied;
  const branch = new URL(req.url).searchParams.get("branch") ?? "";
  const empty = NextResponse.json({ pr: null, checks: null, sha: null, repo: null });
  if (!branch) return empty;
  try {
    const store = getStore();
    const session = findSession(store, sessionId);
    const meta = session?.container_json
      ? (JSON.parse(session.container_json) as { repo?: string })
      : null;
    if (!meta?.repo) return empty;
    // External bot-job sessions (gh-app dispatch) carry an App installation
    // token — authenticate the lookup with it (refreshed through the gh-app
    // when stale); normal sessions call exactly as before (stored PAT).
    const injected = await freshInstallationToken(store, sessionId, session);
    const r = injected
      ? await githubPrStatus(meta.repo, branch, injected)
      : await githubPrStatus(meta.repo, branch);
    if (!r.ok) return empty;
    return NextResponse.json({ ...r.status, repo: meta.repo });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `pr-status failed: ${msg}`);
  }
}
