import { tool } from "ai";
import { z } from "zod";
import { generateChatImage, type GeneratedImage } from "@/lib/ai/image";
import { MissingApiKeyError } from "@/lib/ai/models";
import {
  IMAGE_ASPECT_RATIOS,
  type ImageAspectRatio,
} from "@/lib/ai/image-models";

const aspectRatioSchema = z.enum(
  IMAGE_ASPECT_RATIOS as unknown as [ImageAspectRatio, ...ImageAspectRatio[]],
);

const inputSchema = z.object({
  prompt: z
    .string()
    .min(1)
    .describe(
      "A vivid, detailed description of the image to generate. Include subject, style, composition, lighting, and mood.",
    ),
  aspectRatio: aspectRatioSchema
    .optional()
    .describe("Aspect ratio. Defaults to 1:1 (square)."),
});

/**
 * Build the `generateImage` tool for one chat request.
 *
 * - `imageModelId` is the user's Settings pick (which image model produces the
 *   pixels) — decoupled from the text model the user is chatting with, so ANY
 *   chat model (incl. DeepSeek, which has no image endpoint) can trigger it.
 * - `onGenerated` collects each image so the route's onFinish can append a
 *   markdown reference to the persisted assistant text (tool parts themselves
 *   aren't persisted — see persist.ts). The live UI renders from the tool part.
 *
 * The image is shown to the user automatically; the description tells the model
 * NOT to paste the URL/markdown, so we don't double-render on reload.
 */
export function createGenerateImageTool({
  imageModelId,
  onGenerated,
}: {
  imageModelId?: string;
  onGenerated: (image: GeneratedImage) => void;
}) {
  return tool({
    description:
      "Generate an image from a text prompt. Use whenever the user asks you to create, draw, generate, design, or make a picture, illustration, logo, icon, or any visual. The generated image is displayed to the user automatically — do NOT repeat the image URL or embed markdown for it in your reply; just briefly acknowledge what you made.",
    inputSchema,
    execute: async ({ prompt, aspectRatio }) => {
      try {
        const image = await generateChatImage({
          prompt,
          modelId: imageModelId,
          aspectRatio,
        });
        onGenerated(image);
        return {
          status: "ok" as const,
          prompt: image.prompt,
          model: image.modelLabel,
          url: image.url,
        };
      } catch (err) {
        if (err instanceof MissingApiKeyError) {
          return {
            status: "error" as const,
            error: `No API key for ${err.provider}. Add one in Settings → Providers (or pick a different image model) to generate images.`,
          };
        }
        const message = err instanceof Error ? err.message : String(err);
        console.error("[generate-image] failed:", message);
        return {
          status: "error" as const,
          error: `Image generation failed: ${message}`,
        };
      }
    },
  });
}
