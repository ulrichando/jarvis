import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { resolveBridgeToken } from "@/lib/bridge/store";
import { extractBearer } from "@/lib/bridge/auth";
import { bridgeError } from "@/lib/bridge/errors";
import { loadSettings } from "@/lib/settings/store";
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
