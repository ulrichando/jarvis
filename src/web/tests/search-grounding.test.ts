import { afterEach, describe, expect, it, vi } from "vitest";

// Mock the shared Brave backend so no test hits the network — same pattern
// as web-search.test.ts (search-grounding imports searchBrave from the
// shared pipeline module).
const searchBrave = vi.fn();
vi.mock("@/lib/search/pipeline", () => ({
  searchBrave: (...a: unknown[]) => searchBrave(...a),
}));

import {
  hasSearchIntent,
  isWeakToolCaller,
  formatSearchGrounding,
  maybeGroundWithSearch,
} from "../src/lib/chat/search-grounding";

afterEach(() => {
  vi.restoreAllMocks();
  searchBrave.mockReset();
});

const HITS = [
  { title: "Alpha", url: "https://a.example/one", snippet: "alpha snippet" },
  { title: "Beta", url: "https://b.example/two", snippet: "", extraSnippets: ["beta extra"] },
  { title: "Gamma", url: "https://c.example/three", snippet: "gamma snippet" },
];

describe("hasSearchIntent", () => {
  const positives = [
    "what's the latest news on OpenAI",
    "current price of BTC",
    "who won the game today",
    "search for the best mechanical keyboards",
    "weather in Paris",
    "look up the Next.js 16 release date",
    "best laptops 2026",
    "what's the BTC price right now",
    "as of today, is the merge complete?",
  ];
  for (const q of positives) {
    it(`fires on: "${q}"`, () => {
      expect(hasSearchIntent(q)).toBe(true);
    });
  }

  const negatives = [
    "write a poem",
    "explain recursion",
    "refactor this function",
    "thanks",
    "can you review my current implementation of the parser",
    "I'm tired today",
    "",
  ];
  for (const q of negatives) {
    it(`does NOT fire on: "${q}"`, () => {
      expect(hasSearchIntent(q)).toBe(false);
    });
  }
});

describe("isWeakToolCaller", () => {
  it("flags the whole DeepSeek family", () => {
    for (const id of [
      "deepseek-chat",
      "deepseek-v4-flash",
      "deepseek-reasoner",
      "deepseek-v4-pro",
    ]) {
      expect(isWeakToolCaller(id)).toBe(true);
    }
  });

  it("flags thinking models that reject tool_choice (Kimi thinking)", () => {
    expect(isWeakToolCaller("kimi-k2-thinking")).toBe(true);
  });

  it("leaves strong tool-callers alone (Claude / GPT / Gemini, incl. o3)", () => {
    for (const id of [
      "claude-fable-5",
      "claude-sonnet-4-6",
      "claude-haiku-4-5",
      "gpt-5",
      "gpt-5-mini",
      "o3", // OpenAI reasoner but a strong tool-caller — must NOT be grounded
      "gemini-2.5-pro",
      "gemini-2.5-flash",
    ]) {
      expect(isWeakToolCaller(id)).toBe(false);
    }
  });

  it("does not flag non-reasoning Kimi or unknown models", () => {
    expect(isWeakToolCaller("kimi-k2-instant")).toBe(false);
    expect(isWeakToolCaller("some-unknown-model")).toBe(false);
  });
});

describe("maybeGroundWithSearch (injection gating)", () => {
  it("fires for a DeepSeek model + search-intent query and injects the results", async () => {
    searchBrave.mockResolvedValue(HITS);
    const block = await maybeGroundWithSearch({
      modelId: "deepseek-v4-flash",
      search: undefined, // toggle not off
      workspaceId: undefined,
      userText: "what's the latest news on quantum computing",
    });
    expect(searchBrave).toHaveBeenCalledTimes(1);
    expect(searchBrave).toHaveBeenCalledWith(
      "what's the latest news on quantum computing",
      expect.objectContaining({ count: 8 }),
    );
    expect(block).toBeTruthy();
    // Results text lands in the block the route appends to the system prompt.
    expect(block).toContain("Web search results");
    expect(block).toContain("Alpha");
    expect(block).toContain("https://a.example/one");
    expect(block).toContain("alpha snippet");
    expect(block).toContain("beta extra"); // extraSnippets fallback
    expect(block).toContain('Query: "what\'s the latest news on quantum computing"');
    // Anti-"search is down" instruction is present.
    expect(block).toMatch(/never claim it is down/i);
  });

  it("does NOT fire for a strong tool-caller (no Brave call)", async () => {
    const block = await maybeGroundWithSearch({
      modelId: "claude-fable-5",
      search: undefined,
      workspaceId: undefined,
      userText: "what's the latest news on quantum computing",
    });
    expect(block).toBeNull();
    expect(searchBrave).not.toHaveBeenCalled();
  });

  it("does NOT fire when the Search toggle is off", async () => {
    const block = await maybeGroundWithSearch({
      modelId: "deepseek-chat",
      search: false,
      workspaceId: undefined,
      userText: "what's the latest news on quantum computing",
    });
    expect(block).toBeNull();
    expect(searchBrave).not.toHaveBeenCalled();
  });

  it("does NOT fire for a non-search query", async () => {
    const block = await maybeGroundWithSearch({
      modelId: "deepseek-chat",
      search: undefined,
      workspaceId: undefined,
      userText: "refactor this function",
    });
    expect(block).toBeNull();
    expect(searchBrave).not.toHaveBeenCalled();
  });

  it("does NOT fire on workspace turns", async () => {
    const block = await maybeGroundWithSearch({
      modelId: "deepseek-chat",
      search: undefined,
      workspaceId: "ws-1",
      userText: "what's the latest news on quantum computing",
    });
    expect(block).toBeNull();
    expect(searchBrave).not.toHaveBeenCalled();
  });

  it("falls through silently (null) when Brave errors", async () => {
    searchBrave.mockRejectedValue(new Error("Brave HTTP 500"));
    const block = await maybeGroundWithSearch({
      modelId: "deepseek-chat",
      search: undefined,
      workspaceId: undefined,
      userText: "current price of BTC",
    });
    expect(block).toBeNull();
  });

  it("falls through silently (null) on empty results", async () => {
    searchBrave.mockResolvedValue([]);
    const block = await maybeGroundWithSearch({
      modelId: "deepseek-chat",
      search: undefined,
      workspaceId: undefined,
      userText: "current price of BTC",
    });
    expect(block).toBeNull();
  });

  it("falls through silently (null) when the search exceeds the timeout", async () => {
    searchBrave.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(HITS), 5_000)),
    );
    const block = await maybeGroundWithSearch({
      modelId: "deepseek-chat",
      search: undefined,
      workspaceId: undefined,
      userText: "current price of BTC",
      timeoutMs: 50,
    });
    expect(block).toBeNull();
  });
});

describe("formatSearchGrounding", () => {
  it("caps at 5 hits and numbers them", () => {
    const many = Array.from({ length: 8 }, (_, i) => ({
      title: `T${i}`,
      url: `https://x.example/${i}`,
      snippet: `s${i}`,
    }));
    const block = formatSearchGrounding("q", many);
    expect(block).toContain("5. T4");
    expect(block).not.toContain("T5");
  });
});
