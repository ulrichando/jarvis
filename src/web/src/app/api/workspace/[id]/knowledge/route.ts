import { NextResponse } from "next/server";
import {
  addKnowledge,
  listKnowledge,
  removeKnowledge,
  setKnowledgeEnabled,
} from "@/lib/workspace/knowledge";

export const runtime = "nodejs";

/**
 * Knowledge document API for the workspace's chat scope.
 *
 * GET     /api/workspace/[id]/knowledge        → list docs
 * POST    /api/workspace/[id]/knowledge        body: { name, content }   → add/replace
 * PATCH   /api/workspace/[id]/knowledge        body: { name, enabled }   → enable/disable
 * DELETE  /api/workspace/[id]/knowledge?name=  → remove
 *
 * Files live at <workspace>/.jarvis/knowledge/<name>. The chat route
 * reads + concatenates enabled docs into the system prompt on every
 * workspace-scoped turn (lib/workspace/knowledge.ts → readKnowledgeBlock).
 */

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const docs = await listKnowledge(id);
  return NextResponse.json({ docs });
}

export async function POST(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    content?: unknown;
  };
  const name = typeof body.name === "string" ? body.name : "";
  const content = typeof body.content === "string" ? body.content : "";
  const r = await addKnowledge(id, name, content);
  if (!r.ok) {
    return NextResponse.json({ error: r.error }, { status: 400 });
  }
  return NextResponse.json({ doc: r.doc });
}

export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    enabled?: unknown;
  };
  const name = typeof body.name === "string" ? body.name : "";
  const enabled = body.enabled === true;
  const ok = await setKnowledgeEnabled(id, name, enabled);
  if (!ok) return NextResponse.json({ error: "invalid name" }, { status: 400 });
  return NextResponse.json({ ok: true });
}

export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const name = url.searchParams.get("name") ?? "";
  const ok = await removeKnowledge(id, name);
  if (!ok) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ ok: true });
}
