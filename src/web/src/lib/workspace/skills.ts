import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import { workspaceRoot } from "./storage";

// Workspace-scoped skills — reusable prompt + shell macros the chat
// layer can invoke as `/skill-name [args]`. Stored as markdown with
// YAML frontmatter at `.jarvis/skills/<name>.md`, mirroring Anthropic's
// skills format so files are forward-compatible if the format becomes
// a wider standard.
//
// Frontmatter shape:
//   ---
//   name: <kebab-case-id>
//   description: <one line>
//   kind: prompt | shell
//   ---
//   <markdown body — the prompt template OR the shell command>
//
// V1 model: skills are stored + listed + edited via the Settings UI.
// Slash-command resolution in the composer is a V2 — for now skills
// document themselves and live alongside the project.

const SKILLS_DIR = ".jarvis/skills";
const MAX_SKILL_BYTES = 64 * 1024;
const NAME_RE = /^[a-z][a-z0-9-]*$/;

export type Skill = {
  name: string;
  description: string;
  kind: "prompt" | "shell";
  body: string;
  bytes: number;
  updatedAt: number;
};

function skillsRoot(workspaceId: string): string {
  return path.join(workspaceRoot(workspaceId), SKILLS_DIR);
}

function safeName(name: string): string | null {
  const t = name.trim().toLowerCase();
  if (!NAME_RE.test(t) || t.length > 60) return null;
  return t;
}

function parseFrontmatter(raw: string): {
  meta: Record<string, string>;
  body: string;
} {
  const m = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return { meta: {}, body: raw };
  const meta: Record<string, string> = {};
  for (const line of m[1].split("\n")) {
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const k = line.slice(0, colon).trim();
    const v = line.slice(colon + 1).trim();
    if (k) meta[k] = v;
  }
  return { meta, body: m[2] };
}

function buildFile(skill: Omit<Skill, "bytes" | "updatedAt">): string {
  return [
    "---",
    `name: ${skill.name}`,
    `description: ${skill.description.replace(/\n/g, " ")}`,
    `kind: ${skill.kind}`,
    "---",
    "",
    skill.body,
    "",
  ].join("\n");
}

export async function listSkills(workspaceId: string): Promise<Skill[]> {
  const root = skillsRoot(workspaceId);
  let names: string[];
  try {
    names = await fs.readdir(root);
  } catch {
    return [];
  }
  const out: Skill[] = [];
  for (const f of names) {
    if (!f.endsWith(".md")) continue;
    try {
      const abs = path.join(root, f);
      const stat = await fs.stat(abs);
      const raw = await fs.readFile(abs, "utf8");
      const { meta, body } = parseFrontmatter(raw);
      const name = meta.name || f.replace(/\.md$/, "");
      out.push({
        name,
        description: meta.description ?? "",
        kind: meta.kind === "shell" ? "shell" : "prompt",
        body: body.trim(),
        bytes: stat.size,
        updatedAt: stat.mtimeMs,
      });
    } catch {
      /* skip malformed */
    }
  }
  out.sort((a, b) => a.name.localeCompare(b.name));
  return out;
}

export async function saveSkill(
  workspaceId: string,
  skill: Pick<Skill, "name" | "description" | "kind" | "body">,
): Promise<{ ok: true; skill: Skill } | { ok: false; error: string }> {
  const safe = safeName(skill.name);
  if (!safe) {
    return {
      ok: false,
      error: "name must be lowercase, kebab-case, alphanumeric (max 60 chars)",
    };
  }
  if (skill.kind !== "prompt" && skill.kind !== "shell") {
    return { ok: false, error: "kind must be 'prompt' or 'shell'" };
  }
  if (!skill.body.trim()) {
    return { ok: false, error: "body required" };
  }
  const root = skillsRoot(workspaceId);
  await fs.mkdir(root, { recursive: true });
  const file = buildFile({
    name: safe,
    description: skill.description.trim().slice(0, 200),
    kind: skill.kind,
    body: skill.body,
  });
  if (Buffer.byteLength(file, "utf8") > MAX_SKILL_BYTES) {
    return { ok: false, error: "skill too large (max 64K)" };
  }
  const target = path.join(root, `${safe}.md`);
  await fs.writeFile(target, file, "utf8");
  const stat = await fs.stat(target);
  return {
    ok: true,
    skill: {
      name: safe,
      description: skill.description.trim().slice(0, 200),
      kind: skill.kind,
      body: skill.body.trim(),
      bytes: stat.size,
      updatedAt: stat.mtimeMs,
    },
  };
}

export async function deleteSkill(
  workspaceId: string,
  name: string,
): Promise<boolean> {
  const safe = safeName(name);
  if (!safe) return false;
  try {
    await fs.unlink(path.join(skillsRoot(workspaceId), `${safe}.md`));
    return true;
  } catch {
    return false;
  }
}
