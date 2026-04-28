import { NextResponse } from "next/server";
import { listWorkspaces, createWorkspace } from "@/lib/workspace/storage";

export const runtime = "nodejs";

export async function GET() {
  const workspaces = await listWorkspaces();
  return NextResponse.json({ workspaces });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const name = typeof body?.name === "string" ? body.name : "untitled";
  const ws = await createWorkspace(name);
  return NextResponse.json({ workspace: ws });
}
