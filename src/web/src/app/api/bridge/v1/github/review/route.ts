import { NextResponse } from "next/server";
import { reviewPullRequest } from "@/lib/bridge/code-review";
import { validRepoFullName } from "@/lib/bridge/containers";
import { bridgeError } from "@/lib/bridge/errors";

// POST /api/bridge/v1/github/review { repo, number, model? } — review a PR's
// diff with a model and post the findings as a PR comment (claude.ai/code Code
// Review). Manual trigger; the webhook auto-reviews opened PRs via the separate
// github/webhook route when JARVIS_CODE_AUTO_REVIEW=1.
//
// AUTH: gated at the network edge by proxy.ts's /api/* bearer gate
// (JARVIS_REQUIRE_LOCAL_AUTH=1) — this route is NOT in proxy.ts's public
// allowlist. There is no per-resource OWNERSHIP to enforce here: the review
// runs with the server's own gh credentials (not a per-user resource), so a
// per-route bearer/getUserId check would add nothing the proxy gate doesn't
// already do AND would reject the auth-disabled dev path. The reviewable
// surface is bounded by what the server's gh token can already access.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    repo?: string;
    number?: number;
    model?: string;
  } | null;
  if (!body?.repo || !validRepoFullName(body.repo) || typeof body.number !== "number") {
    return bridgeError(400, "invalid_request", "repo (owner/name) + number required");
  }
  const r = await reviewPullRequest(body.repo, body.number, body.model ?? "");
  if (!r.ok) return bridgeError(400, "invalid_request", r.error);
  return NextResponse.json({ ok: true, url: r.url });
}
