// Server-side extraction of claude.ai-style self-contained artifacts
// (System B) from a FINAL assistant message. The streaming parser
// (lib/actions/message-parser.ts) handles the live render path; this runs
// once in chat/route.ts onFinish to persist + version what the model
// emitted. Pure + dependency-free → unit-testable.

import type { ArtifactKind, JarvisArtifact } from "@/lib/actions/types";

const KINDS: readonly ArtifactKind[] = [
  "code",
  "markdown",
  "html",
  "react",
  "svg",
  "mermaid",
];

// Extract a quoted attribute value from a tag's attribute string.
// (Mirrors message-parser's internal extractAttr — kept local so this
// module stays a standalone pure function.)
function attr(attrs: string, name: string): string | undefined {
  const m = attrs.match(new RegExp(`${name}="([^"]*)"`, "i"));
  return m ? m[1] : undefined;
}

// Same normalization the streaming parser applies on close: markdown keeps
// its fences; every other kind gets a stray wrapping ```fence``` removed
// and &lt;/&gt; entities unescaped (some models defensively escape).
function finalize(raw: string, kind: ArtifactKind): string {
  if (kind === "markdown") return raw.trim();
  const fence = raw.match(/^\s*```\w*\n([\s\S]*?)\n\s*```\s*$/);
  const inner = fence ? fence[1] : raw;
  return inner.replace(/&lt;/g, "<").replace(/&gt;/g, ">").trim();
}

export function extractJarvisArtifacts(text: string): JarvisArtifact[] {
  if (!text || !text.toLowerCase().includes("<jarvisartifact")) return [];
  const re = /<jarvisartifact\b([^>]*)>([\s\S]*?)<\/jarvisartifact>/gi;
  const found: JarvisArtifact[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const attrs = m[1];
    const slug = (attr(attrs, "slug") ?? attr(attrs, "id") ?? "").trim();
    if (!slug) continue; // slug is the versioning identity — required
    const kindRaw = (attr(attrs, "kind") ?? "code").toLowerCase();
    const kind = (KINDS as readonly string[]).includes(kindRaw)
      ? (kindRaw as ArtifactKind)
      : "code";
    const title = (attr(attrs, "title") ?? "Artifact").trim();
    const language = attr(attrs, "language")?.trim() || undefined;
    const content = finalize(m[2], kind);
    if (!content) continue;
    found.push({ slug, title, kind, language, content });
  }
  // A model that re-emits the same slug within one message means its latest
  // intent for that turn — keep the last occurrence per slug.
  const bySlug = new Map<string, JarvisArtifact>();
  for (const a of found) bySlug.set(a.slug, a);
  return [...bySlug.values()];
}
