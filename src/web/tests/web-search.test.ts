import { afterEach, describe, expect, it, vi } from "vitest";

// Brave is the ONLY backend now — stub the shared searchBrave so tests don't
// hit the network. web-search.ts imports it from "@/lib/search/pipeline".
const searchBrave = vi.fn();
vi.mock("@/lib/search/pipeline", () => ({
  searchBrave: (...a: unknown[]) => searchBrave(...a),
}));

import { webSearchTool } from "../src/lib/tools/web-search";

type Out = {
  query: string;
  results: { title: string; url: string; snippet: string }[];
  error?: string;
};

// tool.execute has a required second arg (ToolCallOptions) we don't use.
const run = (query: string): Promise<Out> =>
  (webSearchTool.execute as (i: { query: string }, o: unknown) => Promise<Out>)(
    { query },
    {},
  );

afterEach(() => {
  vi.restoreAllMocks();
  searchBrave.mockReset();
});

describe("webSearchTool (Brave-only)", () => {
  it("returns Brave hits mapped to {title,url,snippet}, extraSnippets as fallback", async () => {
    searchBrave.mockResolvedValue([
      { title: "A", url: "http://a", snippet: "snip a" },
      { title: "B", url: "http://b", snippet: "", extraSnippets: ["extra b"] },
    ]);
    const out = await run("hello");
    expect(searchBrave).toHaveBeenCalledWith("hello", { count: 8 });
    expect(out.results).toEqual([
      { title: "A", url: "http://a", snippet: "snip a" },
      { title: "B", url: "http://b", snippet: "extra b" },
    ]);
    expect(out.error).toBeUndefined();
  });

  it("caps at 5 results", async () => {
    searchBrave.mockResolvedValue(
      Array.from({ length: 8 }, (_, i) => ({
        title: `T${i}`,
        url: `http://${i}`,
        snippet: `s${i}`,
      })),
    );
    const out = await run("many");
    expect(out.results).toHaveLength(5);
  });

  it("returns a 'not configured' error when BRAVE_SEARCH_API_KEY is missing", async () => {
    searchBrave.mockRejectedValue(new Error("BRAVE_SEARCH_API_KEY not set"));
    const out = await run("x");
    expect(out.results).toEqual([]);
    expect(out.error).toMatch(/BRAVE_SEARCH_API_KEY/);
  });

  it("returns 'Search unavailable' on any other Brave error", async () => {
    searchBrave.mockRejectedValue(new Error("Brave HTTP 500"));
    const out = await run("y");
    expect(out.results).toEqual([]);
    expect(out.error).toBe("Search unavailable");
  });
});
