import { GoogleGenerativeAI } from "@google/generative-ai";
import type { VisionClient } from "./types.ts";

export function createGeminiVisionClient(opts: { apiKey: string }): VisionClient {
  const genai = new GoogleGenerativeAI(opts.apiKey);
  return {
    name: "gemini",
    async describe({ imageBase64, prompt, model }) {
      const m = genai.getGenerativeModel({ model: model ?? "gemini-2.0-flash" });
      const resp = await m.generateContent([
        prompt,
        { inlineData: { data: imageBase64, mimeType: "image/jpeg" } },
      ]);
      return resp.response.text();
    },
  };
}
