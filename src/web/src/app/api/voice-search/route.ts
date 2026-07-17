import { getUserId } from "@/lib/auth-helpers";
import { verifyProxyToken } from "@/lib/bridge/proxyJwt";
import { getOrCreateProxyJwtSecret } from "@/lib/bridge/proxySecret";
import { bestSnippet } from "@/lib/search/core";
import { enrichHitsWithContent, runSearchPipeline } from "@/lib/search/pipeline";

export const runtime = "nodejs";

// /api/voice-search — web search for the LiveKit voice agent ("research" in
// voice mode). The agent runs host-networked on the VPS and cannot reach the
// internal SearXNG service (no published port), but jarvis-web IS on the
// searxng network — so the agent calls here with its service proxy JWT and we
// run the shared search pipeline (src/lib/search): Brave Search API when
// BRAVE_SEARCH_API_KEY is set, else SearXNG → DuckDuckGo keyless, with
// homepage/junk filtering, domain dedup, and reranking.
//
// Voice latency discipline: Brave results already carry extra_snippets (real
// page text with ZERO fetch latency), so the Brave path skips page fetching
// by default and answers in ~1s. Only the keyless fallback — whose snippets
// are thin — fetches the top pages, in parallel under a hard ~2s budget.
// Override with VOICE_SEARCH_FETCH_PAGES / VOICE_SEARCH_FETCH_TIMEOUT_MS.
//
// Auth: better-auth session OR the voice-agent proxy-JWT service token
// (verifyProxyToken, sub === "voice-agent"). proxy.ts allowlists this in
// SELF_AUTH_GET_PATTERNS. Read-only, no user-scoped data.

const SERVICE_SUB = "voice-agent";
const MAX_RESULTS = 8;
// Per-backend cap. The agent's own HTTP timeout is 12s total — worst case
// here is brave+searxng+ddg+fetch ≈ 4+4+4+2.5s, still under it in practice
// because dead backends fail fast (HTTP error, not timeout).
const SEARCH_TIMEOUT_MS = 4000;
const CONTENT_MAX_CHARS = 1500;
const SNIPPET_MAX_CHARS = 500;

function envInt(name: string, fallback: number): number {
  const n = parseInt(process.env[name] ?? "", 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

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

  const braveKey = (process.env.BRAVE_SEARCH_API_KEY ?? "").trim();
  const searxng = (process.env.SEARXNG_URL ?? "").trim();
  if (!braveKey && !searxng) {
    return Response.json({ error: "search not configured" }, { status: 503 });
  }

  try {
    const { hits, provider } = await runSearchPipeline(q, {
      maxResults: MAX_RESULTS,
      searchTimeoutMs: SEARCH_TIMEOUT_MS,
      log: (m) => console.warn(`[voice-search] ${m}`),
    });
    const pages = envInt("VOICE_SEARCH_FETCH_PAGES", provider === "brave" ? 0 : 2);
    const enriched = await enrichHitsWithContent(hits, {
      pages,
      timeoutMs: envInt("VOICE_SEARCH_FETCH_TIMEOUT_MS", 2000),
      budgetMs: envInt("VOICE_SEARCH_FETCH_TIMEOUT_MS", 2000) + 500,
      maxChars: CONTENT_MAX_CHARS,
    });
    return Response.json({
      hits: enriched.map((h) => ({
        title: h.title,
        url: h.url,
        snippet: bestSnippet(h, SNIPPET_MAX_CHARS),
        ...(h.content ? { content: h.content } : {}),
      })),
    });
  } catch {
    return Response.json({ error: "search failed" }, { status: 502 });
  }
}
