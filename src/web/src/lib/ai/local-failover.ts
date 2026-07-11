import "server-only";

/**
 * Local last-resort LLM failover (Stage 2 of full offline parity,
 * 2026-07-10 — mirrors the voice agent's Stage 1 JARVIS_LOCAL_LLM_FAILOVER
 * philosophy and the CLI proxy's localFailover.ts).
 *
 * When the selected CLOUD model fails with a genuine-outage signal — no API
 * key configured, connection/DNS/timeout error, or a 5xx from the provider —
 * AND a local Ollama daemon is reachable with the failover model pulled, the
 * chat turn is retried once against local qwen2.5:7b. Cloud stays strictly
 * primary:
 *
 *   - The healthy path is unchanged: the middleware below only runs code on
 *     the REJECTION branch of doStream/doGenerate; no probe, no extra fetch,
 *     no latency while the cloud answers.
 *   - Definitive provider answers (400/401/403/404/422/429) are NEVER
 *     rerouted — a bad key, quota exhaustion, or a malformed request must
 *     surface to the user, not silently degrade to a 7B local model.
 *   - DEPLOYED (0wlan.com / VPS) instances have no local Ollama: the probe
 *     (1.5 s cap, only ever reached on the failure path) fails fast and the
 *     original cloud error surfaces exactly as before Stage 2 — the
 *     deployed path degrades to a no-op by construction.
 *
 * Flag: JARVIS_LOCAL_LLM_FAILOVER — default ON; 0/false/no/off disables.
 * Knobs (shared with Stage 1 + the CLI): JARVIS_LOCAL_LLM_MODEL (default
 * qwen2.5:7b), JARVIS_LOCAL_LLM_URL (default OLLAMA_BASE_URL or
 * http://localhost:11434, + /v1).
 *
 * Deliberately NOT wired into getModel() itself: /api/settings/test-provider
 * and friends must keep reporting the REAL provider status — a failover
 * there would fake a green check for a dead key.
 */

import { wrapLanguageModel, type LanguageModel } from "ai";
import type { LanguageModelV3 } from "@ai-sdk/provider";
import { getModel, MissingApiKeyError, buildProvider } from "./models";
import { buildOllamaMeta, type ModelMeta } from "./models-meta";

const DEFAULT_LOCAL_MODEL = "qwen2.5:7b";

export function localFailoverEnabled(): boolean {
  const v = (process.env.JARVIS_LOCAL_LLM_FAILOVER ?? "").trim().toLowerCase();
  // Default ON — unset arms the last-resort tail. Explicit opt-out only.
  return !(v === "0" || v === "false" || v === "no" || v === "off");
}

export function localFailoverTag(): string {
  return (process.env.JARVIS_LOCAL_LLM_MODEL ?? "").trim() || DEFAULT_LOCAL_MODEL;
}

/** OpenAI-compat base for the local rung, e.g. http://localhost:11434/v1. */
function localBaseURL(): string {
  const explicit = (process.env.JARVIS_LOCAL_LLM_URL ?? "").trim();
  if (explicit) return explicit.replace(/\/+$/, "");
  const root = (process.env.OLLAMA_BASE_URL ?? "http://localhost:11434").replace(
    /\/+$/,
    "",
  );
  return `${root}/v1`;
}

/** Daemon root (native API) — /api/tags lives here, not under /v1. */
function localDaemonRoot(): string {
  return localBaseURL().replace(/\/v1$/, "");
}

/**
 * Is a local Ollama reachable AND is the failover model pulled? Only ever
 * called on the failure path (cloud already failed), so its 1.5 s cap costs
 * the healthy path nothing. On the deployed VPS this returns false fast and
 * the cloud error surfaces unchanged.
 */
export async function probeLocalOllama(timeoutMs = 1500): Promise<boolean> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${localDaemonRoot()}/api/tags`, {
      signal: controller.signal,
      cache: "no-store",
    });
    if (!res.ok) return false;
    const data = (await res.json()) as {
      models?: Array<{ name?: string; model?: string }>;
    };
    const want = localFailoverTag();
    return (data.models ?? []).some(
      (m) => (m.model ?? m.name ?? "").trim() === want,
    );
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

const NETWORK_ERR_RE =
  /econnrefused|econnreset|etimedout|enotfound|eai_again|socket hang up|fetch failed|network|unable to connect|terminated|connection (?:refused|closed|error)/i;

/**
 * Which failures count as a genuine outage (→ local retry allowed)?
 *   - MissingApiKeyError: the cloud is unusable from here — spec'd trigger.
 *   - HTTP 5xx / 408 / 529: provider-side failure.
 *   - No HTTP status + network-shaped message (walked through .cause):
 *     connection refused / DNS / reset / timeout.
 * NOT an outage: 429 (quota/rate — the provider ANSWERED) and every other
 * 4xx (the request/key is the problem), plus any non-network error without
 * a status (schema/validation bugs must surface, not get masked by a
 * different model answering).
 */
export function isOutageError(err: unknown): boolean {
  if (err instanceof MissingApiKeyError) return true;
  for (
    let e: unknown = err, depth = 0;
    e instanceof Error && depth < 4;
    e = e.cause, depth++
  ) {
    const status = (e as { statusCode?: unknown }).statusCode;
    if (typeof status === "number") {
      return status === 408 || (status >= 500 && status <= 599);
    }
    if (NETWORK_ERR_RE.test(e.message) || NETWORK_ERR_RE.test(e.name)) {
      return true;
    }
  }
  return false;
}

/** Build the local LanguageModel rung (ollama OpenAI-compat, qwen2.5:7b). */
export function buildLocalFailoverModel(): LanguageModelV3 {
  const factory = buildProvider(
    "ollama",
    process.env.OLLAMA_API_KEY ?? "ollama",
    localBaseURL(),
  );
  return factory(localFailoverTag()) as LanguageModelV3;
}

/** Test seams — production callers pass nothing. */
export type LocalFailoverDeps = {
  enabled?: boolean;
  probe?: () => Promise<boolean>;
  localModel?: () => LanguageModelV3;
};

/**
 * Wrap a cloud model so a genuine-outage failure retries once against local
 * Ollama. Pass-through when the cloud call succeeds — the try branch returns
 * the untouched doStream/doGenerate result, so online behavior is identical.
 * Mid-stream drops (error AFTER the stream opened) are not retried — only
 * failures to start the call.
 */
export function withLocalFailover(
  model: LanguageModel,
  deps: LocalFailoverDeps = {},
): LanguageModel {
  if (!(deps.enabled ?? localFailoverEnabled())) return model;
  const probe = deps.probe ?? (() => probeLocalOllama());
  const makeLocal = deps.localModel ?? buildLocalFailoverModel;

  const localOnOutage = async (err: unknown): Promise<LanguageModelV3 | null> => {
    if (!isOutageError(err)) return null;
    if (!(await probe())) return null;
    console.warn(
      `[ai] cloud model call failed (${err instanceof Error ? err.message : String(err)}) — ` +
        `retrying on local ollama ${localFailoverTag()} (JARVIS_LOCAL_LLM_FAILOVER)`,
    );
    return makeLocal();
  };

  return wrapLanguageModel({
    model: model as LanguageModelV3,
    middleware: {
      specificationVersion: "v3",
      wrapGenerate: async ({ doGenerate, params }) => {
        try {
          return await doGenerate();
        } catch (err) {
          const local = await localOnOutage(err);
          if (!local) throw err;
          return await local.doGenerate(params);
        }
      },
      wrapStream: async ({ doStream, params }) => {
        try {
          return await doStream();
        } catch (err) {
          const local = await localOnOutage(err);
          if (!local) throw err;
          return await local.doStream(params);
        }
      },
    },
  }) as LanguageModel;
}

/**
 * Drop-in replacement for getModel() on the chat path.
 *   - Cloud model resolves + has a key → same meta, model wrapped in the
 *     pass-through failover middleware (no behavior change while healthy).
 *   - Model is already local (ollama) → returned untouched (no self-wrap).
 *   - MissingApiKeyError AND local Ollama reachable → serve the turn from
 *     local qwen2.5:7b. Not reachable (e.g. deployed VPS) → the original
 *     error propagates and the route's 400 handling behaves exactly as
 *     before Stage 2.
 */
export async function getModelWithLocalFailover(
  id: string,
  deps: LocalFailoverDeps = {},
): Promise<{ meta: ModelMeta; model: LanguageModel }> {
  const enabled = deps.enabled ?? localFailoverEnabled();
  try {
    const selected = await getModel(id);
    if (!enabled || selected.meta.provider === "ollama") return selected;
    return { ...selected, model: withLocalFailover(selected.model, deps) };
  } catch (err) {
    if (enabled && err instanceof MissingApiKeyError) {
      const probe = deps.probe ?? (() => probeLocalOllama());
      if (await probe()) {
        const tag = localFailoverTag();
        console.warn(
          `[ai] no API key for "${err.provider}" — serving from local ollama ${tag} (JARVIS_LOCAL_LLM_FAILOVER)`,
        );
        const makeLocal = deps.localModel ?? buildLocalFailoverModel;
        return {
          meta: buildOllamaMeta(tag),
          model: makeLocal() as LanguageModel,
        };
      }
    }
    throw err;
  }
}
