import { NextResponse } from "next/server";
import { deleteSkill, listSkills, saveSkill } from "@/lib/skills";

export const runtime = "nodejs";

/**
 * Personal skills API (Settings → Skills).
 *
 * GET     /api/skills         → list skills
 * POST    /api/skills         body: { name, description, whenToUse?, body } → create/replace
 * DELETE  /api/skills?name=   → recoverable delete (moved to ~/.jarvis/.skills-trash/)
 *
 * Files live at ~/.jarvis/skills/<name>/SKILL.md — the SAME store the
 * jarvis CLI (src/cli/src/skills/loadSkillsDir.ts) and the voice agent
 * (skills_list / skill_view / skill_manage tools) read, in the frontmatter
 * format both parse (name / description / when_to_use). See lib/skills.ts.
 *
 * Auth: covered by the proxy.ts network gate exactly like /api/knowledge —
 * same-origin browser requests need a session cookie; non-browser callers
 * need the bearer token. No SELF_AUTH entry (this route must NOT bypass the
 * shared-token gate).
 */

export async function GET() {
  const skills = await listSkills();
  return NextResponse.json({ skills });
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    description?: unknown;
    whenToUse?: unknown;
    body?: unknown;
  };
  const r = await saveSkill({
    name: typeof body.name === "string" ? body.name : "",
    description: typeof body.description === "string" ? body.description : "",
    whenToUse: typeof body.whenToUse === "string" ? body.whenToUse : "",
    body: typeof body.body === "string" ? body.body : "",
  });
  if (!r.ok) {
    return NextResponse.json({ error: r.error }, { status: 400 });
  }
  return NextResponse.json({ skill: r.skill });
}

export async function DELETE(req: Request) {
  const url = new URL(req.url);
  const name = url.searchParams.get("name") ?? "";
  const ok = await deleteSkill(name);
  if (!ok) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ ok: true });
}
