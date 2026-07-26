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

// ─── Shared-file key preservation ────────────────────────────────────────────
// ~/.jarvis/settings.json is SHARED with the jarvis CLI + voice-agent, which
// keep their own keys in the same file (enabledPlugins, effortLevel,
// skipDangerousModePermissionPrompt, voiceNoticeSeen, …). settingsSchema is a
// strict zod object — .parse() strips every key it doesn't know — so writing
// the parsed object alone silently DELETES all CLI/voice keys on each web
// settings save. saveSettings therefore grafts every unknown key from the
// on-disk JSON back onto the validated web subset before writing. Schema-KNOWN
// keys are never grafted, so a field the web deliberately cleared (apiKey:
// null → undefined) can't be resurrected from disk. loadSettings still returns
// the strict schema view, so unknown (possibly secret) CLI keys never reach
// redactForClient / the client.

type JsonObject = Record<string, unknown>;

function isPlainObject(v: unknown): v is JsonObject {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

// Minimal structural view of a zod v4 schema node — enough to unwrap
// default/optional/nullable/catch wrappers and reach object shapes without
// leaning on zod's internal types.
type SchemaNode = {
  def?: { type?: string; innerType?: SchemaNode };
  shape?: Record<string, SchemaNode>;
};

const WRAPPER_TYPES = new Set(["default", "prefault", "optional", "nullable", "catch", "readonly"]);

function unwrapSchema(node: SchemaNode): SchemaNode {
  let s = node;
  while (s.def && WRAPPER_TYPES.has(s.def.type ?? "") && s.def.innerType) {
    s = s.def.innerType;
  }
  return s;
}

/**
 * The subtree of `raw` the schema does NOT know: top-level foreign keys are
 * kept whole; keys the schema models as nested objects are recursed into so
 * foreign NESTED keys (e.g. a CLI field inside `user`) survive too.
 */
function extractUnknownKeys(raw: unknown, schema: SchemaNode): JsonObject {
  const core = unwrapSchema(schema);
  if (!isPlainObject(raw) || core.def?.type !== "object" || !core.shape) return {};
  const unknown: JsonObject = {};
  for (const [key, value] of Object.entries(raw)) {
    const sub = core.shape[key];
    if (!sub) {
      unknown[key] = value; // whole subtree is foreign — keep as-is
      continue;
    }
    const nested = extractUnknownKeys(value, sub);
    if (Object.keys(nested).length > 0) unknown[key] = nested;
  }
  return unknown;
}

/** Graft foreign keys onto the validated web settings (validated wins on web-owned keys). */
function graftUnknownKeys(base: JsonObject, unknown: JsonObject): JsonObject {
  const out: JsonObject = { ...base };
  for (const [key, value] of Object.entries(unknown)) {
    const existing = out[key];
    out[key] =
      isPlainObject(value) && isPlainObject(existing)
        ? graftUnknownKeys(existing, value)
        : value;
  }
  return out;
}

// Raw on-disk JSON, same candidate order as loadSettings. Unlike loadSettings
// this never schema-parses — it only needs structurally valid JSON, because
// its sole job is sourcing the unknown keys to preserve.
async function readRawSettings(): Promise<JsonObject | null> {
  for (const file of [SETTINGS_FILE, `${SETTINGS_FILE}.bak`, LEGACY_SETTINGS_FILE]) {
    try {
      const parsed: unknown = JSON.parse(await fs.readFile(file, "utf-8"));
      if (isPlainObject(parsed)) return parsed;
    } catch {
      // missing or torn — try the next candidate
    }
  }
  return null;
}

export async function saveSettings(next: Settings): Promise<Settings> {
  const validated = settingsSchema.parse(next);
  await ensureDir();
  // Preserve CLI/voice-agent keys the web schema doesn't know (see the
  // shared-file preservation block above).
  const raw = await readRawSettings();
  const merged = raw
    ? graftUnknownKeys(
        validated as unknown as JsonObject,
        extractUnknownKeys(raw, settingsSchema as unknown as SchemaNode),
      )
    : validated;
  const body = JSON.stringify(merged, null, 2);
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
