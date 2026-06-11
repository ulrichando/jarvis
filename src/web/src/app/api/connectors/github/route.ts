import { NextResponse } from "next/server";
import { githubStatus, connectGithub, disconnectGithub } from "@/lib/connectors/github";

// GET — is GitHub connected? (returns { connected, login? } — never the token)
export async function GET(): Promise<NextResponse> {
  return NextResponse.json(await githubStatus());
}

// POST { token } — validate a PAT against GET /user and persist it server-side.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => ({}))) as { token?: string };
  if (!body.token || typeof body.token !== "string") {
    return NextResponse.json({ ok: false, error: "token required" }, { status: 400 });
  }
  const r = await connectGithub(body.token);
  return NextResponse.json(r, { status: r.ok ? 200 : 400 });
}

// DELETE — forget the stored token.
export async function DELETE(): Promise<NextResponse> {
  await disconnectGithub();
  return NextResponse.json({ ok: true });
}
