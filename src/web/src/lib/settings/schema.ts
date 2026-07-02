import { z } from "zod";
import { MODELS_META, DEFAULT_MODEL, type Provider } from "@/lib/ai/models-meta";
import { IMAGE_MODELS, DEFAULT_IMAGE_MODEL } from "@/lib/ai/image-models";

export const PROVIDER_KEYS: Provider[] = [
  "anthropic",
  "openai",
  "google",
  "deepseek",
  "kimi",
];

// Kokoro voice id shape (af_heart, bm_george, …) — the picker lists the
// live set from /api/tts/voices. Duplicated from lib/chat/voices to keep
// this module dependency-free; keep in sync.
export const KOKORO_VOICE_ID_RE = /^[a-z]{2}_[a-z0-9]+$/;

const providerSettingsSchema = z.object({
  apiKey: z.string().optional(),
  baseURL: z.string().url().optional().or(z.literal("").transform(() => undefined)),
});

export const settingsSchema = z.object({
  version: z.literal(1).default(1),
  user: z
    .object({
      name: z.string().max(80).optional(),
      callName: z.string().max(40).optional(),
      jobTitle: z.string().max(100).optional(),
      preferences: z.string().max(2000).optional(),
      // Preferred Kokoro voice id for web voice mode (General → Voice
      // settings). .catch(): pre-2026-07 files stored texture names
      // ("Mellow") — degrade to unset, never reject the file.
      voice: z.string().regex(KOKORO_VOICE_ID_RE).optional().catch(undefined),
    })
    .default({}),
  notifications: z
    .object({
      responseCompletions: z.boolean().default(false),
    })
    .default({ responseCompletions: false }),
  capabilities: z
    .object({
      markdown: z.boolean().default(true),
      codeHighlight: z.boolean().default(true),
      streaming: z.boolean().default(true),
    })
    .default({ markdown: true, codeHighlight: true, streaming: true }),
  defaults: z
    .object({
      // .catch(): these two fields validate against MUTABLE registries — a
      // model id that was valid when saved can vanish in a later build (live
      // failure: llama-3.3-70b survived the Groq removal in settings.json and
      // the whole file was silently discarded on load). Degrade the single
      // field to its default instead of rejecting the entire settings file.
      model: z
        .string()
        .refine((m) => m in MODELS_META, "unknown model")
        .default(DEFAULT_MODEL)
        .catch(DEFAULT_MODEL),
      // Which image model the in-chat `generateImage` tool uses. Decoupled
      // from `model` (the text model) — image gen is always delegated.
      imageModel: z
        .string()
        .refine((m) => m in IMAGE_MODELS, "unknown image model")
        .default(DEFAULT_IMAGE_MODEL)
        .catch(DEFAULT_IMAGE_MODEL),
      systemPrompt: z.string().max(8000).optional(),
      temperature: z.number().min(0).max(2).default(0.7),
    })
    .default({
      model: DEFAULT_MODEL,
      imageModel: DEFAULT_IMAGE_MODEL,
      temperature: 0.7,
    }),
  providers: z
    .object({
      anthropic: providerSettingsSchema.default({}),
      openai: providerSettingsSchema.default({}),
      google: providerSettingsSchema.default({}),
      deepseek: providerSettingsSchema.default({}),
      kimi: providerSettingsSchema.default({}),
    })
    .default({
      anthropic: {},
      openai: {},
      google: {},
      deepseek: {},
      kimi: {},
    }),
  // Local model backends reachable by base URL (no API key). Ollama is the
  // OpenAI/Ollama-compatible local server the voice-agent's local LLM also
  // uses (JARVIS_LOCAL_LLM_URL), so detecting/pulling here lands models in the
  // SAME server the agent runs against.
  connections: z
    .object({
      ollama: z
        .object({
          baseURL: z
            .string()
            .url()
            .optional()
            .or(z.literal("").transform(() => undefined)),
        })
        .default({}),
    })
    .default({ ollama: {} }),
  appearance: z
    .object({
      fontSize: z.enum(["sm", "md", "lg"]).default("md"),
      density: z.enum(["compact", "cozy"]).default("cozy"),
    })
    .default({ fontSize: "md", density: "cozy" }),
  // Jarvis in Chrome — browser-extension preferences. Persisted now and read by
  // the extension (over the bridge) once it connects. defaultPolicy governs
  // whether Jarvis may act on a site by default; blockedSites are always denied.
  chrome: z
    .object({
      defaultPolicy: z.enum(["allow", "block"]).default("allow"),
      blockedSites: z.array(z.string()).default([]),
    })
    .default({ defaultPolicy: "allow", blockedSites: [] }),
  integrations: z
    .object({
      github: z
        .object({
          // Personal Access Token (classic or fine-grained). Used to
          // push the workspace's git repo to a GitHub remote. Stays
          // local — never sent to the model, never logged.
          token: z.string().optional(),
          // Default account/org for the prompt that asks "<owner>/<repo>"
          // on first push. Optional convenience.
          defaultOwner: z.string().optional(),
        })
        .default({}),
    })
    .default({ github: {} }),
});

export type Settings = z.infer<typeof settingsSchema>;
export type ProviderSettings = z.infer<typeof providerSettingsSchema>;

export const DEFAULT_SETTINGS: Settings = settingsSchema.parse({});
