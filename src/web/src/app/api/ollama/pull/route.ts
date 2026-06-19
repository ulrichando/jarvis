import { resolveOllamaBaseURL } from "@/lib/ollama";

export const runtime = "nodejs";

/**
 * POST /api/ollama/pull { name } — pull a model into the connected Ollama,
 * streaming the native NDJSON progress straight back to the client (one JSON
 * object per line: {status}, {status:"downloading",completed,total}, …,
 * {status:"success"}). The UI parses the stream to drive a progress bar.
 */
export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { name?: string };
  const name = (body.name ?? "").trim();
  if (!name) {
    return Response.json({ error: "model name required" }, { status: 400 });
  }
  // Model tags are alphanum + a small punctuation set (e.g. `llama3.2:3b`,
  // `library/qwen2.5-coder:7b`). Reject anything else before proxying.
  if (!/^[A-Za-z0-9._:/-]+$/.test(name)) {
    return Response.json({ error: "invalid model name" }, { status: 400 });
  }

  const base = await resolveOllamaBaseURL();
  const upstream = await fetch(`${base}/api/pull`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model: name, stream: true }),
  }).catch(() => null);

  if (!upstream || !upstream.ok || !upstream.body) {
    return Response.json(
      { error: `pull failed to start (${upstream ? upstream.status : "no connection"})` },
      { status: 502 },
    );
  }

  // Pass the upstream NDJSON stream through unchanged.
  return new Response(upstream.body, {
    headers: {
      "content-type": "application/x-ndjson; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
