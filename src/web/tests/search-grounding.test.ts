import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the shared Brave backend so no test hits the network — same pattern
// as web-search.test.ts (search-grounding imports searchBrave from the
// shared pipeline module).
const searchBrave = vi.fn();
vi.mock("@/lib/search/pipeline", () => ({
  searchBrave: (...a: unknown[]) => searchBrave(...a),
}));

// Mock the query-gen task-model call: search-grounding only imports
// `generateText` from "ai" (everything else this file pulls from "ai" is
// type-only, so a full module mock is safe — same as tests/kimi/*).
const generateText = vi.fn();
vi.mock("ai", () => ({
  generateText: (...a: unknown[]) => generateText(...a),
}));

// Mock model resolution so no settings/keys are touched. generateSearchQueries
// resolves the task model via getModel(id) before calling generateText.
const getModel = vi.fn();
vi.mock("@/lib/ai/models", () => ({
  getModel: (...a: unknown[]) => getModel(...a),
}));

import {
  hasSearchIntent,
  isWeakToolCaller,
  generateSearchQueries,
  parseQueryGenOutput,
  formatSearchGrounding,
  maybeGroundWithSearch,
  shouldOfferWebSearchTool,
  toWebSearchToolPart,
  SERVER_SEARCH_TOOL_CALL_ID,
  type SearchGrounding,
} from "../src/lib/chat/search-grounding";
import { extractSources } from "../src/components/chat/sources";
import type { UIMessage } from "ai";

beforeEach(() => {
  getModel.mockResolvedValue({ model: { id: "task-model" }, meta: {} });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  searchBrave.mockReset();
  generateText.mockReset();
  getModel.mockReset();
});

const HITS = [
  { title: "Alpha", url: "https://a.example/one", snippet: "alpha snippet" },
  { title: "Beta", url: "https://b.example/two", snippet: "", extraSnippets: ["beta extra"] },
  { title: "Gamma", url: "https://c.example/three", snippet: "gamma snippet" },
];

/** The canonical weak tool-caller used across the grounding tests. */
const WEAK = "deepseek-v4-flash";

/** Shorthand: the task model proposes these queries (or NONE / prose). */
function modelReturns(text: string) {
  generateText.mockResolvedValue({ text });
}

describe("hasSearchIntent (THE intent gate)", () => {
  const positives = [
    // Original tight tier (news/prices/weather/sports/releases).
    "what's the latest news on OpenAI",
    "current price of BTC",
    "who won the game today",
    "search for the best mechanical keyboards",
    "weather in Paris",
    "look up the Next.js 16 release date",
    "best laptops 2026",
    "what's the BTC price right now",
    "as of today, is the merge complete?",
    // 2026-07-18 broadening — the LIVE misses that motivated it.
    "who is Michelle Jackson",
    "research tokenization and organization",
    // General informational lookups.
    "who was Nikola Tesla",
    "what is the capital of Burkina Faso",
    "what are tariffs and how do they affect prices",
    "tell me about the Roman Empire",
    "tell me more about the Voyager missions",
    "give me info on the Mars rover",
    "give me a rundown on the EU AI Act",
    "how does a nuclear reactor work",
    "how do black holes work",
    "when did the Berlin Wall fall",
    "where is Mount Kilimanjaro",
    "find information about SpaceX's next launch",
    "look into quantum computing startups",
    "learn about the history of jazz",
    "background on the suez canal crisis",
    "is Blockbuster still in business",
    // Explicit commands still bypass everything.
    "search the web for how to fix this bug",
    "google the wifi 7 spec",
  ];
  for (const q of positives) {
    it(`fires on: "${q}"`, () => {
      expect(hasSearchIntent(q)).toBe(true);
    });
  }

  const negatives = [
    // Original negatives — must STAY negative after the broadening.
    "write a poem",
    "explain recursion",
    "refactor this function",
    "thanks",
    "can you review my current implementation of the parser",
    "I'm tired today",
    "",
    // Code / engineering tasks.
    "write a function that sorts a list",
    "fix this bug in my parser",
    "debug the failing test",
    "implement a react component for the sidebar",
    "write some code to parse csv",
    // Creative writing.
    "write a story about dragons",
    "write me a haiku",
    "compose a song about summer",
    // Conversation-local / user's own material.
    "summarize the above",
    "what is wrong with my code",
    "summarize this conversation",
    "tell me about the above message", // referential wins over "tell me about"
    // Greetings / chit-chat / assistant-referential.
    "hello",
    "ok cool",
    "good morning",
    "who are you",
    "what's your name",
    // Pure arithmetic.
    "what is 2+2",
    "what is 144 / 12",
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

describe("parseQueryGenOutput", () => {
  it("parses one query per line", () => {
    expect(parseQueryGenOutput("barcelona last match result\nbarcelona score")).toEqual([
      "barcelona last match result",
      "barcelona score",
    ]);
  });

  it("strips list markers, quotes, and code fences", () => {
    expect(
      parseQueryGenOutput('```\n1. foo query\n- bar query\n"baz query"\n```'),
    ).toEqual(["foo query", "bar query", "baz query"]);
  });

  it("NONE / empty → [] (caller falls back to the raw user text)", () => {
    expect(parseQueryGenOutput("NONE")).toEqual([]);
    expect(parseQueryGenOutput("none.")).toEqual([]);
    expect(parseQueryGenOutput("")).toEqual([]);
    expect(parseQueryGenOutput("   \n  ")).toEqual([]);
  });

  it("dedupes (case-insensitive) and caps at 3", () => {
    expect(
      parseQueryGenOutput("Foo\nfoo\nbar\nbaz\nqux\nquux"),
    ).toEqual(["Foo", "bar", "baz"]);
  });

  // ── Hardening (review #2/#7): prose must never reach Brave as a query ──

  it("drops a preamble line and keeps the real queries", () => {
    expect(
      parseQueryGenOutput("Sure, here are the queries:\n1. barcelona away record 2026"),
    ).toEqual(["barcelona away record 2026"]);
  });

  it("apology / refusal output → [] so the caller's raw-text fallback fires", () => {
    expect(
      parseQueryGenOutput("I'm sorry, I can't search the web for you."),
    ).toEqual([]);
    expect(parseQueryGenOutput("I cannot help with that request.")).toEqual([]);
  });

  it('"No search needed." → [] (meta verdict, not a query)', () => {
    expect(parseQueryGenOutput("No search needed.")).toEqual([]);
  });

  it("drops lead-in lines ending in a colon", () => {
    expect(
      parseQueryGenOutput("Relevant search terms below:\nbtc price today"),
    ).toEqual(["btc price today"]);
  });

  it("caps an over-long prose line to a keyword-sized query", () => {
    const long =
      "the complete detailed history of the roman empire from its founding to its collapse with every emperor listed";
    const queries = parseQueryGenOutput(long);
    expect(queries).toEqual([
      "the complete detailed history of the roman empire from its founding to",
    ]);
    expect(queries[0].split(" ").length).toBeLessThanOrEqual(12);
    expect(queries[0].length).toBeLessThanOrEqual(120);
  });
});

describe("generateSearchQueries (query REFINEMENT — the regex gates, the LLM only rewrites)", () => {
  it("regex-negative text → [] with NO LLM call, ever", async () => {
    for (const text of [
      "explain recursion please",
      "thanks",
      "write me a haiku",
      "hello",
      "fix this bug in my parser",
    ]) {
      expect(await generateSearchQueries({ userText: text })).toEqual([]);
    }
    expect(generateText).not.toHaveBeenCalled();
    expect(getModel).not.toHaveBeenCalled();
  });

  it("empty input → [] with no LLM call", async () => {
    expect(await generateSearchQueries({ userText: "" })).toEqual([]);
    expect(await generateSearchQueries({ userText: "   " })).toEqual([]);
    expect(generateText).not.toHaveBeenCalled();
  });

  it('explicit "search for X" → [X] with no LLM call', async () => {
    expect(
      await generateSearchQueries({
        userText: "search for the best mechanical keyboards",
      }),
    ).toEqual(["the best mechanical keyboards"]);
    expect(
      await generateSearchQueries({ userText: "google the wifi 7 spec" }),
    ).toEqual(["the wifi 7 spec"]);
    expect(
      await generateSearchQueries({
        userText: "can you look up the Next.js 16 release date",
      }),
    ).toEqual(["the Next.js 16 release date"]);
    expect(generateText).not.toHaveBeenCalled();
  });

  it("explicit command mid-sentence keeps the whole text as the query", async () => {
    expect(
      await generateSearchQueries({
        userText: "I need you to look up the score",
      }),
    ).toEqual(["I need you to look up the score"]);
    expect(generateText).not.toHaveBeenCalled();
  });

  it("normal lookup → task model refines the query", async () => {
    modelReturns("Michelle Jackson biography\nwho is Michelle Jackson");
    const queries = await generateSearchQueries({
      userText: "who is Michelle Jackson",
    });
    expect(queries).toEqual([
      "Michelle Jackson biography",
      "who is Michelle Jackson",
    ]);
    expect(getModel).toHaveBeenCalledWith("deepseek-v4-flash");
    expect(generateText).toHaveBeenCalledTimes(1);
    const call = generateText.mock.calls[0][0] as Record<string, unknown>;
    expect(call.prompt).toContain("who is Michelle Jackson");
    expect(call.temperature).toBe(0);
    expect(call.maxOutputTokens).toBe(64);
  });

  it("passes recent turns to the task model for context", async () => {
    modelReturns("barcelona away record 2026");
    await generateSearchQueries({
      userText: "what's their away record?",
      recentTurns: [
        { role: "user", text: "who won Barcelona's last match" },
        { role: "assistant", text: "Barcelona won 2-1 against Sevilla." },
      ],
    });
    const call = generateText.mock.calls[0][0] as Record<string, unknown>;
    expect(call.prompt).toContain("User (earlier): who won Barcelona's last match");
    expect(call.prompt).toContain("Assistant (earlier): Barcelona won 2-1");
    expect(call.prompt).toContain("Latest user message: what's their away record?");
  });

  it("JARVIS_SEARCH_QUERYGEN_MODEL overrides the task model", async () => {
    vi.stubEnv("JARVIS_SEARCH_QUERYGEN_MODEL", "claude-haiku-4-5");
    modelReturns("some query");
    await generateSearchQueries({ userText: "who is Michelle Jackson" });
    expect(getModel).toHaveBeenCalledWith("claude-haiku-4-5");
  });

  it('model says "NONE" → raw-text fallback (query-gen cannot veto a gated-in turn)', async () => {
    modelReturns("NONE");
    expect(
      await generateSearchQueries({ userText: "who is Michelle Jackson" }),
    ).toEqual(["who is Michelle Jackson"]);
    expect(generateText).toHaveBeenCalledTimes(1);
  });

  it("model returns empty output → raw-text fallback", async () => {
    modelReturns("");
    expect(
      await generateSearchQueries({ userText: "who is Michelle Jackson" }),
    ).toEqual(["who is Michelle Jackson"]);
  });

  it("model returns only prose/apology (all lines dropped) → raw-text fallback", async () => {
    modelReturns("I'm sorry, I can't search the web for you.");
    expect(
      await generateSearchQueries({ userText: "who is Michelle Jackson" }),
    ).toEqual(["who is Michelle Jackson"]);
  });

  it("model throws → raw-text fallback", async () => {
    generateText.mockRejectedValue(new Error("provider 500"));
    expect(
      await generateSearchQueries({ userText: "who is Michelle Jackson" }),
    ).toEqual(["who is Michelle Jackson"]);
  });

  it("getModel throws (missing task-model key) → raw-text fallback", async () => {
    getModel.mockRejectedValue(new Error("No API key configured"));
    expect(
      await generateSearchQueries({ userText: "who is Michelle Jackson" }),
    ).toEqual(["who is Michelle Jackson"]);
    expect(generateText).not.toHaveBeenCalled();
  });

  it("query-gen hang → times out into the raw-text fallback", async () => {
    generateText.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ text: "late" }), 5_000)),
    );
    expect(
      await generateSearchQueries({
        userText: "who is Michelle Jackson",
        timeoutMs: 50,
      }),
    ).toEqual(["who is Michelle Jackson"]);
  });

  it("kill-switch JARVIS_SEARCH_QUERYGEN=0 → raw text, no LLM (= pre-refinement #280)", async () => {
    vi.stubEnv("JARVIS_SEARCH_QUERYGEN", "0");
    expect(
      await generateSearchQueries({ userText: "who is Michelle Jackson" }),
    ).toEqual(["who is Michelle Jackson"]);
    expect(
      await generateSearchQueries({ userText: "write me a haiku" }),
    ).toEqual([]);
    // Under the kill-switch even explicit commands keep the raw-text query
    // — exactly the #280 behavior.
    expect(
      await generateSearchQueries({
        userText: "search for the best mechanical keyboards",
      }),
    ).toEqual(["search for the best mechanical keyboards"]);
    expect(generateText).not.toHaveBeenCalled();
    expect(getModel).not.toHaveBeenCalled();
  });
});

describe("maybeGroundWithSearch (scoped gating: weak caller + regex intent)", () => {
  it("fires for a weak caller + search-y turn — query-gen refines the query", async () => {
    modelReturns("quantum computing news");
    searchBrave.mockResolvedValue(HITS);
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined, // toggle not off
      workspaceId: undefined,
      userText: "what's the latest news on quantum computing",
    });
    expect(searchBrave).toHaveBeenCalledTimes(1);
    expect(searchBrave).toHaveBeenCalledWith(
      "quantum computing news",
      expect.objectContaining({ count: 20 }),
    );
    expect(grounding).toBeTruthy();
    const block = grounding!.block;
    // Results text lands in the block the route appends to the system prompt.
    expect(block).toContain("Web search results");
    expect(block).toContain("Alpha");
    expect(block).toContain("https://a.example/one");
    expect(block).toContain("alpha snippet");
    expect(block).toContain("beta extra"); // extraSnippets fallback
    expect(block).toContain('Query: "quantum computing news"');
    // Anti-"search is down" instruction is present.
    expect(block).toMatch(/never claim it is down/i);
    // The raw hits ride along for the synthetic tool-webSearch part, in the
    // client's Sources shape ({title, url, snippet} — snippet falls back to
    // extraSnippets[0], same as the prompt block).
    expect(grounding!.query).toBe("quantum computing news");
    expect(grounding!.results).toEqual([
      { title: "Alpha", url: "https://a.example/one", snippet: "alpha snippet" },
      { title: "Beta", url: "https://b.example/two", snippet: "beta extra" },
      { title: "Gamma", url: "https://c.example/three", snippet: "gamma snippet" },
    ]);
  });

  it("does NOT fire for STRONG callers — null, ZERO LLM cost, tool path preserved", async () => {
    for (const modelId of [
      "claude-fable-5",
      "claude-sonnet-4-6",
      "gpt-5",
      "o3",
      "gemini-2.5-pro",
    ]) {
      const grounding = await maybeGroundWithSearch({
        modelId,
        search: undefined,
        workspaceId: undefined,
        userText: "what's the latest news on quantum computing",
      });
      expect(grounding).toBeNull();
      // Route contract: null grounding → the webSearch tool is offered, so
      // strong callers keep their iterative on-demand tool search.
      expect(shouldOfferWebSearchTool(undefined, grounding)).toBe(true);
    }
    expect(generateText).not.toHaveBeenCalled();
    expect(getModel).not.toHaveBeenCalled();
    expect(searchBrave).not.toHaveBeenCalled();
  });

  it("does NOT fire on regex-negative turns — no query-gen LLM call", async () => {
    for (const text of ["explain recursion", "thanks", "write me a haiku"]) {
      expect(
        await maybeGroundWithSearch({
          modelId: WEAK,
          search: undefined,
          workspaceId: undefined,
          userText: text,
        }),
      ).toBeNull();
    }
    expect(generateText).not.toHaveBeenCalled();
    expect(getModel).not.toHaveBeenCalled();
    expect(searchBrave).not.toHaveBeenCalled();
  });

  it("caps the returned results at 5 (same hits as the prompt block)", async () => {
    modelReturns("michelle jackson");
    searchBrave.mockResolvedValue(
      Array.from({ length: 20 }, (_, i) => ({
        title: `T${i}`,
        url: `https://x.example/${i}`,
        snippet: `s${i}`,
      })),
    );
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "who is Michelle Jackson",
    });
    expect(grounding!.results).toHaveLength(15);
    expect(grounding!.results[14].title).toBe("T14");
  });

  it("fires when the Search toggle is explicitly ON (composer default)", async () => {
    // The composer's Search toggle now defaults ON, so the wire value for an
    // untouched toggle is a literal `true` — must gate the same as undefined.
    modelReturns("quantum computing news");
    searchBrave.mockResolvedValue(HITS);
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: true,
      workspaceId: undefined,
      userText: "what's the latest news on quantum computing",
    });
    expect(grounding).toBeTruthy();
    expect(searchBrave).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire when the Search toggle is off (no query-gen either)", async () => {
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: false,
      workspaceId: undefined,
      userText: "what's the latest news on quantum computing",
    });
    expect(grounding).toBeNull();
    expect(searchBrave).not.toHaveBeenCalled();
    expect(generateText).not.toHaveBeenCalled(); // no wasted LLM call
  });

  it("does NOT fire on workspace turns (no query-gen either)", async () => {
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: "ws-1",
      userText: "what's the latest news on quantum computing",
    });
    expect(grounding).toBeNull();
    expect(searchBrave).not.toHaveBeenCalled();
    expect(generateText).not.toHaveBeenCalled();
  });

  it("merges the top-2 queries deduped by url when the first is thin", async () => {
    modelReturns("q-one\nq-two\nq-three");
    searchBrave
      .mockResolvedValueOnce([
        { title: "A", url: "https://a.example", snippet: "a" },
        { title: "B", url: "https://b.example", snippet: "b" },
      ])
      .mockResolvedValueOnce([
        { title: "B again", url: "https://b.example", snippet: "dup" },
        { title: "C", url: "https://c.example", snippet: "c" },
        { title: "D", url: "https://d.example", snippet: "d" },
      ]);
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "who won Barcelona's last match",
    });
    // ≤2 Brave calls, ever — q-three is never searched.
    expect(searchBrave).toHaveBeenCalledTimes(2);
    expect(searchBrave).toHaveBeenNthCalledWith(1, "q-one", expect.anything());
    expect(searchBrave).toHaveBeenNthCalledWith(2, "q-two", expect.anything());
    // Deduped by url: A, B, C, D (the dup B dropped).
    expect(grounding!.results.map((r) => r.url)).toEqual([
      "https://a.example",
      "https://b.example",
      "https://c.example",
      "https://d.example",
    ]);
    // Both queries contributed → joined display query.
    expect(grounding!.query).toBe("q-one · q-two");
  });

  it("skips the 2nd Brave call when the first already filled the cap", async () => {
    modelReturns("q-one\nq-two");
    searchBrave.mockResolvedValue(
      Array.from({ length: 20 }, (_, i) => ({
        title: `T${i}`,
        url: `https://x.example/${i}`,
        snippet: `s${i}`,
      })),
    );
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "who won Barcelona's last match",
    });
    expect(searchBrave).toHaveBeenCalledTimes(1);
    expect(grounding!.results).toHaveLength(15);
    expect(grounding!.query).toBe("q-one");
  });

  it("falls through silently (null) when Brave errors", async () => {
    modelReturns("btc price");
    searchBrave.mockRejectedValue(new Error("Brave HTTP 500"));
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "current price of BTC",
    });
    expect(grounding).toBeNull();
  });

  it("falls through silently (null) on empty results", async () => {
    modelReturns("btc price");
    searchBrave.mockResolvedValue([]);
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "current price of BTC",
    });
    expect(grounding).toBeNull();
  });

  it("falls through silently (null) when the search exceeds the timeout", async () => {
    modelReturns("btc price");
    searchBrave.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(HITS), 5_000)),
    );
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "current price of BTC",
      timeoutMs: 50,
    });
    expect(grounding).toBeNull();
  });

  it("query-gen failure inside maybeGround still grounds via the raw text — search + chips survive", async () => {
    generateText.mockRejectedValue(new Error("task model down"));
    searchBrave.mockResolvedValue(HITS);
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "who is Michelle Jackson",
    });
    expect(grounding).toBeTruthy();
    expect(searchBrave).toHaveBeenCalledWith(
      "who is Michelle Jackson",
      expect.anything(),
    );
    // Chips still populated off the same hits.
    expect(grounding!.results).toHaveLength(HITS.length);
  });

  it("kill-switch JARVIS_SEARCH_QUERYGEN=0 → pre-refactor (#280) behavior exactly", async () => {
    vi.stubEnv("JARVIS_SEARCH_QUERYGEN", "0");
    searchBrave.mockResolvedValue(HITS);
    // Weak caller + regex intent → ONE Brave call with the RAW user text
    // (whitespace collapsed, same normalization as #280).
    const grounding = await maybeGroundWithSearch({
      modelId: WEAK,
      search: undefined,
      workspaceId: undefined,
      userText: "what's   the latest news on quantum computing",
    });
    expect(grounding).toBeTruthy();
    expect(grounding!.query).toBe("what's the latest news on quantum computing");
    expect(searchBrave).toHaveBeenCalledTimes(1);
    expect(searchBrave).toHaveBeenCalledWith(
      "what's the latest news on quantum computing",
      expect.objectContaining({ count: 20 }),
    );
    // Strong callers and regex-negative turns stay null, same as #280.
    expect(
      await maybeGroundWithSearch({
        modelId: "claude-fable-5",
        search: undefined,
        workspaceId: undefined,
        userText: "what's the latest news on quantum computing",
      }),
    ).toBeNull();
    expect(
      await maybeGroundWithSearch({
        modelId: WEAK,
        search: undefined,
        workspaceId: undefined,
        userText: "write me a haiku",
      }),
    ).toBeNull();
    // And the query-gen LLM was never consulted.
    expect(generateText).not.toHaveBeenCalled();
    expect(getModel).not.toHaveBeenCalled();
  });
});

describe("shouldOfferWebSearchTool (double-search guard)", () => {
  const grounding: SearchGrounding = {
    block: "(block)",
    query: "q",
    results: [],
  };

  it("drops the tool on grounded turns", () => {
    expect(shouldOfferWebSearchTool(undefined, grounding)).toBe(false);
    expect(shouldOfferWebSearchTool(true, grounding)).toBe(false);
  });

  it("keeps the tool on non-grounded turns — every strong-caller turn is one", () => {
    expect(shouldOfferWebSearchTool(undefined, null)).toBe(true);
    expect(shouldOfferWebSearchTool(true, null)).toBe(true);
  });

  it("Search toggle off → no tool regardless", () => {
    expect(shouldOfferWebSearchTool(false, null)).toBe(false);
    expect(shouldOfferWebSearchTool(false, grounding)).toBe(false);
  });
});

describe("formatSearchGrounding", () => {
  it("caps at 15 hits and numbers them", () => {
    const many = Array.from({ length: 20 }, (_, i) => ({
      title: `T${i}`,
      url: `https://x.example/${i}`,
      snippet: `s${i}`,
    }));
    const block = formatSearchGrounding("q", many);
    expect(block).toContain("15. T14");
    expect(block).not.toContain("T15");
  });
});

describe("toWebSearchToolPart ↔ extractSources round-trip", () => {
  it("builds the exact part shape the Sources chips read", () => {
    const grounding = {
      block: "(ignored)",
      query: "who is Michelle Jackson",
      results: [
        { title: "Alpha", url: "https://a.example/one", snippet: "alpha snippet" },
        { title: "Beta", url: "https://b.example/two", snippet: "beta extra" },
      ],
    };
    const part = toWebSearchToolPart(grounding);
    // The part IS a tool-webSearch output — same wire/persisted shape as a
    // real webSearch tool call.
    expect(part.type).toBe("tool-webSearch");
    expect(part.state).toBe("output-available");
    expect(part.toolCallId).toBe(SERVER_SEARCH_TOOL_CALL_ID);
    // And extractSources (the ONLY consumer contract that matters for the
    // chips) reads the results straight off it — this is the reload path:
    // saveAssistantMessage persists [part, {text}], toUIMessages returns
    // content verbatim, message.tsx runs extractSources over the parts.
    const message = {
      id: "m1",
      role: "assistant",
      parts: [part, { type: "text", text: "grounded answer" }],
    } as unknown as UIMessage;
    expect(extractSources(message)).toEqual([
      { title: "Alpha", url: "https://a.example/one", snippet: "alpha snippet" },
      { title: "Beta", url: "https://b.example/two", snippet: "beta extra" },
    ]);
  });

  it("de-dupes by url like the live client does", () => {
    const part = toWebSearchToolPart({
      block: "",
      query: "q",
      results: [
        { title: "A", url: "https://a.example", snippet: "" },
        { title: "A again", url: "https://a.example", snippet: "" },
      ],
    });
    const message = {
      id: "m1",
      role: "assistant",
      parts: [part],
    } as unknown as UIMessage;
    expect(extractSources(message)).toHaveLength(1);
  });
});
