import "server-only";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

// Personal-scoped skills (Settings → Skills) — reusable recipes shared with
// the jarvis CLI and the voice agent. This store reads/writes the SAME
// directory those runtimes consume:
//
//   ~/.jarvis/skills/<name>/SKILL.md
//
// Interop format — the intersection both runtime consumers actually parse:
//
//   - CLI (src/cli/src/skills/loadSkillsDir.ts): user skills root is
//     ~/.jarvis/skills (getClaudeConfigHomeDir()/skills) and ONLY the
//     directory layout <name>/SKILL.md is loaded ("Single .md files are NOT
//     supported in /skills/ directory"). The skill's canonical name is the
//     DIRECTORY name; frontmatter `name` is display-only. It reads
//     `description` and `when_to_use` from frontmatter.
//   - Voice agent (src/voice-agent/pipeline/skills_loader.py): reads both
//     <name>/SKILL.md and flat <name>.md; REQUIRES frontmatter `name` and
//     `description`, `when_to_use` optional (defaults to description).
//
// So the writer always produces the directory layout with frontmatter
// name / description / when_to_use, byte-shape mirroring the voice agent's
// own writer (src/voice-agent/pipeline/skills_authoring.py::render_skill_md),
// and validation limits mirror skills_authoring.py (name regex/64, desc 1024,
// content 100k). The workspace-scoped lib's `kind: prompt|shell` frontmatter
// (lib/workspace/skills.ts) is deliberately NOT written here — no runtime
// consumer parses it.
//
// listSkills also surfaces flat <name>.md files (voice-agent legacy layout)
// so nothing already on disk is invisible in the UI; saving a skill whose
// name matches an existing flat file upgrades it to the directory layout.
// Deletion mirrors the voice agent's recoverable delete: entries move to
// ~/.jarvis/.skills-trash/ (outside the discovery tree), never rm -rf.

const SKILLS_ROOT = path.join(os.homedir(), ".jarvis", "skills");
const TRASH_DIRNAME = ".skills-trash";

// Limits mirror src/voice-agent/pipeline/skills_authoring.py
const MAX_NAME_LENGTH = 64;
const MAX_DESCRIPTION_LENGTH = 1024;
const MAX_SKILL_CONTENT_CHARS = 100_000;
const NAME_RE = /^[a-z0-9][a-z0-9-]*$/;

export type PersonalSkill = {
  name: string;
  description: string;
  whenToUse: string;
  bytes: number;
  updatedAt: number;
  layout: "dir" | "flat";
};

export function safeSkillName(name: string): string | null {
  const t = name.trim().toLowerCase();
  if (!NAME_RE.test(t) || t.length > MAX_NAME_LENGTH) return null;
  return t;
}

/**
 * Minimal frontmatter parser matching the voice agent's hand-rolled one
 * (skills_loader.py::_parse_frontmatter): flat key/value pairs plus
 * block-scalar (`|` / `>`) multi-line values. List items and nested keys
 * (present in some hand-authored skills) are skipped, not errors.
 */
export function parseSkillFrontmatter(raw: string): {
  meta: Record<string, string>;
  body: string;
} {
  const m = raw.match(/^---[ \t]*\n([\s\S]*?)\n---[ \t]*\n?([\s\S]*)$/);
  if (!m) return { meta: {}, body: raw };
  const meta: Record<string, string> = {};
  const lines = m[1].split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    i++;
    const stripped = line.trim();
    if (!stripped) continue;
    // Indented lines outside a block scalar are list items / nested maps of
    // keys we don't consume — skip.
    if (/^[ \t]/.test(line)) continue;
    const colon = stripped.indexOf(":");
    if (colon <= 0) continue;
    const key = stripped.slice(0, colon).trim();
    let val = stripped.slice(colon + 1).trim();
    if (val === "|" || val === ">") {
      const block: string[] = [];
      let indent: number | null = null;
      while (i < lines.length) {
        const ln = lines[i];
        if (ln.trim() && !/^[ \t]/.test(ln)) break;
        if (ln.trim() && indent === null)
          indent = ln.length - ln.trimStart().length;
        block.push(indent !== null ? ln.slice(indent) : ln);
        i++;
      }
      val = block.join("\n").trim();
    } else if (
      (val.startsWith('"') && val.endsWith('"') && val.length >= 2) ||
      (val.startsWith("'") && val.endsWith("'") && val.length >= 2)
    ) {
      val = val.slice(1, -1);
    }
    meta[key] = val;
  }
  return { meta, body: m[2] };
}

/**
 * Compose SKILL.md — byte-shape mirror of the voice agent's
 * skills_authoring.py::render_skill_md (block scalar with 2-space indent
 * for multi-line when_to_use).
 */
function renderSkillMd(
  name: string,
  description: string,
  whenToUse: string,
  body: string,
): string {
  const lines = ["---", `name: ${name}`, `description: ${description}`];
  if (whenToUse) {
    if (whenToUse.includes("\n")) {
      lines.push("when_to_use: |");
      for (const ln of whenToUse.split("\n")) lines.push(`  ${ln}`);
    } else {
      lines.push(`when_to_use: ${whenToUse}`);
    }
  }
  lines.push("---");
  return lines.join("\n") + "\n\n" + body.trim() + "\n";
}

async function statOrNull(p: string) {
  try {
    return await fs.stat(p);
  } catch {
    return null;
  }
}

export async function listSkills(
  root: string = SKILLS_ROOT,
): Promise<PersonalSkill[]> {
  let entries;
  try {
    entries = await fs.readdir(root, { withFileTypes: true });
  } catch {
    return [];
  }
  const out: PersonalSkill[] = [];
  for (const ent of entries) {
    let file: string;
    let name: string;
    let layout: "dir" | "flat";
    if (ent.isDirectory()) {
      name = ent.name;
      file = path.join(root, ent.name, "SKILL.md");
      layout = "dir";
    } else if (ent.isFile() && ent.name.endsWith(".md")) {
      name = ent.name.replace(/\.md$/, "");
      file = path.join(root, ent.name);
      layout = "flat";
    } else {
      continue;
    }
    try {
      const stat = await fs.stat(file);
      const raw = await fs.readFile(file, "utf8");
      const { meta } = parseSkillFrontmatter(raw);
      out.push({
        // Canonical identity is the path (directory / file name) — that's
        // what the CLI keys on and what save/delete address. Frontmatter
        // `name` should always match for skills we author.
        name,
        description: meta.description ?? "",
        whenToUse: meta.when_to_use ?? "",
        bytes: stat.size,
        updatedAt: stat.mtimeMs,
        layout,
      });
    } catch {
      /* dir without SKILL.md, unreadable file — skip */
    }
  }
  out.sort((a, b) => a.name.localeCompare(b.name));
  return out;
}

export async function saveSkill(
  input: {
    name: string;
    description: string;
    whenToUse?: string;
    body: string;
  },
  root: string = SKILLS_ROOT,
): Promise<{ ok: true; skill: PersonalSkill } | { ok: false; error: string }> {
  const name = safeSkillName(input.name);
  if (!name) {
    return {
      ok: false,
      error: `name must be lowercase letters, digits, and hyphens (max ${MAX_NAME_LENGTH} chars)`,
    };
  }
  // Newlines in single-line frontmatter values would break out of the field
  // (frontmatter injection) — collapse, like lib/workspace/skills.ts does.
  const description = input.description.replace(/\n/g, " ").trim();
  if (!description) return { ok: false, error: "description required" };
  if (description.length > MAX_DESCRIPTION_LENGTH) {
    return {
      ok: false,
      error: `description exceeds ${MAX_DESCRIPTION_LENGTH} characters`,
    };
  }
  const whenToUse = (input.whenToUse ?? "").trim();
  const body = input.body.trim();
  if (!body) return { ok: false, error: "body required" };

  const content = renderSkillMd(name, description, whenToUse, body);
  if (content.length > MAX_SKILL_CONTENT_CHARS) {
    return {
      ok: false,
      error: `skill too large (max ${MAX_SKILL_CONTENT_CHARS} characters)`,
    };
  }

  const dir = path.join(root, name);
  const target = path.join(dir, "SKILL.md");
  try {
    await fs.mkdir(dir, { recursive: true });
    // Atomic write (temp + rename in the same dir), mirroring the voice
    // agent's _atomic_write_text — a crashed write never leaves a truncated
    // SKILL.md for the CLI/voice loaders to trip on.
    const tmp = path.join(dir, `.tmp-${Date.now()}-${process.pid}.md`);
    await fs.writeFile(tmp, content, "utf8");
    await fs.rename(tmp, target);
  } catch (e) {
    return { ok: false, error: `could not write skill: ${String(e)}` };
  }
  // Upgrade path: a flat <name>.md would shadow/duplicate the dir entry in
  // the voice loader — remove it once the dir layout exists.
  await fs.unlink(path.join(root, `${name}.md`)).catch(() => {});

  const stat = await fs.stat(target);
  return {
    ok: true,
    skill: {
      name,
      description,
      whenToUse,
      bytes: stat.size,
      updatedAt: stat.mtimeMs,
      layout: "dir",
    },
  };
}

/**
 * Recoverable delete — move to <root>/../.skills-trash/<name>-<stamp>,
 * mirroring the voice agent's delete_user_skill (never rm -rf, and the
 * trash lives OUTSIDE the discovery tree so trashed skills aren't
 * re-discovered by any consumer).
 */
export async function deleteSkill(
  name: string,
  root: string = SKILLS_ROOT,
): Promise<boolean> {
  const safe = safeSkillName(name);
  if (!safe) return false;
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\..*$/, "Z");
  const trashRoot = path.join(path.dirname(root), TRASH_DIRNAME);

  const dir = path.join(root, safe);
  if (await statOrNull(path.join(dir, "SKILL.md"))) {
    try {
      await fs.mkdir(trashRoot, { recursive: true });
      await fs.rename(dir, path.join(trashRoot, `${safe}-${stamp}`));
      return true;
    } catch {
      return false;
    }
  }
  const flat = path.join(root, `${safe}.md`);
  if (await statOrNull(flat)) {
    try {
      await fs.mkdir(trashRoot, { recursive: true });
      await fs.rename(flat, path.join(trashRoot, `${safe}-${stamp}.md`));
      return true;
    } catch {
      return false;
    }
  }
  return false;
}
