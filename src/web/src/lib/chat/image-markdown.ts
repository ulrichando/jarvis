// MINIMAL RECONSTRUCTION (2026-06-25). The original src/lib/chat/image-markdown.ts
// was deleted by a concurrent agent session and is NOT recoverable from git (it
// was untracked). Image GENERATION itself still works — the explicit Image
// toggle drives it in src/app/api/chat/route.ts — and these helpers render +
// clean the generated-image markdown. Restore the original if that session has
// it; this file is safe to overwrite.
// Structural minimum this module needs from a generated image (the real
// GeneratedImage in @/lib/ai/image is a superset). Kept loose so both the
// route's GeneratedImage[] and the client's {url, prompt}[] satisfy it.
type ImageRef = { url: string; prompt?: string };

/**
 * Append generated images to an assistant message as markdown so the chat
 * renderer shows them. Images carry a `/api/media/<id>.<ext>` URL.
 */
export function appendImageMarkdown(text: string, images: readonly ImageRef[]): string {
  if (!images?.length) return text;
  const blocks = images
    .map((img) => {
      const alt = (img.prompt || "generated image").replace(/[[\]]/g, " ").trim();
      return `![${alt}](${img.url})`;
    })
    .join("\n\n");
  return text ? `${text}\n\n${blocks}` : blocks;
}

/**
 * Strip generated-image markdown out of message text before re-sending history
 * to the language model, so it never re-ingests its own image output.
 */
export function stripGeneratedImagesForModel(text: string): string {
  return text
    .replace(/!\[[^\]]*\]\((?:\/api\/media\/|data:image\/)[^)]*\)\s*/g, "")
    .trimEnd();
}

/**
 * Conservative image-intent heuristic: require BOTH an explicit generation verb
 * and an image noun, so an ambiguous message never triggers a (paid) image
 * generation. The explicit Image toggle remains the primary trigger; restore the
 * original module for the full intent model.
 */
export function hasImageIntent(text: string): boolean {
  if (!text) return false;
  return /\b(draw|generate|create|make|render|paint|sketch|illustrate|design)\b[^.?!\n]{0,40}\b(image|picture|photo|drawing|illustration|painting|portrait|logo|icon|artwork|wallpaper)\b/i.test(
    text,
  );
}
