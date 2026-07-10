import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { resolveBridgeToken } from "@/lib/bridge/store";
import { extractBearer } from "@/lib/bridge/auth";
import { bridgeError } from "@/lib/bridge/errors";
import { loadSettings, saveSettings } from "@/lib/settings/store";
import { providerEnvKey } from "@/lib/ai/provider-keys";
import type { Provider } from "@/lib/ai/models-meta";

export const runtime = "nodejs";

/**
 * GET /api/bridge/v1/keys — the server's effective provider API keys, for
 * `jarvis keys pull` (server = source of truth; the puller writes these into
 * its local ~/.jarvis/keys.env, which the voice agent / CLI / tray all read).
 *
 * Response values are the canonical keys.env variable names, so the client
 * writes them verbatim without its own provider→env mapping.
 *
 * SECURITY: this route returns secret values, so unlike the v1-permissive
 * worker routes (any non-empty bearer, network gate does the real work) the
 * bearer MUST resolve to a real Remote Control token minted by
 * /api/bridge/token for a logged-in user (`jarvis auth login`).
 */

// provider → canonical env-var name in ~/.jarvis/keys.env. google: the web
// honors GOOGLE_GENERATIVE_AI_API_KEY on read, but keys.env's canonical name
// is GOOGLE_API_KEY. ollama is excluded — local daemon, placeholder key only.
const ENV_NAME: Record<Exclude<Provider, "ollama">, string> = {
  anthropic: "ANTHROPIC_API_KEY",
  openai: "OPENAI_API_KEY",
  google: "GOOGLE_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  kimi: "KIMI_API_KEY",
};

export async function GET(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get("authorization"));
  if (!token) return bridgeError(401, "unauthorized", "Missing bearer");
  const userId = resolveBridgeToken(getStore(), token);
  if (!userId) return bridgeError(401, "unauthorized", "Invalid token");

  // SECURITY — latent multi-user leak (safe today: single-user deploy with
  // signup disabled, so the only real account is the operator). `userId` is
  // resolved but NOT scoped: these are the OPERATOR's global provider keys
  // (host env + web settings), so ANY valid bridge token — including a second
  // account, were signup ever enabled — would pull them. Before this box goes
  // multi-user, gate on the resolved user being the owner (LOCAL_USER_ID or a
  // configured owner id) or move to per-user provider keys. Do NOT naively
  // restrict to LOCAL_USER_ID: the real operator logs in via `jarvis auth
  // login` under a better-auth id ≠ LOCAL_USER_ID, so that breaks keys pull.
  const settings = await loadSettings();
  const keys: Record<string, string> = {};
  for (const [provider, envName] of Object.entries(ENV_NAME) as [
    Exclude<Provider, "ollama">,
    string,
  ][]) {
    // Same precedence as the web's own model calls: a key entered in the
    // web Providers UI (settings.json) wins over the host env fallback.
    const stored = settings.providers?.[provider]?.apiKey?.trim() ?? "";
    const effective = stored || (providerEnvKey(provider) ?? "").trim();
    if (effective) keys[envName] = effective;
  }
  return NextResponse.json({ keys });
}

// env-var name → provider, the inverse of ENV_NAME (for POST).
const PROVIDER_BY_ENV: Record<string, Exclude<Provider, "ollama">> =
  Object.fromEntries(
    (Object.entries(ENV_NAME) as [Exclude<Provider, "ollama">, string][]).map(
      ([p, env]) => [env, p],
    ),
  );

/**
 * POST /api/bridge/v1/keys — write provider API keys INTO the server's web
 * store (settings.json), for `jarvis keys push`. This is what makes a key
 * managed by the web Providers UI: once in settings.json it shows the masked
 * preview + delete affordance (keySource "settings"), instead of the opaque
 * "set · keys.env" an env-sourced key gets. Complements the GET (pull).
 *
 * Body: { keys: { ANTHROPIC_API_KEY: "...", GOOGLE_API_KEY: "...", ... } } —
 * the same canonical env-name shape the GET returns, so push/pull are
 * symmetric. Only the keys present are written (upsert); unknown env names and
 * empty values are ignored. Provider keys already in settings.json are
 * overwritten.
 *
 * SECURITY: same strict Remote-Control-token gate as the GET (it writes
 * secrets, not just reads them). The same latent multi-user caveat applies —
 * see the GET; safe today on the single-user, signup-disabled deploy.
 */
export async function POST(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get("authorization"));
  if (!token) return bridgeError(401, "unauthorized", "Missing bearer");
  const userId = resolveBridgeToken(getStore(), token);
  if (!userId) return bridgeError(401, "unauthorized", "Invalid token");

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return bridgeError(400, "bad_request", "Body must be JSON");
  }
  const incoming = (body as { keys?: unknown })?.keys;
  if (typeof incoming !== "object" || incoming === null) {
    return bridgeError(400, "bad_request", "Expected { keys: { ENV_NAME: value } }");
  }

  const settings = await loadSettings();
  const providers = { ...settings.providers };
  const updated: string[] = [];
  for (const [envName, value] of Object.entries(incoming as Record<string, unknown>)) {
    const provider = PROVIDER_BY_ENV[envName];
    if (!provider) continue; // unknown env var — ignore
    if (typeof value !== "string" || !value.trim()) continue; // empty — ignore
    providers[provider] = { ...providers[provider], apiKey: value.trim() };
    updated.push(envName);
  }
  if (updated.length > 0) {
    await saveSettings({ ...settings, providers });
  }
  return NextResponse.json({ updated });
}
