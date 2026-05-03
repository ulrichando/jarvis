import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import { listAllFiles, readFile, writeFile, resolveSafe } from "@/lib/workspace/storage";

// Rule-based fixers: deterministic transforms that fix common errors
// WITHOUT consulting the LLM. Production AI coding tools (Lovable, v0,
// Replit Agent) all ship a battery of these for known patterns —
// burning an LLM turn on a regex-fixable bug is wasteful and slow.
//
// Each rule is independent and idempotent (running twice on a clean
// workspace is a no-op). They're invoked after every JARVIS turn that
// produced file edits, before the verification step decides to
// auto-retry. If a rule fixes something, the next verification re-runs
// fresh and may pass without further LLM turns.

export type FixResult = {
  rule: string;
  filesChanged: string[];
  description: string;
};

type Fixer = (workspaceId: string) => Promise<FixResult | null>;

/**
 * Strip deprecated `<Link href="..."><a>...</a></Link>` patterns.
 * Next.js 13+ rejects these at runtime ("Invalid <Link> with <a> child").
 * We rewrite them to the modern direct-child form.
 *
 * Heuristic: regex matches a single-line OR multi-line Link/a/Link
 * sandwich, captures the href + className from the wrapper or the
 * inner <a>, and emits a flat `<Link href="..." className="...">CONTENT</Link>`.
 *
 * Idempotent: if the file already uses the modern pattern, no change.
 */
const fixLinkAnchorPattern: Fixer = async (workspaceId) => {
  const all = await listAllFiles(workspaceId);
  const tsx = all.filter((p) => p.endsWith(".tsx") || p.endsWith(".jsx"));
  const changed: string[] = [];
  // Match <Link OPENERATTRS> ... <a INNERATTRS> CHILDREN </a> ... </Link>
  // [\s\S] = any char incl newlines. Non-greedy. The wrapper attrs and
  // inner attrs are captured separately so we can merge className.
  const re =
    /<Link\b([^>]*)>\s*<a\b([^>]*)>([\s\S]*?)<\/a>\s*<\/Link>/g;
  for (const rel of tsx) {
    let src: string;
    try {
      src = await readFile(workspaceId, rel);
    } catch {
      continue;
    }
    if (!re.test(src)) {
      re.lastIndex = 0;
      continue;
    }
    re.lastIndex = 0;
    const next = src.replace(re, (_, linkAttrs, aAttrs, body) => {
      // Pull className from inner <a> if Link doesn't already have one.
      const linkHasClass = /\bclassName\s*=/.test(linkAttrs);
      const aClass = aAttrs.match(/\bclassName\s*=\s*("[^"]*"|'[^']*'|\{[^}]*\})/);
      const classNameAttr =
        !linkHasClass && aClass ? ` className=${aClass[1]}` : "";
      // Same logic for `target`, `rel`, `onClick` from the inner <a>.
      const carry = ["target", "rel", "onClick", "title", "aria-label"]
        .map((attr) => {
          const re2 = new RegExp(
            `\\b${attr}\\s*=\\s*("[^"]*"|'[^']*'|\\{[^}]*\\})`,
          );
          const fromLink = re2.test(linkAttrs);
          if (fromLink) return "";
          const m = aAttrs.match(re2);
          return m ? ` ${attr}=${m[1]}` : "";
        })
        .join("");
      return `<Link${linkAttrs.trimEnd()}${classNameAttr}${carry}>${body.trim()}</Link>`;
    });
    if (next !== src) {
      await writeFile(workspaceId, rel, next);
      changed.push(rel);
    }
  }
  if (changed.length === 0) return null;
  return {
    rule: "fix-link-a-child",
    filesChanged: changed,
    description: `Removed deprecated <a> children from ${changed.length} <Link> wrapper(s).`,
  };
};

/**
 * If a file uses `<Link ` but doesn't import it from 'next/link',
 * prepend the import. Common when the model edits a JSX block in place
 * without checking imports.
 */
const ensureLinkImport: Fixer = async (workspaceId) => {
  const all = await listAllFiles(workspaceId);
  const tsx = all.filter((p) => p.endsWith(".tsx") || p.endsWith(".jsx"));
  const changed: string[] = [];
  for (const rel of tsx) {
    let src: string;
    try {
      src = await readFile(workspaceId, rel);
    } catch {
      continue;
    }
    const usesLink = /<Link\b/.test(src);
    if (!usesLink) continue;
    const hasImport =
      /from\s+['"]next\/link['"]/.test(src) || /require\(['"]next\/link['"]\)/.test(src);
    if (hasImport) continue;
    // Insert the import at the top, after any other imports.
    const lines = src.split("\n");
    let insertAt = 0;
    for (let i = 0; i < lines.length; i++) {
      if (/^import\b/.test(lines[i])) insertAt = i + 1;
      else if (lines[i].trim() && !/^\/\/|^\/\*|^\*/.test(lines[i].trim())) break;
    }
    lines.splice(insertAt, 0, `import Link from 'next/link'`);
    await writeFile(workspaceId, rel, lines.join("\n"));
    changed.push(rel);
  }
  if (changed.length === 0) return null;
  return {
    rule: "ensure-link-import",
    filesChanged: changed,
    description: `Added missing 'import Link from "next/link"' to ${changed.length} file(s).`,
  };
};

/**
 * Ensure /workspace/data/ exists if any code references `data/app.db`
 * or `data/dev.db`. SQLite's `better-sqlite3` throws SQLITE_CANTOPEN
 * when the parent directory is missing; the model often forgets to
 * `mkdir -p data` before opening the db.
 */
const ensureDataDir: Fixer = async (workspaceId) => {
  const all = await listAllFiles(workspaceId);
  const candidates = all.filter(
    (p) => p.endsWith(".ts") || p.endsWith(".tsx") || p.endsWith(".js") || p.endsWith(".mjs"),
  );
  let referencesDb = false;
  for (const rel of candidates) {
    try {
      const src = await readFile(workspaceId, rel);
      if (/\bdata\/(?:app|dev)\.db\b/.test(src)) {
        referencesDb = true;
        break;
      }
    } catch {
      /* skip unreadable */
    }
  }
  if (!referencesDb) return null;
  const dataDir = resolveSafe(workspaceId, "data");
  try {
    const stat = await fs.stat(dataDir);
    if (stat.isDirectory()) return null; // already exists
  } catch {
    /* doesn't exist, create */
  }
  await fs.mkdir(dataDir, { recursive: true });
  return {
    rule: "ensure-data-dir",
    filesChanged: ["data/"],
    description: "Created missing data/ directory referenced by SQLite open() calls.",
  };
};

const ALL_FIXERS: Fixer[] = [
  fixLinkAnchorPattern,
  ensureLinkImport,
  ensureDataDir,
];

/**
 * Run every rule-based fixer against the workspace. Returns the
 * subset that actually changed something. Cheap (regex + small file
 * reads) and idempotent — safe to invoke after every artifact.
 */
export async function runFixers(workspaceId: string): Promise<FixResult[]> {
  const out: FixResult[] = [];
  for (const fixer of ALL_FIXERS) {
    try {
      const r = await fixer(workspaceId);
      if (r) out.push(r);
    } catch (err) {
      // A buggy fixer shouldn't break the verify pipeline. Log and
      // continue so the other rules still get a shot.
      console.warn(
        "[fixers] rule failed:",
        (err as Error).message,
      );
    }
  }
  return out;
}

// Suppress "imported path is unused" — `path` is used by ensureDataDir
// indirectly via resolveSafe; explicit reference here keeps the compiler
// honest if we refactor later.
void path;
