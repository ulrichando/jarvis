import "server-only";

import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomBytes } from "node:crypto";

/**
 * On-disk store for generated images. Lives under ~/.jarvis/media/ — the same
 * ~/.jarvis root the rest of JARVIS uses (workspaces, keys.env), absolute so it
 * doesn't depend on the web app's cwd. Files are served by `/api/media/<id>`.
 *
 * We persist the bytes to disk and reference them by a tiny URL (~40 chars) so
 * the conversation history stays small — embedding a multi-MB base64 data URL
 * in the assistant text would bloat Postgres AND get re-sent to the model as
 * input on every following turn (huge token cost / context overflow).
 */

// Base dir for generated images. Overridable via JARVIS_MEDIA_DIR (tests +
// flexible deploys); defaults to ~/.jarvis/media, alongside the rest of
// JARVIS's user data. Read at call time so tests/env changes take effect.
function mediaDir(): string {
  return (
    process.env.JARVIS_MEDIA_DIR ?? path.join(os.homedir(), ".jarvis", "media")
  );
}

const EXT_BY_TYPE: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/jpg": "jpg",
  "image/webp": "webp",
};

const TYPE_BY_EXT: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/webp",
};

// A media filename is `<32 hex chars>.<ext>`. The route validates the incoming
// param against this BEFORE touching the filesystem — no `..`, no slashes, no
// absolute paths can pass, so path traversal out of MEDIA_DIR is impossible.
const FILENAME_RE = /^[a-f0-9]{32}\.(png|jpe?g|webp)$/;

export type StoredMedia = { id: string; url: string; filename: string };

/** Write image bytes to the media store; return its public `/api/media` URL. */
export async function writeMedia(
  bytes: Uint8Array,
  mediaType: string,
): Promise<StoredMedia> {
  const ext = EXT_BY_TYPE[mediaType.toLowerCase()] ?? "png";
  const id = randomBytes(16).toString("hex"); // 128-bit, unguessable
  const filename = `${id}.${ext}`;
  const dir = mediaDir();
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(path.join(dir, filename), bytes);
  return { id, url: `/api/media/${filename}`, filename };
}

/**
 * Read a media file by its filename param. Returns null when the name is
 * malformed (traversal attempt / wrong shape) or the file is missing, so the
 * route can answer 404 without leaking which case it was.
 */
export async function readMedia(
  filename: string,
): Promise<{ bytes: Buffer; contentType: string } | null> {
  if (!FILENAME_RE.test(filename)) return null;
  const ext = filename.slice(filename.lastIndexOf(".") + 1).toLowerCase();
  const contentType = TYPE_BY_EXT[ext];
  if (!contentType) return null;
  try {
    const bytes = await fs.readFile(path.join(mediaDir(), filename));
    return { bytes, contentType };
  } catch {
    return null;
  }
}
