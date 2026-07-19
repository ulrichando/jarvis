// Defensive sanitizer for Mermaid diagram source.
//
// If a raw `</jarvisArtifact>` close tag (or a trailing partial of one,
// from a streaming chunk boundary) ever leaks into artifact content,
// Mermaid's parser throws (`Expecting 'SEMI','NEWLINE',… got 'TAGSTART'`)
// and the diagram dies. The streaming parser now holds split close tags
// back at chunk boundaries, but a diagram should never fail to render
// because of a stray wrapper tag regardless of which path produced the
// string — so strip from the first close-tag occurrence onward (case-
// insensitive, `>` optional), drop any trailing partial prefix of the
// tag, and trim.
const CLOSE_TAG_PREFIX = "</jarvisartifact";

export function sanitizeMermaidSource(source: string): string {
  let out = source;
  const idx = out.toLowerCase().indexOf(CLOSE_TAG_PREFIX);
  if (idx !== -1) out = out.slice(0, idx);
  // Trailing partial close tag from a mid-stream chunk boundary
  // (`…end</jarvisArtif`). Longest suffix that is a prefix of the tag;
  // minimum 2 chars (`</`) so a lone `<` inside a label is left alone.
  const lower = out.toLowerCase();
  for (let k = Math.min(CLOSE_TAG_PREFIX.length, lower.length); k >= 2; k--) {
    if (lower.endsWith(CLOSE_TAG_PREFIX.slice(0, k))) {
      out = out.slice(0, out.length - k);
      break;
    }
  }
  return out.trim();
}
