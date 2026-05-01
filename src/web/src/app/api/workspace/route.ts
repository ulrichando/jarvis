import { NextResponse } from "next/server";
import {
  createWorkspace,
  listWorkspaces,
  listWorkspacesOfKind,
  type WorkspaceKind,
} from "@/lib/workspace/storage";

export const runtime = "nodejs";

export async function GET(req: Request) {
  // Optional `?kind=design|workbench` filter so each tab can list only
  // its own workspaces. Legacy callers without the param get the full
  // unfiltered list (back-compat).
  const url = new URL(req.url);
  const kind = url.searchParams.get("kind");
  const workspaces =
    kind === "design" || kind === "workbench"
      ? await listWorkspacesOfKind(kind as WorkspaceKind)
      : await listWorkspaces();
  return NextResponse.json({ workspaces });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const name = typeof body?.name === "string" ? body.name : "untitled";
  const kind =
    body?.kind === "design" || body?.kind === "workbench"
      ? (body.kind as WorkspaceKind)
      : undefined;
  const ws = await createWorkspace(name, kind);
  return NextResponse.json({ workspace: ws });
}
