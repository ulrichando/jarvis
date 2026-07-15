import { getUserId } from "@/lib/auth-helpers";
import { verifyProxyToken } from "@/lib/bridge/proxyJwt";
import { getOrCreateProxyJwtSecret } from "@/lib/bridge/proxySecret";

export const runtime = "nodejs";

// /api/voice-search — web search for the LiveKit voice agent ("research" in
// voice mode). The agent runs host-networked on the VPS and cannot reach the
// internal SearXNG service (no published port), but jarvis-web IS on the
// searxng network — so the agent calls here with its service proxy JWT and we
// proxy the query to SearXNG (the same SEARXNG_URL the chat proxy uses).
//
// Auth: better-auth session OR the voice-agent proxy-JWT service token
// (verifyProxyToken, sub === "voice-agent"). proxy.ts allowlists this in
// SELF_AUTH_GET_PATTERNS. Read-only, no user-scoped data.

const SERVICE_SUB = "voice-agent";
const MAX_RESULTS = 8;
const SEARCH_TIMEOUT_MS = 8000;

async function isAuthed(req: Request): Promise<boolean> {
  if (await getUserId(req.headers)) return true;
  const authz = req.headers.get("authorization") ?? "";
  const bearer = /^bearer /i.test(authz) ? authz.slice(7).trim() : "";
  if (!bearer) return false;
  const result = verifyProxyToken(bearer, getOrCreateProxyJwtSecret());
  return result.ok && result.claims.sub === SERVICE_SUB;
}

export async function GET(req: Request) {
  if (!(await isAuthed(req))) return new Response("Unauthorized", { status: 401 });

  const url = new URL(req.url);
  const q = (url.searchParams.get("q") ?? "").trim();
  if (!q) return Response.json({ hits: [] });

  const base = (process.env.SEARXNG_URL ?? "").trim().replace(/\/+$/, "");
  if (!base) {
    return Response.json({ error: "search not configured" }, { status: 503 });
  }

  try {
    // SearXNG needs `search.formats: [html, json]` server-side or json 403s.
    const searchUrl = `${base}/search?q=${encodeURIComponent(q)}&format=json&pageno=1`;
    const res = await fetch(searchUrl, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(SEARCH_TIMEOUT_MS),
    });
    if (!res.ok) {
      return Response.json({ error: `search HTTP ${res.status}` }, { status: 502 });
    }
    const data = (await res.json()) as { results?: unknown };
    const results = Array.isArray(data?.results) ? data.results : [];
    const hits = results
      .map((r: unknown) => {
        const o = (r ?? {}) as Record<string, unknown>;
        return {
          title: String(o.title ?? "").trim(),
          url: String(o.url ?? "").trim(),
          snippet: String(o.content ?? "").trim(),
        };
      })
      .filter((h) => h.title && h.url)
      .slice(0, MAX_RESULTS);
    return Response.json({ hits });
  } catch {
    return Response.json({ error: "search failed" }, { status: 502 });
  }
}
