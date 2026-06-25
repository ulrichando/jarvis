// MINIMAL RECONSTRUCTION (2026-06-25). The original src/lib/knowledge/store.ts
// was deleted by a concurrent agent session and is NOT recoverable from git (it
// was untracked). The knowledge backend (the rest of src/lib/knowledge/* and
// src/app/api/knowledge/*) was deleted in the same sweep, so there is no global
// knowledge to inject — this returns an empty block. Restore the original if
// that session has it; this file is safe to overwrite.

/**
 * The original read the user's global knowledge entries and rendered them as a
 * system-prompt block. With no knowledge backend it contributes nothing.
 * Consumed as `finalSystem += await readGlobalKnowledgeBlock()`.
 */
export async function readGlobalKnowledgeBlock(): Promise<string> {
  return "";
}
