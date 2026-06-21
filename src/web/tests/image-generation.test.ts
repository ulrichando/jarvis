import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  IMAGE_MODELS,
  DEFAULT_IMAGE_MODEL,
  IMAGE_PROVIDERS,
  IMAGE_MODEL_IDS,
} from "@/lib/ai/image-models";
import { writeMedia, readMedia } from "@/lib/media/store";

// --- Registry invariants (pure) --------------------------------------------
describe("image-models registry", () => {
  it("the default model exists in the registry", () => {
    expect(DEFAULT_IMAGE_MODEL).toBe("gemini-3.1-flash-image-preview");
    expect(IMAGE_MODELS[DEFAULT_IMAGE_MODEL]).toBeDefined();
    // Default is Nano Banana 2 on Google.
    expect(IMAGE_MODELS[DEFAULT_IMAGE_MODEL].provider).toBe("google");
  });

  it("every entry is self-consistent (key === id, non-empty upstreamId)", () => {
    for (const [key, m] of Object.entries(IMAGE_MODELS)) {
      expect(m.id).toBe(key);
      expect(m.upstreamId.length).toBeGreaterThan(0);
      expect(m.label.length).toBeGreaterThan(0);
    }
  });

  it("derives IMAGE_PROVIDERS as the unique provider set", () => {
    const expected = new Set(
      Object.values(IMAGE_MODELS).map((m) => m.provider),
    );
    expect(new Set(IMAGE_PROVIDERS)).toEqual(expected);
    // Only image-capable providers we wire keys for.
    expect(new Set(IMAGE_PROVIDERS)).toEqual(new Set(["google", "openai"]));
  });

  it("IMAGE_MODEL_IDS matches the registry keys", () => {
    expect(new Set(IMAGE_MODEL_IDS)).toEqual(new Set(Object.keys(IMAGE_MODELS)));
  });
});

// --- Media store (round-trip + path-traversal guard) -----------------------
describe("media store", () => {
  let tmp: string;

  beforeAll(async () => {
    tmp = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-media-test-"));
    process.env.JARVIS_MEDIA_DIR = tmp;
  });

  afterAll(async () => {
    delete process.env.JARVIS_MEDIA_DIR;
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("writeMedia → readMedia round-trips PNG bytes with the right type", async () => {
    const bytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4]);
    const stored = await writeMedia(bytes, "image/png");
    // URL shape: /api/media/<32 hex>.png
    expect(stored.url).toMatch(/^\/api\/media\/[a-f0-9]{32}\.png$/);
    expect(stored.filename).toMatch(/^[a-f0-9]{32}\.png$/);

    const read = await readMedia(stored.filename);
    expect(read).not.toBeNull();
    expect(read!.contentType).toBe("image/png");
    expect(Buffer.from(read!.bytes).equals(Buffer.from(bytes))).toBe(true);
  });

  it("maps jpeg/webp media types to the right extension + content type", async () => {
    const jpg = await writeMedia(new Uint8Array([1, 2, 3]), "image/jpeg");
    expect(jpg.filename.endsWith(".jpg")).toBe(true);
    expect((await readMedia(jpg.filename))!.contentType).toBe("image/jpeg");

    const webp = await writeMedia(new Uint8Array([4, 5, 6]), "image/webp");
    expect(webp.filename.endsWith(".webp")).toBe(true);
    expect((await readMedia(webp.filename))!.contentType).toBe("image/webp");
  });

  it("falls back to png for an unknown media type", async () => {
    const stored = await writeMedia(new Uint8Array([7]), "image/tiff");
    expect(stored.filename.endsWith(".png")).toBe(true);
  });

  it("rejects path-traversal and malformed names (no fs escape)", async () => {
    for (const bad of [
      "../../etc/passwd",
      "../secret.png",
      "abc/def.png",
      "..%2Fx.png",
      "x.txt",
      "x.png.txt",
      "GHIJKL.png", // not lowercase hex
      "abc", // no extension
      "", // empty
      "/etc/passwd",
      ".png",
    ]) {
      expect(await readMedia(bad), bad).toBeNull();
    }
  });

  it("returns null for a well-formed but missing file", async () => {
    const missing = "0".repeat(32) + ".png";
    expect(await readMedia(missing)).toBeNull();
  });
});
