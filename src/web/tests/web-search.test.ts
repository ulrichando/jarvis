import { afterEach, describe, expect, it, vi } from "vitest";

// duck-duck-scrape is the local-dev fallback; stub it so tests don't hit the net.
const ddgSearch = vi.fn();
vi.mock("duck-duck-scrape", () => ({
  search: (...a: unknown[]) => ddgSearch(...a),
  SafeSearchType: { MODERATE: 0 },
}));

import { webSearchTool } from "../src/lib/tools/web-search";

type Out = { query: string; results: { title: string; url: string; snippet: string }[]; error?: string };

// tool.execute has a required second arg (ToolCallOptions) we don't use.
const run = (query: string): Promise<Out> =>
  (webSearchTool.execute as (i: { query: string }, o: unknown) => Promise<Out>)(
    { query },
    {},
  );

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete process.env.SEARXNG_URL;
  ddgSearch.mockReset();
});

describe("webSearchTool", () => {
  it("uses SearXNG and maps content→snippet when SEARXNG_URL is set", async () => {
    process.env.SEARXNG_URL = "http://searxng:8080/";
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      json: async () => ({
        results: [
          { title: "A", url: "http://a", content: "snip a" },
          { title: "", url: "http://skip", content: "no title dropped" },
        ],
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const out = await run("hello");
    expect(fetchMock.mock.calls[0][0]).toContain(
      "http://searxng:8080/search?q=hello&format=json",
    );
    expect(out.results).toEqual([{ title: "A", url: "http://a", snippet: "snip a" }]);
    expect(ddgSearch).not.toHaveBeenCalled();
  });

  it("returns error='Search unavailable' when SearXNG 403s (the reported bug)", async () => {
    process.env.SEARXNG_URL = "http://searxng:8080";
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 403 })));
    const out = await run("x");
    expect(out.error).toBe("Search unavailable");
    expect(out.results).toEqual([]);
  });

  it("falls back to DuckDuckGo when SEARXNG_URL is unset", async () => {
    ddgSearch.mockResolvedValue({
      noResults: false,
      results: [{ title: "D", url: "http://d", description: "desc" }],
    });
    const out = await run("y");
    expect(ddgSearch).toHaveBeenCalled();
    expect(out.results).toEqual([{ title: "D", url: "http://d", snippet: "desc" }]);
  });
});
