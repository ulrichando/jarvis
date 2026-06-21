import { readMedia } from "@/lib/media/store";

export const runtime = "nodejs";

/**
 * Serves a generated image from ~/.jarvis/media/ by filename (`<id>.<ext>`).
 *
 * Auth: loaded by the browser's <img> tag, which stamps
 * `Sec-Fetch-Site: same-origin` — proxy.ts lets those through even under the
 * bearer gate (page JS can't forge that header, and the Host allowlist blocks
 * cross-origin). The id is a 128-bit random token; readMedia rejects anything
 * not matching `^[a-f0-9]{32}\.(png|jpe?g|webp)$`, so no path traversal.
 */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/media/[id]">,
) {
  const { id } = await ctx.params;
  const media = await readMedia(id);
  if (!media) {
    return new Response("Not found", { status: 404 });
  }
  return new Response(new Uint8Array(media.bytes), {
    headers: {
      "Content-Type": media.contentType,
      // Immutable: the random id never points at different bytes.
      "Cache-Control": "private, max-age=31536000, immutable",
    },
  });
}
