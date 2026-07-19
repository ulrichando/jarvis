import { tool } from "ai";
import { z } from "zod";
import { searchBrave } from "@/lib/search/pipeline";

const inputSchema = z.object({
  query: z.string().describe("The search query to look up"),
});

type Hit = { title: string; url: string; snippet: string };

// Brave Search API is the ONLY web-search backend (2026-07-19, per Ulrich:
// Brave-only — the SearXNG and DuckDuckGo-scraper paths were removed). Reuses
// the shared, rate-limit-aware `searchBrave()` from src/lib/search so chat and
// voice hit exactly one backend. Requires BRAVE_SEARCH_API_KEY; without it the
// tool returns an explicit "not configured" error instead of a silent empty
// result (searchBrave throws "BRAVE_SEARCH_API_KEY not set").
export const webSearchTool = tool({
  description:
    "Search the web for current, real-time information — news, prices, weather, live events, recent facts. Use whenever the question requires up-to-date knowledge beyond your training data.",
  inputSchema,
  execute: async ({ query }) => {
    try {
      const hits = await searchBrave(query, { count: 8 });
      const results: Hit[] = hits.slice(0, 5).map((h) => ({
        title: h.title,
        url: h.url,
        snippet: h.snippet || (h.extraSnippets?.[0] ?? ""),
      }));
      return { query, results };
    } catch (err) {
      console.error("[web-search] Brave search failed:", err);
      const notConfigured =
        err instanceof Error && /BRAVE_SEARCH_API_KEY/.test(err.message);
      return {
        query,
        results: [] as Hit[],
        error: notConfigured
          ? "Web search isn't configured — set BRAVE_SEARCH_API_KEY."
          : "Search unavailable",
      };
    }
  },
});
