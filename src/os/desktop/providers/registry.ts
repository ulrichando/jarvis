import type { Config } from "../config/schema.ts";
import type { LLMClient } from "./types.ts";
import { createGroqClient } from "./groqClient.ts";

export function createClient(cfg: Config): LLMClient {
  switch (cfg.provider) {
    case "groq":
      return createGroqClient({ apiKey: cfg.apiKey });
    case "deepseek":
    case "gemini":
    case "openai":
      throw new Error(`provider "${cfg.provider}" not implemented in Plan 2; add a client to providers/ in a later plan`);
  }
}
