/**
 * Client-safe image-model metadata. NO SDK imports — safe to ship to the
 * browser (the Settings picker imports this). The server resolves these ids
 * to real image models in `@/lib/ai/image.ts`, the same way `models.ts`
 * resolves `models-meta.ts` for language models.
 *
 * Why a registry instead of hardcoding one model: image generation is a
 * capability the chat DELEGATES to, decoupled from the text model you're
 * chatting with. Any chat model (incl. DeepSeek, which has no image endpoint)
 * triggers the `generateImage` tool; the pixels come from whichever of these
 * the user selected as their default.
 */

import type { Provider } from "./models-meta";

export type ImageModelMeta = {
  /** App-facing id (also persisted in settings.defaults.imageModel). */
  id: string;
  label: string;
  description: string;
  provider: Provider;
  /** The id the provider's SDK expects in `provider.image(upstreamId)`. */
  upstreamId: string;
  /** Small pill in the picker (e.g. "Default", "Fast"). */
  badge?: string;
};

// Default = Nano Banana 2 (gemini-3.1-flash-image-preview): fast (~1–3s),
// cheap, and the user's pick. Verified id via Google's image-generation docs
// (2026-06-21). The exact GPT Image / Imagen ids are the current known-good
// values; they're switchable extras, not the default, so easy to bump later.
export const IMAGE_MODELS: Record<string, ImageModelMeta> = {
  "gemini-3.1-flash-image-preview": {
    id: "gemini-3.1-flash-image-preview",
    label: "Nano Banana 2",
    description: "Google's fast image model (~1–3s). Great for iterating.",
    provider: "google",
    upstreamId: "gemini-3.1-flash-image-preview",
    badge: "Default",
  },
  "gemini-3-pro-image-preview": {
    id: "gemini-3-pro-image-preview",
    label: "Nano Banana Pro",
    description:
      "Higher-fidelity Gemini image model — 2K/4K, best in-image text.",
    provider: "google",
    upstreamId: "gemini-3-pro-image-preview",
  },
  "imagen-4.0-generate-001": {
    id: "imagen-4.0-generate-001",
    label: "Imagen 4",
    description: "Google's most photorealistic image model.",
    provider: "google",
    upstreamId: "imagen-4.0-generate-001",
  },
  "gpt-image-1": {
    id: "gpt-image-1",
    label: "GPT Image",
    description: "OpenAI's image model — strong editing + in-image text.",
    provider: "openai",
    upstreamId: "gpt-image-1",
  },
};

export const DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview";

export const IMAGE_MODEL_IDS = Object.keys(IMAGE_MODELS);

/** Providers that can generate images (used to gate the tool by key presence). */
export const IMAGE_PROVIDERS: Provider[] = Array.from(
  new Set(Object.values(IMAGE_MODELS).map((m) => m.provider)),
);

/** Supported aspect ratios surfaced to the model/UI. Kept small on purpose. */
export const IMAGE_ASPECT_RATIOS = [
  "1:1",
  "16:9",
  "9:16",
  "4:3",
  "3:4",
] as const;
export type ImageAspectRatio = (typeof IMAGE_ASPECT_RATIOS)[number];
