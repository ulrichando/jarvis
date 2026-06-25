// MINIMAL RECONSTRUCTION (2026-06-25). The original src/hooks/use-skills.ts was
// deleted by a concurrent agent session and is NOT recoverable from git (it was
// untracked). The skills backend it talked to (src/lib/skills/* +
// src/app/api/skills/*) was deleted in the same sweep, so there is no data
// source: useSkills returns no skills and expandSkill is a passthrough. This
// keeps the web build green; restore the real files from that session's working
// copy if it still has them — this file is safe to overwrite.

export type Skill = {
  id: string;
  name: string;
  content?: string;
};

/**
 * React-Query-shaped result consumed as `const { data: skills } = useSkills()`.
 * No skills backend exists anymore, so `data` is always an empty list.
 */
export function useSkills(): { data: Skill[] } {
  return { data: [] };
}

/**
 * The original expanded a leading `/<skill-name>` command in `text` into that
 * skill's body. With no skills available it returns the text unchanged.
 */
export function expandSkill(text: string, _skills: Skill[]): string {
  return text;
}
