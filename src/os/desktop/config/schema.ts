export type ProviderName = "groq" | "deepseek" | "gemini" | "openai";
export type VisionProviderName = "gemini" | "openai" | "ollama";

export type Config = {
  host: string;
  port: number;
  provider: ProviderName;
  model: string;
  apiKey: string;
  visionProvider: VisionProviderName;
  visionApiKey: string | undefined;
  visionModel: string;
};
