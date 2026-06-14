import { NextResponse } from "next/server";
import { gitPush } from "@/lib/workspace/git";
import { loadSettings } from "@/lib/settings/store";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * POST /api/workspace/[id]/push
 *      body: { ownerRepo: "<owner>/<repo>" }
 *      → { ok: true, url } | { error: "missing_token" | "push_failed", ... }
 *
 * Pushes the workspace's git repo to a GitHub remote using the token
 * configured in Settings → Integrations → GitHub. The remote must
 * already exist on GitHub (we don't create repos here — that needs a
 * separate Repos scope on the token + an extra API call).
 */
export async function POST(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/push">,
) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const ownerRepo = String(body.ownerRepo ?? "").trim();
  if (!ownerRepo) {
    return NextResponse.json(
      { error: "missing ownerRepo" },
      { status: 400 },
    );
  }

  const settings = await loadSettings();
  const token = settings.integrations?.github?.token;
  if (!token) {
    return NextResponse.json(
      {
        error: "missing_token",
        message:
          "Add a GitHub Personal Access Token in Settings → Integrations.",
      },
      { status: 400 },
    );
  }

  try {
    const { url } = await gitPush({ workspaceId: id, ownerRepo, token });
    return NextResponse.json({ ok: true, url });
  } catch (err) {
    // git errors frequently echo the remote URL, which embeds the token
    // (https://x-access-token:<TOKEN>@github.com/…). Redact the exact
    // token before it can reach the server logs OR the client response.
    const raw = String(err instanceof Error ? err.message : err);
    const redacted = token ? raw.split(token).join("***") : raw;
    console.error("[push] failed:", redacted);
    return NextResponse.json(
      { error: "push_failed", message: redacted },
      { status: 500 },
    );
  }
}
