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

/**
 * DELETE /api/ollama/models?name=<model> — remove an installed model.
 * Proxies Ollama's DELETE /api/delete (destructive — re-pull to get it back).
 */
export async function DELETE(req: Request) {
  const name = (new URL(req.url).searchParams.get("name") ?? "").trim();
  if (!name) {
    return Response.json({ error: "model name required" }, { status: 400 });
  }
  if (!/^[A-Za-z0-9._:/-]+$/.test(name)) {
    return Response.json({ error: "invalid model name" }, { status: 400 });
  }
  const base = await resolveOllamaBaseURL();
  const res = await fetch(`${base}/api/delete`, {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model: name }),
  }).catch(() => null);
  if (!res) {
    return Response.json({ error: "no connection to Ollama" }, { status: 502 });
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    return Response.json(
      { error: `delete failed (${res.status})${text ? `: ${text.slice(0, 120)}` : ""}` },
      { status: res.status === 404 ? 404 : 502 },
    );
  }
  return Response.json({ ok: true });
}
