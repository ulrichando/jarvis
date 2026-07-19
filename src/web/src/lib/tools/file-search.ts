import { tool } from "ai";
import { z } from "zod";
import { tokenize, truncateAtWord } from "@/lib/search/core";
import { readAllKnowledgeDocs } from "@/lib/knowledge/store";

// fileSearch — lexical "grep over the user's documents" for the chat model.
// Reads the FULL content of the personal knowledge docs (Settings → Knowledge,
// or files attached in the composer, which upload into the same store) and
// returns the best-matching excerpts. This is the on-demand counterpart to the
// wholesale 4K-per-doc system-prompt injection (readGlobalKnowledgeBlock): the
// model calls fileSearch to dig into large docs — or docs the user disabled
// from injection — that don't fit in the prompt.
//
// No embeddings: scoring is distinct-query-token coverage with a light
// frequency tiebreak (same tokenizer as web search). That's the lazy-correct
// V1 the knowledge store was already designed for ("real RAG with chunking +
// embeddings is a V2"); it handles code/markdown/logs/CSV/JSON well.

const inputSchema = z.object({
  query: z
    .string()
    .min(1)
    .describe("What to look for in the user's uploaded documents."),
  fileName: z
    .string()
    .optional()
    .describe(
      "Optional: restrict the search to documents whose name contains this text (case-insensitive).",
    ),
});

/** Most excerpts returned to the model per search. */
const MAX_RESULTS = 6;
/** Per-excerpt char cap (truncated at a word boundary). */
const MAX_EXCERPT_CHARS = 600;
// ponytail: bound the tokenizing work per call so a search over 50 large docs
// can't stall the request path. 4MB of text is far more than any personal doc
// set; raise it (or add real chunk-level indexing) if it ever bites.
const MAX_SCAN_CHARS = 4 * 1024 * 1024;
const CHUNK_LINES = 45;
const CHUNK_CHARS = 1400;
const CHUNK_OVERLAP_LINES = 5;

type Chunk = { lineStart: number; text: string };

/** Split doc content into overlapping line-windows for scoring. */
function chunk(content: string): Chunk[] {
  const lines = content.split("\n");
  const chunks: Chunk[] = [];
  let i = 0;
  while (i < lines.length) {
    let j = i;
    let chars = 0;
    while (j < lines.length && j - i < CHUNK_LINES && chars < CHUNK_CHARS) {
      chars += lines[j].length + 1;
      j++;
    }
    chunks.push({ lineStart: i + 1, text: lines.slice(i, j).join("\n") });
    if (j >= lines.length) break;
    i = Math.max(i + 1, j - CHUNK_OVERLAP_LINES);
  }
  return chunks;
}

/**
 * Coverage-dominant score: +1 per distinct query token present in the chunk,
 * plus a small frequency bonus (capped) so a chunk that merely mentions a term
 * doesn't outrank one that's actually about it. 0 = no query term present.
 */
function scoreChunk(text: string, queryTokens: string[]): number {
  if (queryTokens.length === 0) return 0;
  const freq = new Map<string, number>();
  for (const t of tokenize(text)) freq.set(t, (freq.get(t) ?? 0) + 1);
  let score = 0;
  for (const q of queryTokens) {
    const f = freq.get(q);
    if (f) score += 1 + Math.min(f, 3) * 0.1;
  }
  return score;
}

type Match = { file: string; lineStart: number; excerpt: string; score: number };

export const fileSearchTool = tool({
  description:
    "Search the user's uploaded documents (Settings → Knowledge, or files they attached in chat) for a query and return the most relevant excerpts. " +
    "Use this to answer questions about the user's own files — reports, specs, code, logs, CSVs, notes — instead of guessing. " +
    "Returns excerpts with the source file name and line number.",
  inputSchema,
  execute: async ({ query, fileName }) => {
    try {
      const queryTokens = Array.from(new Set(tokenize(query)));
      let docs = await readAllKnowledgeDocs();
      if (fileName) {
        const needle = fileName.toLowerCase();
        docs = docs.filter((d) => d.name.toLowerCase().includes(needle));
      }
      if (docs.length === 0) {
        return {
          query,
          results: [] as Match[],
          note: fileName
            ? `No uploaded document matches "${fileName}".`
            : "No documents have been uploaded yet. The user can add them in Settings → Knowledge or by attaching a file in chat.",
        };
      }
      if (queryTokens.length === 0) {
        return {
          query,
          results: [] as Match[],
          note: "The query has no searchable terms — try more specific words.",
        };
      }

      const matches: Match[] = [];
      let scanned = 0;
      for (const doc of docs) {
        if (scanned >= MAX_SCAN_CHARS) break;
        for (const c of chunk(doc.content)) {
          scanned += c.text.length;
          if (scanned >= MAX_SCAN_CHARS) break;
          const score = scoreChunk(c.text, queryTokens);
          if (score > 0) {
            matches.push({
              file: doc.name,
              lineStart: c.lineStart,
              excerpt: truncateAtWord(c.text.trim(), MAX_EXCERPT_CHARS),
              score,
            });
          }
        }
      }

      matches.sort((a, b) => b.score - a.score);
      const results = matches.slice(0, MAX_RESULTS).map(({ score, ...m }) => {
        void score; // score drives ranking only; not surfaced to the model
        return m;
      });
      return {
        query,
        results,
        ...(results.length === 0
          ? { note: "No matching passages found in the uploaded documents." }
          : {}),
      };
    } catch (err) {
      console.error("[file-search] failed:", err);
      return {
        query,
        results: [] as Match[],
        error: "Document search is unavailable.",
      };
    }
  },
});
