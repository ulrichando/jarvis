import "server-only";
import path from "node:path";
import { workspaceRoot, WORKSPACES_ROOT } from "./storage";
import { createKnowledgeStore, type KnowledgeDoc } from "@/lib/knowledge/files";

// Workspace-scoped knowledge documents. Stored as plaintext files
// under `<workspace>/.jarvis/knowledge/` so they're invisible to the
// build but available to the chat layer for system-prompt injection.
// Core logic lives in lib/knowledge/files.ts (shared with the
// personal-scoped store); this module binds it to the workspace root
// and keeps the original workspaceId-first API.

export type { KnowledgeDoc };

const store = (workspaceId: string) => {
  // workspaceRoot() regex-validates the id (throws on separators / abs paths);
  // re-prove the derived root stays inside WORKSPACES_ROOT anyway — the
  // explicit path-injection barrier for every fs op in the store.
  const wsRoot = path.resolve(workspaceRoot(workspaceId));
  if (!wsRoot.startsWith(path.resolve(WORKSPACES_ROOT) + path.sep)) {
    throw new Error(`workspace root escapes WORKSPACES_ROOT: ${workspaceId}`);
  }
  return createKnowledgeStore({
    root: path.join(wsRoot, ".jarvis", "knowledge"),
    blockHeader: "Workspace knowledge",
    blockIntro:
      "The following documents are reference material for this project. Treat them as authoritative for facts about the project, brand, or domain.",
  });
};

export async function listKnowledge(workspaceId: string): Promise<KnowledgeDoc[]> {
  return store(workspaceId).list();
}

export async function addKnowledge(
  workspaceId: string,
  name: string,
  content: string,
): Promise<{ ok: true; doc: KnowledgeDoc } | { ok: false; error: string }> {
  return store(workspaceId).add(name, content);
}

export async function removeKnowledge(
  workspaceId: string,
  name: string,
): Promise<boolean> {
  return store(workspaceId).remove(name);
}

export async function setKnowledgeEnabled(
  workspaceId: string,
  name: string,
  enabled: boolean,
): Promise<boolean> {
  return store(workspaceId).setEnabled(name, enabled);
}

/**
 * Read all enabled knowledge docs and concatenate them into a single
 * string suitable for appending to the chat system prompt.
 */
export async function readKnowledgeBlock(workspaceId: string): Promise<string> {
  return store(workspaceId).readBlock();
}
