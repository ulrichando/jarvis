import "server-only";

import { promises as fs } from "node:fs";
import path from "node:path";
import {
  DEFAULT_SETTINGS,
  settingsSchema,
  type Settings,
} from "./schema";

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
    { hasKey: boolean; keyPreview?: string; baseURL?: string }
  >;
  integrations: {
    github: { hasToken: boolean; tokenPreview?: string; defaultOwner?: string };
  };
} {
  const redactedProviders = Object.fromEntries(
    Object.entries(settings.providers).map(([k, v]) => {
      const key = v.apiKey ?? "";
      return [
        k,
        {
          hasKey: key.length > 0,
          keyPreview: key ? `••••${key.slice(-4)}` : undefined,
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
