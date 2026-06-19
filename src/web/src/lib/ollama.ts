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
  return base;
}

export type OllamaModel = {
  name: string;
  size?: number;
  modified?: string;
  family?: string;
  parameterSize?: string;
};
