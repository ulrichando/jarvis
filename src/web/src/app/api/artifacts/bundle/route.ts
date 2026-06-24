import { NextResponse } from "next/server";
import { bundleReactSource } from "@/lib/artifacts/bundle";
import { getUserId } from "@/lib/auth-helpers";

export const runtime = "nodejs";

// Bundle a single React artifact's source → self-contained ESM the preview
// iframe loads. Used by the live in-chat panel + the gallery detail view
// (both authed via the same-origin session). The public share page bundles
// server-side instead (see /a/[token]) so it works logged-out.
//
// Defense-in-depth: proxy.ts already 401s unauthenticated /api/* (verified),
// and this only transpiles posted source (no data/secret access). The gate
// below keeps it consistent with the other artifact routes.
export async function POST(req: Request) {
  await getUserId(req.headers);
  let source = "";
  try {
    const body = (await req.json()) as { source?: string };
    source = body.source ?? "";
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }
  if (!source.trim()) {
    return NextResponse.json({ error: "missing source" }, { status: 400 });
  }
  const out = await bundleReactSource(source);
  if ("error" in out) {
    return NextResponse.json({ error: out.error }, { status: 422 });
  }
  return new Response(out.js, {
    headers: {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
