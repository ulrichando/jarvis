import { NextResponse } from "next/server";
import { listSkills, saveSkill, deleteSkill } from "@/lib/workspace/skills";

export const runtime = "nodejs";

/**
 * GET    /api/workspace/[id]/skills            → list skills
 * POST   /api/workspace/[id]/skills            body: { name, description, kind, body }
 *                                                → create or update
 * DELETE /api/workspace/[id]/skills?name=...   → remove
 */

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const skills = await listSkills(id);
  return NextResponse.json({ skills });
}

export async function POST(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    description?: unknown;
    kind?: unknown;
    body?: unknown;
  };
  const r = await saveSkill(id, {
    name: typeof body.name === "string" ? body.name : "",
    description:
      typeof body.description === "string" ? body.description : "",
    kind:
      body.kind === "shell" || body.kind === "prompt" ? body.kind : "prompt",
    body: typeof body.body === "string" ? body.body : "",
  });
  if (!r.ok) return NextResponse.json({ error: r.error }, { status: 400 });
  return NextResponse.json({ skill: r.skill });
}

export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const name = url.searchParams.get("name") ?? "";
  const ok = await deleteSkill(id, name);
  if (!ok) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ ok: true });
}
