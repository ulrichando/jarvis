import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";
import {
  DEFAULT_SETTINGS,
  settingsSchema,
  type Settings,
} from "./schema";
import { providerEnvKey } from "@/lib/ai/provider-keys";
import type { Provider } from "@/lib/ai/models-meta";

const SETTINGS_DIR = path.join(process.cwd(), ".jarvis");
const SETTINGS_FILE = path.join(SETTINGS_DIR, "settings.json");

let cache: Settings | null = null;

async function ensureDir() {
  await fs.mkdir(SETTINGS_DIR, { recursive: true });
}

export async function loadSettings(): Promise<Settings> {
  if (cache) return cache;
  try {
    const raw = await fs.readFile(SETTINGS_FILE, "utf-8");
    const parsed = settingsSchema.safeParse(JSON.parse(raw));
    cache = parsed.success ? parsed.data : DEFAULT_SETTINGS;
  } catch {
    cache = DEFAULT_SETTINGS;
  }
  return cache;
}

export async function saveSettings(next: Settings): Promise<Settings> {
  const validated = settingsSchema.parse(next);
  await ensureDir();
  await fs.writeFile(SETTINGS_FILE, JSON.stringify(validated, null, 2), "utf-8");
  cache = validated;
  return validated;
}

export function invalidateSettingsCache() {
  cache = null;
}

/**
 * Redact API keys for transport to the client. Keeps the last 4 chars so the
 * UI can show `••••1a2b` without ever re-sending the actual secret.
 */
export function redactForClient(settings: Settings): Settings & {
  providers: Record<
    keyof Settings["providers"],
    {
      hasKey: boolean;
      keyPreview?: string;
      keySource?: "settings" | "env";
      baseURL?: string;
    }
  >;
  integrations: {
    github: { hasToken: boolean; tokenPreview?: string; defaultOwner?: string };
  };
} {
  const redactedProviders = Object.fromEntries(
    Object.entries(settings.providers).map(([k, v]) => {
      // A key stored in the web's settings.json wins; otherwise fall back to the
      // SAME env var the actual AI call uses (loaded from ~/.jarvis/keys.env via
      // next.config.ts), so a key entered on the desktop shows as configured here.
      const settingsKey = v.apiKey ?? "";
      const envKey = settingsKey ? "" : providerEnvKey(k as Provider) ?? "";
      const effective = settingsKey || envKey;
      return [
        k,
        {
          hasKey: effective.length > 0,
          keyPreview: effective ? `••••${effective.slice(-4)}` : undefined,
          keySource: settingsKey ? "settings" : envKey ? "env" : undefined,
          baseURL: v.baseURL,
        },
      ];
    }),
  ) as never;
  const ghToken = settings.integrations?.github?.token ?? "";
  const redactedIntegrations = {
    github: {
      hasToken: ghToken.length > 0,
      tokenPreview: ghToken ? `••••${ghToken.slice(-4)}` : undefined,
      defaultOwner: settings.integrations?.github?.defaultOwner,
    },
  };
  return {
    ...settings,
    providers: redactedProviders,
    integrations: redactedIntegrations,
  };
}
