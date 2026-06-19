import { resolveOllamaBaseURL, type OllamaModel } from "@/lib/ollama";

export const runtime = "nodejs";

type OllamaTag = {
  name: string;
  size?: number;
  modified_at?: string;
  details?: { family?: string; parameter_size?: string };
};

/**
 * GET /api/ollama/models — verify the connection + list installed models.
 * Proxies the resolved Ollama base URL's /api/version + /api/tags (the same
 * server the voice-agent's local LLM pulls from). Returns 502 with the base URL
 * when Ollama is unreachable so the UI can show a clear "not connected" state.
 */
export async function GET() {
  const base = await resolveOllamaBaseURL();
  try {
    const [verRes, tagsRes] = await Promise.all([
      fetch(`${base}/api/version`, { cache: "no-store" }).catch(() => null),
      fetch(`${base}/api/tags`, { cache: "no-store" }),
    ]);
    if (!tagsRes.ok) {
      return Response.json(
        { ok: false, baseURL: base, error: `Ollama returned ${tagsRes.status}` },
        { status: 502 },
      );
    }
    const data = (await tagsRes.json()) as { models?: OllamaTag[] };
    const version =
      verRes && verRes.ok ? ((await verRes.json()) as { version?: string }).version : undefined;
    const models: OllamaModel[] = (data.models ?? []).map((m) => ({
      name: m.name,
      size: m.size,
      modified: m.modified_at,
      family: m.details?.family,
      parameterSize: m.details?.parameter_size,
    }));
    return Response.json({ ok: true, baseURL: base, version, models });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "connection failed";
    return Response.json({ ok: false, baseURL: base, error: msg }, { status: 502 });
  }
}
