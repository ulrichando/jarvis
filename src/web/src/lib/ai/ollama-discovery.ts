import "server-only";

// Enumerates models installed in the local Ollama daemon so the model picker
// can list every model the user has pulled — not just the two hardcoded in
// models-meta.ts. Server-only: the browser can't reliably reach :11434
// (cross-origin), so the picker fetches /api/providers/ollama-models, which
// calls this. Best-effort: an offline/missing daemon yields [] and the picker
// falls back to the static entries.

export type DiscoveredOllamaModel = {
  /** Stable picker id; encodes the tag so the server can route it without a
   *  shared registry entry. See models.ts::getModel + models-meta::ollamaIdToTag. */
  id: string;
  /** The exact ollama tag, e.g. "qwen3:30b-a3b". */
  tag: string;
};

function ollamaApiBase(): string {
  // The chat client appends "/v1"; /api/tags lives at the daemon root.
  return (process.env.OLLAMA_BASE_URL ?? "http://localhost:11434").replace(
    /\/+$/,
    "",
  );
}

export async function fetchInstalledOllamaModels(): Promise<
  DiscoveredOllamaModel[]
> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 1500);
  try {
    const res = await fetch(`${ollamaApiBase()}/api/tags`, {
      signal: controller.signal,
      cache: "no-store",
    });
    if (!res.ok) return [];
    const data = (await res.json()) as {
      models?: Array<{ name?: string; model?: string }>;
    };
    const seen = new Set<string>();
    const out: DiscoveredOllamaModel[] = [];
    for (const m of data.models ?? []) {
      const tag = (m.model ?? m.name ?? "").trim();
      if (!tag || seen.has(tag)) continue;
      seen.add(tag);
      out.push({ id: `ollama:${tag}`, tag });
    }
    return out;
  } catch {
    // Daemon offline / unreachable / malformed — fall back to static entries.
    return [];
  } finally {
    clearTimeout(timeout);
  }
}
