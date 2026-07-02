import { NextResponse } from "next/server";
import {
  addKnowledge,
  listKnowledge,
  removeKnowledge,
  setKnowledgeEnabled,
} from "@/lib/knowledge/store";

export const runtime = "nodejs";

/**
 * Personal knowledge document API (Settings → Knowledge).
 *
 * GET     /api/knowledge        → list docs
 * POST    /api/knowledge        body: { name, content }   → add/replace
 * PATCH   /api/knowledge        body: { name, enabled }   → enable/disable
 * DELETE  /api/knowledge?name=  → remove
 *
 * Files live at ~/.jarvis/knowledge/<name>. The chat route reads +
 * concatenates enabled docs into the system prompt on EVERY turn
 * (lib/knowledge/store.ts → readGlobalKnowledgeBlock).
 */

export async function GET() {
  const docs = await listKnowledge();
  return NextResponse.json({ docs });
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    content?: unknown;
  };
  const name = typeof body.name === "string" ? body.name : "";
  const content = typeof body.content === "string" ? body.content : "";
  const r = await addKnowledge(name, content);
  if (!r.ok) {
    return NextResponse.json({ error: r.error }, { status: 400 });
  }
  return NextResponse.json({ doc: r.doc });
}

export async function PATCH(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    enabled?: unknown;
  };
  const name = typeof body.name === "string" ? body.name : "";
  const enabled = body.enabled === true;
  const ok = await setKnowledgeEnabled(name, enabled);
  if (!ok) return NextResponse.json({ error: "invalid name" }, { status: 400 });
  return NextResponse.json({ ok: true });
}

export async function DELETE(req: Request) {
  const url = new URL(req.url);
  const name = url.searchParams.get("name") ?? "";
  const ok = await removeKnowledge(name);
  if (!ok) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ ok: true });
}
