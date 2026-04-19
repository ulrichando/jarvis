import type { ToolRunner } from "../types.ts";
import type { VisionClient } from "../../providers/types.ts";
import { capture, toBase64 } from "../../screen/observer.ts";

type ScreenInput = { monitor?: "focused" | "all" | string; question?: string };

const DEFAULT_PROMPT = "Describe what's on this screen concisely. Note the focused application, any visible text, and what the user appears to be doing.";

export function createScreenTool(visionClient: VisionClient | undefined): ToolRunner {
  return {
    def: {
      name: "screen",
      description: "Capture the current screen and describe it via a vision model. Use to see what the user is doing.",
      input_schema: {
        type: "object",
        properties: {
          monitor: { type: "string", description: "Monitor to capture: 'focused' (default), 'all', or a monitor name like 'DP-1'" },
          question: { type: "string", description: "Optional specific question to ask about the screen" },
        },
        required: [],
      },
    },
    async run(input: unknown): Promise<{ output: string; is_error?: boolean }> {
      if (!visionClient) {
        return {
          output: "screen tool unavailable: vision provider not configured (set GEMINI_API_KEY or JARVIS_VISION_PROVIDER)",
          is_error: true,
        };
      }
      const { monitor, question } = (input as ScreenInput) ?? {};
      try {
        const cap = await capture({ monitor });
        const description = await visionClient.describe({
          imageBase64: toBase64(cap.jpeg),
          prompt: question ?? DEFAULT_PROMPT,
        });
        return { output: description };
      } catch (err) {
        return { output: `screen capture/describe failed: ${String(err)}`, is_error: true };
      }
    },
  };
}
