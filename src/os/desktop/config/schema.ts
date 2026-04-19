export type ProviderName = "groq" | "deepseek" | "gemini" | "openai";

export type Config = {
  host: string;
  port: number;
  provider: ProviderName;
  model: string;
  apiKey: string; // the key for the selected provider
};
