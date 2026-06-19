import "server-only";

import { loadSettings } from "@/lib/settings/store";

const DEFAULT_OLLAMA = "http://127.0.0.1:11434";

/**
 * Resolve the Ollama base URL the server proxies to:
 *   settings.json connection  →  env (JARVIS_OLLAMA_URL / OLLAMA_BASE_URL)  →  localhost.
 *
 * A trailing slash and a stray `/v1` (the OpenAI-compat suffix the voice-agent
 * uses in JARVIS_LOCAL_LLM_URL) are stripped so we can append the NATIVE
 * `/api/*` paths Ollama exposes for model management.
 */
/**
 * SSRF guard. The ollama routes fetch() this base URL server-side, so only
 * http(s) to a loopback or RFC1918-private host is allowed. Rejects file://,
 * the cloud-metadata address (169.254.169.254 — link-local), public hosts, and
 * bare hostnames (which can DNS-resolve anywhere). Ollama is a local server, so
 * this costs no real functionality. Exported for testing.
 */
export function isPrivateOllamaUrl(raw: string): boolean {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;
  const host = u.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "localhost" || host === "::1") return true;
  const m = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!m) return false; // bare hostname / IPv6 global — could resolve anywhere
  const octets = m.slice(1).map(Number);
  if (octets.some((n) => n > 255)) return false;
  const [a, b] = octets;
  if (a === 127) return true; // loopback 127.0.0.0/8
  if (a === 10) return true; // 10.0.0.0/8
  if (a === 192 && b === 168) return true; // 192.168.0.0/16
  if (a === 172 && b >= 16 && b <= 31) return true; // 172.16.0.0/12
  return false; // public, 0.0.0.0, 169.254/16 link-local (metadata), etc.
}

export async function resolveOllamaBaseURL(): Promise<string> {
  const settings = await loadSettings();
  const fromSettings = settings.connections?.ollama?.baseURL?.trim();
  const fromEnv = (
    process.env.JARVIS_OLLAMA_URL ||
    process.env.OLLAMA_BASE_URL ||
    ""
  ).trim();
  let base = fromSettings || fromEnv || DEFAULT_OLLAMA;
  base = base.replace(/\/+$/, "").replace(/\/v1$/, "");
  // Fail closed: a disallowed value (from settings.json OR the env vars, which
  // bypass the zod schema) reverts to localhost rather than letting the server
  // fetch an attacker-named host.
  if (!isPrivateOllamaUrl(base)) {
    console.warn(
      `[ollama] refusing non-local base URL "${base}"; using ${DEFAULT_OLLAMA}`,
    );
    return DEFAULT_OLLAMA;
  }
  return base;
}

export type OllamaModel = {
  name: string;
  size?: number;
  modified?: string;
  family?: string;
  parameterSize?: string;
};
