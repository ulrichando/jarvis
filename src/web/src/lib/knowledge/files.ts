import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";

// File-backed knowledge store, parameterized by root directory. Two
// instances exist: workspace-scoped (lib/workspace/knowledge.ts, rooted at
// <workspace>/.jarvis/knowledge) and personal-scoped (lib/knowledge/store.ts,
// rooted at ~/.jarvis/knowledge). Extracted 2026-07-02 from the workspace
// module so the global twin (deleted 2026-06-25) could be restored without
// a second copy-paste of the same logic.
//
// V1 model: NO embeddings, NO chunking. Each file is read whole and
// truncated to 4K chars before being appended to the system prompt.
// That's enough for typical "project rules" / "brand tone" / "API
// contract" documents and avoids the infrastructure cost of vector
// store + embeddings provider config. Real RAG with chunking +
// retrieval is a V2 — the file format here is forward-compatible
// (we just add a chunks/embeddings step on read).

export type KnowledgeDoc = {
  name: string;
  bytes: number;
  /** Unix timestamp ms — last modified time on disk. */
  updatedAt: number;
  enabled: boolean;
};

const MAX_FILE_BYTES = 1 * 1024 * 1024; // 1MB per file
const MAX_TOTAL_DOCS = 50;

type Meta = { disabled: string[] };

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

export function createKnowledgeStore(opts: {
  root: string;
  /** Heading of the injected system-prompt block, e.g. "Workspace knowledge". */
  blockHeader: string;
  /** One-line intro under the heading, telling the model what the docs are. */
  blockIntro: string;
}) {
  const { root, blockHeader, blockIntro } = opts;
  const metaPath = path.join(root, "_meta.json");

  async function loadMeta(): Promise<Meta> {
    try {
      const raw = await fs.readFile(metaPath, "utf8");
      const j = JSON.parse(raw) as Partial<Meta>;
      return { disabled: Array.isArray(j.disabled) ? j.disabled : [] };
    } catch {
      return { disabled: [] };
    }
  }

  async function saveMeta(meta: Meta): Promise<void> {
    await fs.mkdir(root, { recursive: true });
    await fs.writeFile(metaPath, JSON.stringify(meta, null, 2));
  }

  async function list(): Promise<KnowledgeDoc[]> {
    const meta = await loadMeta();
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

  async function add(
    name: string,
    content: string,
  ): Promise<{ ok: true; doc: KnowledgeDoc } | { ok: false; error: string }> {
    const safe = safeName(name);
    if (!safe) return { ok: false, error: "invalid name" };
    if (content.length === 0) return { ok: false, error: "empty content" };
    if (Buffer.byteLength(content, "utf8") > MAX_FILE_BYTES) {
      return { ok: false, error: "file too large (max 1MB)" };
    }
    const existing = await list();
    if (existing.length >= MAX_TOTAL_DOCS && !existing.find((d) => d.name === safe)) {
      return { ok: false, error: `cap reached (max ${MAX_TOTAL_DOCS} docs)` };
    }
    await fs.mkdir(root, { recursive: true });
    const target = path.join(root, safe);
    await fs.writeFile(target, content, "utf8");
    const stat = await fs.stat(target);
    const meta = await loadMeta();
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

  async function remove(name: string): Promise<boolean> {
    const safe = safeName(name);
    if (!safe) return false;
    try {
      await fs.unlink(path.join(root, safe));
      // Also clear from disabled list if present.
      const meta = await loadMeta();
      if (meta.disabled.includes(safe)) {
        meta.disabled = meta.disabled.filter((n) => n !== safe);
        await saveMeta(meta);
      }
      return true;
    } catch {
      return false;
    }
  }

  async function setEnabled(name: string, enabled: boolean): Promise<boolean> {
    const safe = safeName(name);
    if (!safe) return false;
    const meta = await loadMeta();
    const wasDisabled = meta.disabled.includes(safe);
    if (enabled && wasDisabled) {
      meta.disabled = meta.disabled.filter((n) => n !== safe);
    } else if (!enabled && !wasDisabled) {
      meta.disabled = [...meta.disabled, safe];
    } else {
      return true; // already in target state
    }
    await saveMeta(meta);
    return true;
  }

  /**
   * Read all enabled knowledge docs and concatenate them into a single
   * string suitable for appending to the chat system prompt. Each doc
   * is truncated to 4K chars to keep the total bounded; the chat route
   * decides whether to include the result based on its own budget.
   */
  async function readBlock(): Promise<string> {
    const docs = await list();
    const enabled = docs.filter((d) => d.enabled);
    if (enabled.length === 0) return "";
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
    return `\n\n## ${blockHeader}\n${blockIntro}\n\n${parts.join("\n\n")}\n`;
  }

  return { list, add, remove, setEnabled, readBlock };
}
