import { tool } from "ai";
import { z } from "zod";
import { search, SafeSearchType } from "duck-duck-scrape";

const inputSchema = z.object({
  query: z.string().describe("The search query to look up"),
});

type Hit = { title: string; url: string; snippet: string };

// Self-hosted SearXNG (same SEARXNG_URL the voice + CLI stacks use). The
// DuckDuckGo scraper CAPTCHA-blocks datacenter/VPS IPs, so on the deployed box
// the DDG path returns "Search unavailable" — SearXNG is immune. Needs
// `search.formats: [html, json]` server-side or /search?format=json 403s.
async function searchSearxng(query: string): Promise<Hit[]> {
  const base = (process.env.SEARXNG_URL ?? "").trim().replace(/\/+$/, "");
  const url = `${base}/search?q=${encodeURIComponent(query)}&format=json&pageno=1`;
  // Bound the call so a hung searxng can't stall the whole chat turn (the voice
  // plugin uses the same 15s ceiling).
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(`SearXNG HTTP ${res.status}`);
  const data = (await res.json()) as { results?: unknown[] };
  const results = Array.isArray(data?.results) ? data.results : [];
  return results
    .map((r) => {
      const o = r as { title?: unknown; url?: unknown; content?: unknown };
      return {
        title: String(o?.title ?? "").trim(),
        url: String(o?.url ?? "").trim(),
        snippet: String(o?.content ?? "").trim(),
      };
    })
    .filter((h) => h.title && h.url)
    .slice(0, 5);
}

async function searchDuckDuckGo(query: string): Promise<Hit[]> {
  const results = await search(query, { safeSearch: SafeSearchType.MODERATE });
  if (results.noResults || results.results.length === 0) return [];
  return results.results.slice(0, 5).map((r) => ({
    title: r.title,
    url: r.url,
    snippet: r.description,
  }));
}

export const webSearchTool = tool({
  description:
    "Search the web for current, real-time information — news, prices, weather, live events, recent facts. Use whenever the question requires up-to-date knowledge beyond your training data.",
  inputSchema,
  execute: async ({ query }) => {
    try {
      const results = (process.env.SEARXNG_URL ?? "").trim()
        ? await searchSearxng(query)
        : await searchDuckDuckGo(query);
      return { query, results };
    } catch (err) {
      // Log so a searxng-down failure is distinguishable from a DDG block in prod.
      console.error("[web-search] search failed:", err);
      return { query, results: [] as Hit[], error: "Search unavailable" };
    }
  },
});
