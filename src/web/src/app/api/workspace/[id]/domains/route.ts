import { NextResponse } from "next/server";
import { getWorkspace } from "@/lib/workspace/storage";
import {
  addDomain,
  listDomains,
  removeDomain,
} from "@/lib/deploy/vercel";

export const runtime = "nodejs";

// Strict-ish hostname validation. The previous /^[a-z0-9.-]+\.[a-z]{2,}$/
// accepted "..", leading/trailing hyphens, and all-numeric labels like
// "0.0.0.0". Validate per-label: each label 1-63 chars of [a-z0-9-] with
// no leading/trailing hyphen, an alphabetic TLD, total length <= 253.
function isValidDomain(d: string): boolean {
  if (!d || d.length > 253) return false;
  const labels = d.split(".");
  if (labels.length < 2) return false;
  const tld = labels[labels.length - 1];
  if (!/^[a-z]{2,}$/.test(tld)) return false;
  return labels.every(
    (l) => /^[a-z0-9-]{1,63}$/.test(l) && !l.startsWith("-") && !l.endsWith("-"),
  );
}

/**
 * GET    /api/workspace/[id]/domains  → list domains attached to the
 *                                       workspace's Vercel project
 * POST   /api/workspace/[id]/domains  body: { domain: string }
 *                                       attach a custom domain
 * DELETE /api/workspace/[id]/domains?domain=<name>
 *                                       detach
 */

function getToken(envVars: Record<string, string> | undefined): string | null {
  if (!envVars) return null;
  return envVars.VERCEL_TOKEN ?? null;
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });
  const token = getToken(ws.envVars);
  if (!token || !ws.deploy?.projectId || ws.deploy.provider !== "vercel") {
    return NextResponse.json({
      configured: false,
      domains: [],
    });
  }
  try {
    const domains = await listDomains(
      { token, teamId: ws.deploy.teamId },
      ws.deploy.projectId,
    );
    return NextResponse.json({ configured: true, domains });
  } catch (err) {
    return NextResponse.json(
      {
        configured: true,
        domains: [],
        error: err instanceof Error ? err.message : String(err),
      },
      { status: 200 },
    );
  }
}

export async function POST(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => ({}))) as { domain?: string };
  const domain = (body.domain ?? "").trim().toLowerCase();
  if (!isValidDomain(domain)) {
    return NextResponse.json({ error: "invalid_domain" }, { status: 400 });
  }
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });
  const token = getToken(ws.envVars);
  if (!token || !ws.deploy?.projectId || ws.deploy.provider !== "vercel") {
    return NextResponse.json(
      {
        error: "not_configured",
        hint: "Run a deploy first to create the Vercel project, then add a domain.",
      },
      { status: 400 },
    );
  }
  try {
    const added = await addDomain(
      { token, teamId: ws.deploy.teamId },
      ws.deploy.projectId,
      domain,
    );
    return NextResponse.json({ domain: added });
  } catch (err) {
    return NextResponse.json(
      {
        error: "add_failed",
        message: err instanceof Error ? err.message : String(err),
      },
      { status: 502 },
    );
  }
}

export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const domain = (url.searchParams.get("domain") ?? "").trim();
  if (!domain) {
    return NextResponse.json({ error: "missing_domain" }, { status: 400 });
  }
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });
  const token = getToken(ws.envVars);
  if (!token || !ws.deploy?.projectId || ws.deploy.provider !== "vercel") {
    return NextResponse.json(
      { error: "not_configured" },
      { status: 400 },
    );
  }
  try {
    await removeDomain(
      { token, teamId: ws.deploy.teamId },
      ws.deploy.projectId,
      domain,
    );
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json(
      {
        error: "remove_failed",
        message: err instanceof Error ? err.message : String(err),
      },
      { status: 502 },
    );
  }
}
