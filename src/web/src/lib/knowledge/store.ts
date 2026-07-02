import "server-only";
import os from "node:os";
import path from "node:path";
import { createKnowledgeStore, type KnowledgeDoc } from "./files";

// Personal-scoped knowledge documents (Settings → Knowledge) — reference
// material injected into EVERY chat, unlike the workspace-scoped store
// (lib/workspace/knowledge.ts) which only applies to that workspace's
// turns. Files live at ~/.jarvis/knowledge/, next to the rest of the
// user's JARVIS state.
//
// History: the original backend was deleted by a concurrent agent session
// (2026-06-25) leaving a stub; rebuilt 2026-07-02 on the shared
// createKnowledgeStore core.

export type { KnowledgeDoc };

const store = createKnowledgeStore({
  root: path.join(os.homedir(), ".jarvis", "knowledge"),
  blockHeader: "Personal knowledge",
  blockIntro:
    "The following documents are reference material the user uploaded for all conversations. Treat them as authoritative for facts about the user, their projects, brand, or domain.",
});

export async function listKnowledge(): Promise<KnowledgeDoc[]> {
  return store.list();
}

export async function addKnowledge(
  name: string,
  content: string,
): Promise<{ ok: true; doc: KnowledgeDoc } | { ok: false; error: string }> {
  return store.add(name, content);
}

export async function removeKnowledge(name: string): Promise<boolean> {
  return store.remove(name);
}

export async function setKnowledgeEnabled(
  name: string,
  enabled: boolean,
): Promise<boolean> {
  return store.setEnabled(name, enabled);
}

/**
 * Enabled personal docs → system-prompt block. Consumed by the chat route
 * as `finalSystem += await readGlobalKnowledgeBlock()`; empty string when
 * no docs are enabled.
 */
export async function readGlobalKnowledgeBlock(): Promise<string> {
  return store.readBlock();
}
