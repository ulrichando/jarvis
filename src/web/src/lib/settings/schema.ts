import { z } from "zod";
import { MODELS_META, DEFAULT_MODEL, type Provider } from "@/lib/ai/models-meta";

export const PROVIDER_KEYS: Provider[] = [
  "anthropic",
  "openai",
  "google",
  "groq",
  "deepseek",
  "kimi",
];

const providerSettingsSchema = z.object({
  apiKey: z.string().optional(),
  baseURL: z.string().url().optional().or(z.literal("").transform(() => undefined)),
});

export const settingsSchema = z.object({
  version: z.literal(1).default(1),
  user: z
    .object({
      name: z.string().max(80).optional(),
    })
    .default({}),
  defaults: z
    .object({
      model: z
        .string()
        .refine((m) => m in MODELS_META, "unknown model")
        .default(DEFAULT_MODEL),
      systemPrompt: z.string().max(8000).optional(),
      temperature: z.number().min(0).max(2).default(0.7),
    })
    .default({
      model: DEFAULT_MODEL,
      temperature: 0.7,
    }),
  providers: z
    .object({
      anthropic: providerSettingsSchema.default({}),
      openai: providerSettingsSchema.default({}),
      google: providerSettingsSchema.default({}),
      deepseek: providerSettingsSchema.default({}),
      groq: providerSettingsSchema.default({}),
      kimi: providerSettingsSchema.default({}),
    })
    .default({
      anthropic: {},
      openai: {},
      google: {},
      deepseek: {},
      groq: {},
      kimi: {},
    }),
  appearance: z
    .object({
      fontSize: z.enum(["sm", "md", "lg"]).default("md"),
      density: z.enum(["compact", "cozy"]).default("cozy"),
    })
    .default({ fontSize: "md", density: "cozy" }),
});

export type Settings = z.infer<typeof settingsSchema>;
export type ProviderSettings = z.infer<typeof providerSettingsSchema>;

export const DEFAULT_SETTINGS: Settings = settingsSchema.parse({});
