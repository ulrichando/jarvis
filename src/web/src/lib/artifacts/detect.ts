// Detect claude.ai-style artifacts from an assistant message's NATURAL
// output — substantial, self-contained fenced code/HTML/SVG/mermaid blocks
// — without requiring the explicit <jarvisArtifact> tag. Mirrors claude.ai's
// heuristic ("significant and self-contained, typically over 15 lines").
// Used both for backfilling existing chat history and as a fallback on new
// turns so the gallery reflects everything substantial, tag or not.
//
// Pure + dependency-free → unit-testable.

import type { ArtifactKind, JarvisArtifact } from "@/lib/actions/types";

const MIN_LINES = 15; // claude.ai's "typically over 15 lines" threshold
const MAX_PER_MESSAGE = 6; // don't let one answer flood the gallery

function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "artifact"
  );
}

function classify(
  lang: string,
  body: string,
): { kind: ArtifactKind; worthy: boolean } {
  const l = lang.toLowerCase();
  const trimmed = body.trimStart();
  const lines = body.split("\n").length;
  const isFullHtml = /^<!doctype html|^<html[\s>]/i.test(trimmed);
  const isSvg = /^<svg[\s>]/i.test(trimmed);
  const hasJsx = /<[A-Za-z][^>]*\/?>|<\/[A-Za-z]/.test(body);
  const hasDefaultExport = /export\s+default/.test(body);

  if (l === "mermaid") return { kind: "mermaid", worthy: lines >= 3 };
  if (l === "svg" || isSvg) return { kind: "svg", worthy: true };
  if (l === "html" || l === "htm" || isFullHtml)
    return { kind: "html", worthy: isFullHtml || lines >= MIN_LINES };
  if (
    (l === "jsx" || l === "tsx" || l === "react") ||
    (["js", "ts", "javascript", "typescript"].includes(l) && hasJsx)
  ) {
    if (hasDefaultExport && hasJsx) return { kind: "react", worthy: true };
    return { kind: "code", worthy: lines >= MIN_LINES };
  }
  if (l === "markdown" || l === "md")
    return { kind: "markdown", worthy: lines >= MIN_LINES };
  if (l === "csv") return { kind: "csv", worthy: lines >= 5 };
  if (l === "json") return { kind: "json", worthy: lines >= MIN_LINES };
  // Any other language → a code artifact if it's substantial.
  return { kind: "code", worthy: lines >= MIN_LINES };
}

const KIND_DEFAULT_TITLE: Record<ArtifactKind, string> = {
  react: "React component",
  html: "HTML page",
  svg: "SVG image",
  mermaid: "Diagram",
  markdown: "Document",
  code: "Code snippet",
  csv: "Data table",
  json: "JSON data",
};

// Look at the ~240 chars of prose before the fence for a title: a markdown
// heading, a bold label, or a referenced filename. Falls back per-kind.
function deriveTitle(before: string, kind: ArtifactKind, lang: string): string {
  const tail = before.slice(-240);
  const heading = tail.match(/(?:^|\n)#{1,4}\s+(.+?)\s*$/m);
  if (heading) return heading[1].slice(0, 80);
  const file = tail.match(/`([\w./-]+\.\w{1,5})`/);
  if (file) return file[1].split("/").pop()!.slice(0, 80);
  const bold = tail.match(/\*\*(.+?)\*\*\s*:?\s*$/m);
  if (bold) return bold[1].slice(0, 80);
  if (kind === "code" && lang) return `${lang} snippet`;
  return KIND_DEFAULT_TITLE[kind];
}

export function detectArtifacts(text: string): JarvisArtifact[] {
  if (!text) return [];
  // Skip if the model already used the explicit tag — that path owns it.
  const stripped = text.replace(
    /<jarvisartifact\b[\s\S]*?<\/jarvisartifact>/gi,
    "",
  );
  const fence = /```([\w.+-]*)[ \t]*\r?\n([\s\S]*?)\r?\n```/g;
  const found: JarvisArtifact[] = [];
  const usedSlugs = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = fence.exec(stripped)) !== null && found.length < MAX_PER_MESSAGE) {
    const lang = m[1] ?? "";
    const body = (m[2] ?? "").replace(/\s+$/, "");
    if (!body.trim()) continue;
    const { kind, worthy } = classify(lang, body);
    if (!worthy) continue;
    const before = stripped.slice(0, m.index);
    const title = deriveTitle(before, kind, lang);
    let slug = slugify(title);
    // Disambiguate two distinct artifacts that derive the same title within
    // one message (so they don't collapse into one).
    let n = 2;
    while (usedSlugs.has(slug)) slug = `${slugify(title)}-${n++}`;
    usedSlugs.add(slug);
    found.push({
      slug,
      title,
      kind,
      language: kind === "code" ? lang || undefined : undefined,
      content: body,
    });
  }
  return found;
}
