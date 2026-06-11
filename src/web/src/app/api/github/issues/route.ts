import { NextResponse } from "next/server";
import { listGithubIssues } from "@/lib/connectors/github";

// GET — open issues for the connected GitHub account (uses the server-side
// token; the browser never sees it). 400 if not connected.
export async function GET(): Promise<NextResponse> {
  const r = await listGithubIssues();
  return NextResponse.json(r, { status: r.ok ? 200 : 400 });
}
