import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import { workspaceRoot } from "./storage";

// Workspace-scoped knowledge documents. Stored as plaintext files
// under `.jarvis/knowledge/` so they're invisible to the build but
// available to the chat layer for system-prompt injection.
//
// V1 model: NO embeddings, NO chunking. Each file is read whole and
// truncated to 4K chars before being appended to the system prompt.
// That's enough for typical "project rules" / "brand tone" / "API
// contract" documents and avoids the infrastructure cost of vector
// store + embeddings provider config. Real RAG with chunking +
// retrieval is a V2 — the file format here is forward-compatible
// (we just add a chunks/embeddings step on read).

const KNOWLEDGE_DIR = ".jarvis/knowledge";

export type KnowledgeDoc = {
  name: string;
  bytes: number;
  /** Unix timestamp ms — last modified time on disk. */
  updatedAt: number;
  enabled: boolean;
};

const MAX_FILE_BYTES = 1 * 1024 * 1024; // 1MB per file
const MAX_TOTAL_DOCS = 50;

function knowledgeRoot(workspaceId: string): string {
  return path.join(workspaceRoot(workspaceId), KNOWLEDGE_DIR);
}

function metaPath(workspaceId: string): string {
  return path.join(knowledgeRoot(workspaceId), "_meta.json");
}

type Meta = { disabled: string[] };

async function loadMeta(workspaceId: string): Promise<Meta> {
  try {
    const raw = await fs.readFile(metaPath(workspaceId), "utf8");
    const j = JSON.parse(raw) as Partial<Meta>;
    return { disabled: Array.isArray(j.disabled) ? j.disabled : [] };
  } catch {
    return { disabled: [] };
  }
}

async function saveMeta(workspaceId: string, meta: Meta): Promise<void> {
  await fs.mkdir(knowledgeRoot(workspaceId), { recursive: true });
  await fs.writeFile(metaPath(workspaceId), JSON.stringify(meta, null, 2));
}

// Sanitize document name: strip path traversal, illegal chars, leading
// dots. Caller-supplied names go straight into the filesystem so this
// is a security boundary.
function safeName(name: string): string | null {
  const base = path.basename(name).trim();
  if (!base || base.startsWith(".")) return null;
  if (!/^[A-Za-z0-9._\- ]+$/.test(base)) return null;
  if (base.length > 200) return null;
  return base;
}

export async function listKnowledge(
  workspaceId: string,
): Promise<KnowledgeDoc[]> {
  const root = knowledgeRoot(workspaceId);
  const meta = await loadMeta(workspaceId);
  let entries: { name: string; isFile: boolean }[];
  try {
    const raw = await fs.readdir(root, { withFileTypes: true });
    entries = raw.map((e) => ({ name: e.name, isFile: e.isFile() }));
  } catch {
    return [];
  }
  const out: KnowledgeDoc[] = [];
  for (const e of entries) {
    if (!e.isFile) continue;
    if (e.name === "_meta.json") continue;
    if (e.name.startsWith(".")) continue;
    try {
      const stat = await fs.stat(path.join(root, e.name));
      out.push({
        name: e.name,
        bytes: stat.size,
        updatedAt: stat.mtimeMs,
        enabled: !meta.disabled.includes(e.name),
      });
    } catch {
      /* skipped */
    }
  }
  out.sort((a, b) => b.updatedAt - a.updatedAt);
  return out;
}

export async function addKnowledge(
  workspaceId: string,
  name: string,
  content: string,
): Promise<{ ok: true; doc: KnowledgeDoc } | { ok: false; error: string }> {
  const safe = safeName(name);
  if (!safe) return { ok: false, error: "invalid name" };
  if (content.length === 0) return { ok: false, error: "empty content" };
  if (Buffer.byteLength(content, "utf8") > MAX_FILE_BYTES) {
    return { ok: false, error: "file too large (max 1MB)" };
  }
  const existing = await listKnowledge(workspaceId);
  if (existing.length >= MAX_TOTAL_DOCS && !existing.find((d) => d.name === safe)) {
    return { ok: false, error: `cap reached (max ${MAX_TOTAL_DOCS} docs)` };
  }
  const root = knowledgeRoot(workspaceId);
  await fs.mkdir(root, { recursive: true });
  const target = path.join(root, safe);
  await fs.writeFile(target, content, "utf8");
  const stat = await fs.stat(target);
  const meta = await loadMeta(workspaceId);
  return {
    ok: true,
    doc: {
      name: safe,
      bytes: stat.size,
      updatedAt: stat.mtimeMs,
      enabled: !meta.disabled.includes(safe),
    },
  };
}

export async function removeKnowledge(
  workspaceId: string,
  name: string,
): Promise<boolean> {
  const safe = safeName(name);
  if (!safe) return false;
  try {
    await fs.unlink(path.join(knowledgeRoot(workspaceId), safe));
    // Also clear from disabled list if present.
    const meta = await loadMeta(workspaceId);
    if (meta.disabled.includes(safe)) {
      meta.disabled = meta.disabled.filter((n) => n !== safe);
      await saveMeta(workspaceId, meta);
    }
    return true;
  } catch {
    return false;
  }
}

export async function setKnowledgeEnabled(
  workspaceId: string,
  name: string,
  enabled: boolean,
): Promise<boolean> {
  const safe = safeName(name);
  if (!safe) return false;
  const meta = await loadMeta(workspaceId);
  const wasDisabled = meta.disabled.includes(safe);
  if (enabled && wasDisabled) {
    meta.disabled = meta.disabled.filter((n) => n !== safe);
  } else if (!enabled && !wasDisabled) {
    meta.disabled = [...meta.disabled, safe];
  } else {
    return true; // already in target state
  }
  await saveMeta(workspaceId, meta);
  return true;
}

/**
 * Read all enabled knowledge docs and concatenate them into a single
 * string suitable for appending to the chat system prompt. Each doc
 * is truncated to 4K chars to keep the total bounded; the chat route
 * decides whether to include the result based on its own budget.
 */
export async function readKnowledgeBlock(
  workspaceId: string,
): Promise<string> {
  const docs = await listKnowledge(workspaceId);
  const enabled = docs.filter((d) => d.enabled);
  if (enabled.length === 0) return "";
  const root = knowledgeRoot(workspaceId);
  const parts: string[] = [];
  for (const d of enabled) {
    try {
      const raw = await fs.readFile(path.join(root, d.name), "utf8");
      const trimmed = raw.length > 4096 ? raw.slice(0, 4096) + "\n…[truncated]" : raw;
      parts.push(`### ${d.name}\n${trimmed}`);
    } catch {
      /* missing — skip */
    }
  }
  if (parts.length === 0) return "";
  return `\n\n## Workspace knowledge\nThe following documents are reference material for this project. Treat them as authoritative for facts about the project, brand, or domain.\n\n${parts.join("\n\n")}\n`;
}
