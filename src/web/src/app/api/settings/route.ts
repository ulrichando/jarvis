import { z } from "zod";
import { loadSettings, redactForClient, saveSettings } from "@/lib/settings/store";
import { DEFAULT_SETTINGS, settingsSchema } from "@/lib/settings/schema";

export const runtime = "nodejs";

export async function GET() {
  const settings = await loadSettings();
  return Response.json(redactForClient(settings));
}

/**
 * PATCH — deep-merged update. Provider apiKey is updated only when a non-empty
 * string is supplied, so the client can safely omit it to keep the stored one.
 * Pass `null` to explicitly clear a key.
 */
const providerPatchSchema = z
  .object({
    apiKey: z.string().or(z.null()).optional(),
    baseURL: z.string().or(z.null()).optional(),
  })
  .optional();

const patchSchema = z.object({
  user: z
    .object({
      name: z.string().optional(),
      callName: z.string().optional(),
      jobTitle: z.string().optional(),
      preferences: z.string().optional(),
    })
    .partial()
    .optional(),
  notifications: z
    .object({ responseCompletions: z.boolean() })
    .partial()
    .optional(),
  capabilities: z
    .object({
      markdown: z.boolean(),
      codeHighlight: z.boolean(),
      streaming: z.boolean(),
    })
    .partial()
    .optional(),
  defaults: z
    .object({
      model: z.string().optional(),
      systemPrompt: z.string().optional(),
      temperature: z.number().optional(),
    })
    .partial()
    .optional(),
  providers: z
    .object({
      anthropic: providerPatchSchema,
      openai: providerPatchSchema,
      google: providerPatchSchema,
      deepseek: providerPatchSchema,
      groq: providerPatchSchema,
      kimi: providerPatchSchema,
    })
    .partial()
    .optional(),
  appearance: z
    .object({
      fontSize: z.enum(["sm", "md", "lg"]).optional(),
      density: z.enum(["compact", "cozy"]).optional(),
    })
    .partial()
    .optional(),
  integrations: z
    .object({
      github: z
        .object({
          token: z.string().or(z.null()).optional(),
          defaultOwner: z.string().or(z.null()).optional(),
        })
        .partial()
        .optional(),
    })
    .partial()
    .optional(),
});

export async function PATCH(req: Request) {
  const parsed = patchSchema.safeParse(await req.json());
  if (!parsed.success) {
    return Response.json({ error: parsed.error.flatten() }, { status: 400 });
  }

  const current = await loadSettings();
  const patch = parsed.data;

  const nextProviders = { ...current.providers };
  if (patch.providers) {
    for (const [provider, patchValue] of Object.entries(patch.providers)) {
      if (!patchValue) continue;
      const p = provider as keyof typeof nextProviders;
      const prev = nextProviders[p];
      const next = { ...prev };
      if (patchValue.apiKey !== undefined) {
        next.apiKey = patchValue.apiKey === null ? undefined : patchValue.apiKey;
      }
      if (patchValue.baseURL !== undefined) {
        next.baseURL =
          patchValue.baseURL === null || patchValue.baseURL === ""
            ? undefined
            : patchValue.baseURL;
      }
      nextProviders[p] = next;
    }
  }

  // Integrations: same null-clears-the-field pattern as providers.
  const nextIntegrations = { ...current.integrations };
  if (patch.integrations?.github) {
    const prev = nextIntegrations.github ?? {};
    const ghPatch = patch.integrations.github;
    const nextGh = { ...prev };
    if (ghPatch.token !== undefined) {
      nextGh.token =
        ghPatch.token === null || ghPatch.token === "" ? undefined : ghPatch.token;
    }
    if (ghPatch.defaultOwner !== undefined) {
      nextGh.defaultOwner =
        ghPatch.defaultOwner === null || ghPatch.defaultOwner === ""
          ? undefined
          : ghPatch.defaultOwner;
    }
    nextIntegrations.github = nextGh;
  }

  const next = settingsSchema.parse({
    ...current,
    user: { ...current.user, ...(patch.user ?? {}) },
    notifications: { ...current.notifications, ...(patch.notifications ?? {}) },
    capabilities: { ...current.capabilities, ...(patch.capabilities ?? {}) },
    defaults: { ...current.defaults, ...(patch.defaults ?? {}) },
    providers: nextProviders,
    appearance: { ...current.appearance, ...(patch.appearance ?? {}) },
    integrations: nextIntegrations,
  });

  const saved = await saveSettings(next);
  return Response.json(redactForClient(saved));
}

/**
 * DELETE — reset settings to defaults. Preserves secrets (provider API keys +
 * integration tokens) so the Account → Reset action matches its copy
 * ("API keys and conversations are unaffected").
 */
export async function DELETE() {
  const current = await loadSettings();
  const reset = settingsSchema.parse({
    ...DEFAULT_SETTINGS,
    providers: current.providers,
    integrations: current.integrations,
  });
  const saved = await saveSettings(reset);
  return Response.json(redactForClient(saved));
}
