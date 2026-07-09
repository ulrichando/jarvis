import { NextResponse } from "next/server";
import {
  createWorkspace,
  listWorkspaces,
  listWorkspacesOfKind,
  type WorkspaceKind,
} from "@/lib/workspace/storage";
import { requireUserIdOrSharedLocal, Unauthenticated } from "@/lib/auth-helpers";

export const runtime = "nodejs";

// Workspaces are a single-user filesystem store (not user-scoped), so this is a
// credential CHECK, not data-scoping: accept a session cookie, the shared local
// token, or a per-user API/bridge token — reject anything else. Needed so these
// routes can sit on proxy.ts SELF_AUTH (which waives the shared-token gate) for
// the voice agent's per-user API token without becoming publicly reachable.
async function authOr401(req: Request): Promise<Response | null> {
  try {
    await requireUserIdOrSharedLocal(req.headers);
    return null;
  } catch (e) {
    if (e instanceof Unauthenticated)
      return new Response("Unauthorized", { status: 401 });
    throw e;
  }
}

export async function GET(req: Request) {
  const denied = await authOr401(req);
  if (denied) return denied;
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
  const denied = await authOr401(req);
  if (denied) return denied;
  const body = await req.json().catch(() => ({}));
  const name = typeof body?.name === "string" ? body.name : "untitled";
  const kind =
    body?.kind === "design" || body?.kind === "workbench"
      ? (body.kind as WorkspaceKind)
      : undefined;
  const ws = await createWorkspace(name, kind);
  return NextResponse.json({ workspace: ws });
}
