import { tool } from "ai";
import { z } from "zod";
import { search, SafeSearchType } from "duck-duck-scrape";

const inputSchema = z.object({
  query: z.string().describe("The search query to look up"),
});

export const webSearchTool = tool({
  description:
    "Search the web for current, real-time information — news, prices, weather, live events, recent facts. Use whenever the question requires up-to-date knowledge beyond your training data.",
  inputSchema,
  execute: async ({ query }) => {
    try {
      const results = await search(query, {
        safeSearch: SafeSearchType.MODERATE,
      });

      if (results.noResults || results.results.length === 0) {
        return { query, results: [] as { title: string; url: string; snippet: string }[] };
      }

      return {
        query,
        results: results.results.slice(0, 5).map((r) => ({
          title: r.title,
          url: r.url,
          snippet: r.description,
        })),
      };
    } catch {
      return {
        query,
        results: [] as { title: string; url: string; snippet: string }[],
        error: "Search unavailable",
      };
    }
  },
});
