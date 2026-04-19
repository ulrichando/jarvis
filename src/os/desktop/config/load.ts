import type { Config, ProviderName, VisionProviderName } from "./schema.ts";

const KEY_ENV: Record<ProviderName, string> = {
  groq: "GROQ_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  gemini: "GEMINI_API_KEY",
  openai: "OPENAI_API_KEY",
};

const VISION_KEY_ENV: Record<VisionProviderName, string> = {
  gemini: "GEMINI_API_KEY",
  openai: "OPENAI_API_KEY",
  ollama: "OLLAMA_HOST",
};

const DEFAULT_MODELS: Record<ProviderName, string> = {
  groq: "llama-3.3-70b-versatile",
  deepseek: "deepseek-chat",
  gemini: "gemini-2.0-flash",
  openai: "gpt-4o",
};

const DEFAULT_VISION_MODELS: Record<VisionProviderName, string> = {
  gemini: "gemini-2.0-flash",
  openai: "gpt-4o",
  ollama: "llava",
};

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const provider = (env.JARVIS_PROVIDER ?? "groq") as ProviderName;
  if (!(provider in KEY_ENV)) {
    throw new Error(`unknown JARVIS_PROVIDER "${provider}" (expected: ${Object.keys(KEY_ENV).join(", ")})`);
  }
  const apiKey = env[KEY_ENV[provider]];
  if (!apiKey) {
    throw new Error(`missing ${KEY_ENV[provider]} in environment`);
  }
  const model = env.JARVIS_MODEL ?? DEFAULT_MODELS[provider];

  const visionProvider = (env.JARVIS_VISION_PROVIDER ?? "gemini") as VisionProviderName;
  if (!(visionProvider in VISION_KEY_ENV)) {
    throw new Error(`unknown JARVIS_VISION_PROVIDER "${visionProvider}" (expected: ${Object.keys(VISION_KEY_ENV).join(", ")})`);
  }
  const visionApiKey = env[VISION_KEY_ENV[visionProvider]];
  const visionModel = env.JARVIS_VISION_MODEL ?? DEFAULT_VISION_MODELS[visionProvider];

  const host = env.MISTY_HOST ?? "127.0.0.1";
  const port = Number(env.MISTY_PORT ?? 8765);
  if (!Number.isFinite(port) || port <= 0 || port > 65535) {
    throw new Error(`invalid MISTY_PORT "${env.MISTY_PORT}"`);
  }

  return { host, port, provider, model, apiKey, visionProvider, visionApiKey, visionModel };
}
