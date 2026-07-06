import "server-only";

import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  DEFAULT_SETTINGS,
  settingsSchema,
  type Settings,
} from "./schema";
import { providerEnvKey } from "@/lib/ai/provider-keys";
import type { Provider } from "@/lib/ai/models-meta";

const SETTINGS_DIR = path.join(os.homedir(), ".jarvis");
const SETTINGS_FILE = path.join(SETTINGS_DIR, "settings.json");
// Legacy cwd-relative location (pre-2026-06). Read once for migration; the next
// saveSettings() writes the new ~/.jarvis path, superseding it.
const LEGACY_SETTINGS_FILE = path.join(process.cwd(), ".jarvis", "settings.json");

let cache: Settings | null = null;

async function ensureDir() {
  await fs.mkdir(SETTINGS_DIR, { recursive: true });
}

export async function loadSettings(): Promise<Settings> {
  if (cache) return cache;
  // Try the live file, then the last-known-good backup, then legacy. A single
  // broken/torn file must NOT short-circuit to defaults — that would then get
  // persisted on the next save and permanently wipe every provider key.
  for (const file of [SETTINGS_FILE, `${SETTINGS_FILE}.bak`, LEGACY_SETTINGS_FILE]) {
    let raw: string;
    try {
      raw = await fs.readFile(file, "utf-8");
    } catch {
      continue; // not at this location — try the next
    }
    try {
      const parsed = settingsSchema.safeParse(JSON.parse(raw));
      if (parsed.success) {
        cache = parsed.data;
        return cache;
      }
      // Structurally valid JSON but fails the schema (field-level salvage in
      // the schema already ran) — try the .bak/legacy before defaulting.
      console.warn(`[settings] ${file} failed validation — trying fallback:`, parsed.error.message);
    } catch {
      // Torn / invalid JSON (e.g. crash mid-write) — try the next candidate.
      console.warn(`[settings] ${file} is not valid JSON — trying fallback`);
    }
  }
  console.warn("[settings] no valid settings file found — using defaults");
  cache = DEFAULT_SETTINGS;
  return cache;
}

export async function saveSettings(next: Settings): Promise<Settings> {
  const validated = settingsSchema.parse(next);
  await ensureDir();
  const body = JSON.stringify(validated, null, 2);
  // Atomic write: temp file + rename (atomic on POSIX) so a crash mid-write
  // can't leave a torn settings.json. Back up the current good file first so
  // loadSettings has a recovery source. Both guard against the key-wipe chain
  // (torn file → defaults loaded → defaults saved over real keys).
  const tmp = `${SETTINGS_FILE}.tmp`;
  await fs.writeFile(tmp, body, "utf-8");
  await fs.copyFile(SETTINGS_FILE, `${SETTINGS_FILE}.bak`).catch(() => {}); // best-effort; absent on first save
  await fs.rename(tmp, SETTINGS_FILE);
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
          // Don't ship last-4 of a keys.env secret the web app doesn't own —
          // hasKey + keySource drive the UI; only preview web-stored keys.
          keyPreview: settingsKey ? `••••${settingsKey.slice(-4)}` : undefined,
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
