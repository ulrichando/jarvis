import "server-only";

import { generateImage } from "ai";
import { buildProvider, resolveApiKey, MissingApiKeyError } from "./models";
import {
  IMAGE_MODELS,
  DEFAULT_IMAGE_MODEL,
  IMAGE_PROVIDERS,
  type ImageAspectRatio,
} from "./image-models";
import { writeMedia } from "@/lib/media/store";

/**
 * Server-side image generation. ONE call path for every model — AI SDK v6's
 * `generateImage({ model: provider.image(id), prompt, ... })` works for Gemini
 * Flash Image (Nano Banana), Imagen, and OpenAI GPT Image alike. Verified
 * against ai-sdk.dev + Google docs (2026-06-21). `generateImage` is the stable
 * export in ai@6 (the experimental_ alias is deprecated).
 */

export type GeneratedImage = {
  url: string; // /api/media/<id>.<ext>
  mediaType: string;
  prompt: string;
  modelLabel: string;
};

function resolveImageModelId(modelId?: string): string {
  return modelId && modelId in IMAGE_MODELS ? modelId : DEFAULT_IMAGE_MODEL;
}

// OpenAI's image endpoint takes `size`, not `aspectRatio`. Map our small set
// of ratios to the nearest supported gpt-image size. Google models take
// `aspectRatio` natively, so this is only used for the openai provider.
const OPENAI_SIZE_BY_RATIO: Record<ImageAspectRatio, string> = {
  "1:1": "1024x1024",
  "16:9": "1536x1024",
  "4:3": "1536x1024",
  "9:16": "1024x1536",
  "3:4": "1024x1536",
};

/** True iff at least one image provider has a usable API key. Gates the tool. */
export async function imageGenAvailable(): Promise<boolean> {
  for (const provider of IMAGE_PROVIDERS) {
    const { apiKey } = await resolveApiKey(provider);
    if (apiKey) return true;
  }
  return false;
}

/**
 * Generate one image and persist it to the media store. Throws
 * MissingApiKeyError when the chosen model's provider has no key (the caller
 * surfaces a friendly "add a key" message — same pattern as the chat route).
 */
export async function generateChatImage({
  prompt,
  modelId,
  aspectRatio = "1:1",
}: {
  prompt: string;
  modelId?: string;
  aspectRatio?: ImageAspectRatio;
}): Promise<GeneratedImage> {
  const id = resolveImageModelId(modelId);
  const meta = IMAGE_MODELS[id];
  const { apiKey, baseURL } = await resolveApiKey(meta.provider);
  if (!apiKey) throw new MissingApiKeyError(meta.provider);

  const provider = buildProvider(meta.provider, apiKey, baseURL);
  // Both @ai-sdk/openai and @ai-sdk/google provider instances expose .image().
  const model = (provider as { image: (id: string) => unknown }).image(
    meta.upstreamId,
  );

  const { image } = await generateImage({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    model: model as any,
    prompt,
    ...(meta.provider === "openai"
      ? { size: OPENAI_SIZE_BY_RATIO[aspectRatio] as `${number}x${number}` }
      : { aspectRatio }),
  });

  const mediaType = image.mediaType || "image/png";
  const stored = await writeMedia(image.uint8Array, mediaType);
  return { url: stored.url, mediaType, prompt, modelLabel: meta.label };
}
