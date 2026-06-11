import { NextResponse } from "next/server";
import { listGithubRepos } from "@/lib/connectors/github";

// GET /api/github/repos — the connected GitHub account's repositories for the
// /code repo picker. 400 if GitHub isn't connected.
export async function GET(): Promise<NextResponse> {
  const r = await listGithubRepos();
  return NextResponse.json(r, { status: r.ok ? 200 : 400 });
}
