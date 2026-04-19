import type { Config } from "../config/schema.ts";
import type { VisionClient } from "./types.ts";
import { createGeminiVisionClient } from "./geminiClient.ts";

export function createVisionClient(cfg: Config): VisionClient {
  switch (cfg.visionProvider) {
    case "gemini": {
      if (!cfg.visionApiKey) throw new Error("GEMINI_API_KEY not set for vision provider gemini");
      return createGeminiVisionClient({ apiKey: cfg.visionApiKey });
    }
    case "openai":
    case "ollama":
      throw new Error(`vision provider "${cfg.visionProvider}" not implemented in Plan 3; add a client in a later plan`);
  }
}
