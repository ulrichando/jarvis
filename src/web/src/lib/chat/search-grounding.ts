// Deterministic SERVER-SIDE web search for weak-tool-calling chat models.
//
// The chat route hands every plain-chat turn a `webSearch` tool, but the
// DeepSeek family (and thinking/reasoner models generally) can't be forced to
// call it — tool_choice is rejected in thinking mode ("Thinking mode does not
// support this tool_choice", 400) and in long conversations they unreliably
// answer with TEXT from training data, often claiming "search is down". This
// module mirrors the route's forced-image pattern: when (a) the Search toggle
// isn't off, (b) it's a plain chat (no workspace), (c) the selected model is a
// KNOWN weak tool-caller, and (d) the latest user message clearly needs live
// info, we run ONE bounded Brave search server-side and inject the results
// into the system prompt so the answer is grounded regardless of the model's
// tool-calling ability.
//
// Strong tool-callers (Anthropic / OpenAI / Google) are deliberately never
// grounded here — they call the tool fine on their own, and double-searching
// would waste a Brave call (free tier: ~2000 queries/month) plus latency.
//
// Failure policy: any error / timeout / empty result set returns null and the
// turn proceeds exactly as before (the tool is still offered to the model).

import { searchBrave } from "@/lib/search/pipeline";
import { MODELS_META } from "@/lib/ai/models-meta";

/** Structural subset of RichHit this module needs (same trick as web-search.ts). */
export type GroundingHit = {
  title: string;
  url: string;
  snippet: string;
  extraSnippets?: string[];
};

/** Overall budget for the server-side search (hard cap; falls through on expiry). */
export const SEARCH_GROUNDING_TIMEOUT_MS = 5000;

/** Hits actually injected into the prompt (Brave is asked for a few more). */
const MAX_INJECTED_HITS = 5;
const MAX_SNIPPET_CHARS = 500;
const MAX_QUERY_CHARS = 300;

/**
 * True when the model is a KNOWN weak tool-caller that benefits from
 * deterministic server-side search:
 *
 * - the whole DeepSeek family (deepseek-chat / deepseek-v4-flash /
 *   deepseek-reasoner / deepseek-v4-pro) — even the non-reasoning siblings
 *   drift into answering with text instead of tool calls in long chats;
 * - any reasoning/thinking model on a non-strong provider (kimi-k2-thinking,
 *   future reasoners) — thinking mode rejects tool_choice, so the tool can
 *   never be forced.
 *
 * Anthropic / OpenAI / Google models are NEVER weak here (o3 / GPT-5 /
 * Claude reason AND tool-call reliably); unknown ids default to false so we
 * never burn a Brave call on a model we can't classify.
 */
export function isWeakToolCaller(modelId: string): boolean {
  const meta = MODELS_META[modelId];
  const provider = meta?.provider;
  if (provider === "anthropic" || provider === "openai" || provider === "google") {
    return false;
  }
  if (provider === "deepseek") return true;
  if (meta?.reasoning === true) return true;
  // Robustness for ids that bypass MODELS_META (env overrides, future ids):
  // the DeepSeek family is recognizable by name.
  if (/^deepseek([-/]|$)/i.test(modelId)) return true;
  return false;
}

// Search-intent heuristic — three tiers (2026-07-18 rework). The original
// single TIGHT tier missed obvious lookups ("who is Michelle Jackson",
// "research tokenization and organization"), so the grounding never fired
// and DeepSeek kept claiming "search is down" from training data.
//
// This function is only consulted AFTER the isWeakToolCaller gate, so the
// cost asymmetry rules the design: a false positive costs one cheap Brave
// call + a harmless grounding block; a false NEGATIVE makes the model tell
// the user search is broken. Therefore: ERR TOWARD FIRING.
//
//   1. EXPLICIT_SEARCH_RE — explicit commands ("search for", "look up",
//      "google") always fire, even if the rest of the message looks code-ish.
//   2. NON_SEARCH_PATTERNS — hard-exclude the clearly-not-search shapes:
//      code/engineering tasks, creative writing, pure arithmetic,
//      greetings/thanks, requests about the current conversation or the
//      user's own material.
//   3. SEARCH_INTENT_PATTERNS — the original tight live-info tier
//      (news/prices/weather/sports/releases) PLUS general informational
//      lookups (who/what/when/where questions, "tell me about",
//      "research …", "give me info on", "how does X work", …).

/** Explicit search commands — highest priority, bypass the exclusions. */
const EXPLICIT_SEARCH_RE =
  /\b(?:search\s+(?:the\s+web\s+)?for|search\s+the\s+web|web\s+search|look\s+up|google)\b/i;

/** Clearly-NOT-search shapes. Checked before the intent tier (but after the
 * explicit commands) so "what is …" / "research …" can stay generous
 * without firing on code work, creative writing, or conversation-local
 * requests. */
const NON_SEARCH_PATTERNS: RegExp[] = [
  // Greetings / thanks / acknowledgements — whole-message only.
  /^\s*(?:hi|hiya|hello|hey|yo|sup|thanks?|thank\s+you|thx|ty|ok(?:ay)?|k|cool|nice|great|awesome|perfect|got\s+it|sounds\s+good|good\s+(?:morning|afternoon|evening|night)|bye|goodbye|see\s+ya|later|lol|haha)\b[\s!.?,:;~-]*$/i,
  // Code / engineering tasks.
  /\b(?:refactor|debug|deobfuscate|transpile|minify|typecheck|lint(?:er|ing)?|stack\s*trace|segfault|traceback)\b/i,
  /\bfix\s+(?:this|the|that|my|our)\b/i,
  /\b(?:write|create|implement|generate|add|build)\s+(?:me\s+)?(?:a|an|some|the)?\s*(?:function|method|class|component|module|script|program|unit\s+test|test|tests|regex|sql|query|endpoint|hook|migration|dockerfile|makefile|snippet|cli|api)\b/i,
  /\bwrite\s+(?:some|the|more)?\s*code\b/i,
  // Creative writing.
  /\b(?:write|compose|draft)\s+(?:me\s+)?(?:a|an|some|the)?\s*(?:poem|story|stories|essay|song|lyrics|haiku|limerick|joke|novel|screenplay|letter|email|tweet|blog\s+post|speech|caption|slogan)\b/i,
  // About the current conversation / the user's own material.
  /\b(?:the|this)\s+(?:above|previous|last|current)\s+(?:message|code|text|answer|response|output|conversation|chat|question|reply)\b/i,
  /\b(?:the|this)\s+above\b/i,
  /\bsummari[sz]e\s+(?:the|this|our|that|it|everything)\b/i,
  /\b(?:my|our)\s+(?:current\s+)?(?:code|codebase|function|file|files|implementation|project|repo|branch|pr|approach|setup|config|error|bug)\b/i,
  /\b(?:this|that)\s+(?:code|file|function|snippet|error|bug|conversation|chat|message|paragraph|text|doc(?:ument)?)\b/i,
  // Questions about the assistant itself.
  /^\s*who\s+are\s+you\b/i,
  /\bwhat(?:'s|\s+is)\s+your\b/i,
  // Pure arithmetic ("what is 2+2").
  /^\s*(?:what(?:'s|\s+is)\s+)?[-+\d\s*/x^().,%=]+\??\s*$/i,
];

const SEARCH_INTENT_PATTERNS: RegExp[] = [
  // News / current events.
  /\b(?:latest|breaking|recent)\s+(?:news|headlines?|updates?|developments?|version|release)\b/i,
  /\bnews\s+(?:on|about|for|from)\b/i,
  /\b(?:the\s+)?latest\s+on\b/i,
  /\bheadlines?\b/i,
  /\btrending\b/i,
  /\bwhat(?:'s|\s+is)\s+(?:new|happening|going\s+on)\b/i,
  /\bwhat\s+happened\b/i,
  // Prices / markets — "current X" and "price" need a market-ish context.
  /\bcurrent\s+(?:price|value|rate|version|status|weather|time|score|standings|news|events)\b/i,
  /\b(?:price|prices|cost)\s+(?:of|for)\b/i,
  /\bprice\b[^.?!\n]{0,20}\b(?:today|right\s+now|now)\b/i,
  /\b(?:stock|share)\s+price\b/i,
  /\b(?:market\s+cap|exchange\s+rate)\b/i,
  // Weather.
  /\bweather\b/i,
  /\bforecast\s+(?:in|for|at|today|tomorrow|tonight|this\s+week)\b/i,
  // Sports / results.
  /\bwho\s+(?:won|wins|is\s+winning|will\s+win)\b/i,
  /\bfinal\s+score\b/i,
  /\bscore\b[^.?!\n]{0,30}\b(?:game|match)\b|\b(?:game|match)\b[^.?!\n]{0,30}\bscore\b/i,
  // Releases.
  /\brelease\s+date\b/i,
  /\bwhen\s+(?:is|does|did|will)\b[^.?!\n]{0,60}\b(?:release|come\s+out|launch|drop)\b/i,
  // Recency anchors: interrogative + time word ("who won the game today",
  // "what's the BTC price right now") — bare "today" does NOT fire.
  /\b(?:what(?:'s)?|who|where|when|how\s+(?:much|many))\b[^.?!\n]{0,80}\b(?:today|tonight|right\s+now|currently|this\s+(?:week|month|year))\b/i,
  /\bas\s+of\s+(?:today|now|20\d{2})\b/i,
  /\bup[\s-]?to[\s-]?date\b/i,
  // A recent year — questions pinned to 2025+ are about post-cutoff reality.
  /\b202[5-9]\b/,
  // ── General informational lookups (2026-07-18 broadening) ──
  // "who is Michelle Jackson", "who were the Wright brothers"
  /\bwho\s+(?:is|was|are|were)\s+\S+/i,
  // "what is a quasar", "what's the capital of Chad", "what are tariffs"
  /\bwhat(?:'s|\s+is|\s+are|\s+was|\s+were)\s+\S+/i,
  // "tell me (more) about X"
  /\btell\s+me\s+(?:more\s+)?about\b/i,
  // "research X", "do some research on X"
  /\bresearch\b/i,
  // "info/details/facts/background/overview/history/bio on|about X"
  /\b(?:info(?:rmation)?|details?|facts|background|overview|history|bio(?:graphy)?|profile|rundown|breakdown|primer)\s+(?:on|about|of|for|regarding)\b/i,
  // "find out about X", "learn about X", "read (up) about X"
  /\b(?:find\s+out|learn|read(?:\s+up)?)\s+(?:more\s+)?about\b/i,
  // "look into X", "dig into X", "dive into X"
  /\b(?:look|dig|dive)\s+into\b/i,
  // "find (me) info/details/articles/sources/data …"
  /\bfind\s+(?:me\s+)?(?:info(?:rmation)?|details?|sources?|articles?|papers?|studies|data|stats|statistics)\b/i,
  // "give me info on X", "give me the rundown on X"
  /\bgive\s+me\s+(?:a|an|the|some)?\s*(?:info(?:rmation)?|details?|rundown|overview|summary|breakdown|primer|briefing|low[\s-]?down|facts|background)\b/i,
  // "how does photosynthesis work", "how do black holes work"
  /\bhow\s+(?:does|do|did)\s+[^.?!\n]{0,60}\bwork\b/i,
  // "when did the Berlin Wall fall", "when was X founded"
  /\bwhen\s+(?:did|was|were|is|will|does)\s+\S+/i,
  // "where is Mount Kilimanjaro", "where can I buy X"
  /\bwhere\s+(?:is|was|are|were|can\s+(?:i|you|we)\s+(?:find|buy|get|watch|stream))\s+\S+/i,
  // "is X still alive/open/around", "did X really/actually happen"
  /\bis\s+\S[^.?!\n]{0,60}\bstill\b/i,
  /\bdid\s+\S[^.?!\n]{0,60}\b(?:happen|really|actually)\b/i,
];

/**
 * True when the user's latest message reads as an information lookup that
 * live search results would improve. Mirrors hasImageIntent's role for the
 * forced-image path. Only ever consulted for weak tool-callers, so it errs
 * toward firing (see tier comment above).
 */
export function hasSearchIntent(text: string): boolean {
  if (!text) return false;
  if (EXPLICIT_SEARCH_RE.test(text)) return true;
  if (NON_SEARCH_PATTERNS.some((re) => re.test(text))) return false;
  return SEARCH_INTENT_PATTERNS.some((re) => re.test(text));
}

/** Compact, clearly-delimited grounding block appended to the system prompt. */
export function formatSearchGrounding(
  query: string,
  hits: readonly GroundingHit[],
): string {
  const top = hits.slice(0, MAX_INJECTED_HITS);
  const lines = top.map((h, i) => {
    const snippet = (h.snippet || h.extraSnippets?.[0] || "")
      .trim()
      .slice(0, MAX_SNIPPET_CHARS);
    return `${i + 1}. ${h.title}\n   URL: ${h.url}${snippet ? `\n   ${snippet}` : ""}`;
  });
  return `

# Web search results (live — use these; cite the URLs; if they don't answer, say so)

A live web search already ran server-side for the user's latest message. Web search is WORKING — never claim it is down, unavailable, or that you cannot access current information. Ground your answer in the results below, prefer them over your training data for anything current or time-sensitive, and cite the source URLs you rely on. If they don't answer the question, say so plainly.

Query: "${query}"

${lines.join("\n\n")}`;
}

/** One Brave call under a hard overall timeout; null on any failure. */
async function searchWithTimeout(
  query: string,
  timeoutMs: number,
): Promise<GroundingHit[] | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), timeoutMs);
  });
  try {
    const search = searchBrave(query, {
      count: 8,
      // Leave headroom inside the overall budget for the rate-limit slot
      // spacing searchBrave applies internally (~1.05s between calls).
      timeoutMs: Math.max(1000, timeoutMs - 1000),
    });
    // If the race expires first, a later rejection must not surface as an
    // unhandled promise rejection.
    search.catch(() => {});
    return await Promise.race([search, expired]);
  } catch (err) {
    console.warn(
      "[chat] server-side search failed:",
      err instanceof Error ? err.message : err,
    );
    return null;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

/** One source in the client's Sources-chip shape — EXACTLY what
 * extractSources (components/chat/sources.tsx) reads off a tool-webSearch
 * part's `output.results`, and what the real webSearchTool returns. */
export type SearchSourceResult = { title: string; url: string; snippet: string };

export type SearchGrounding = {
  /** Grounding block the route appends to the system prompt. */
  block: string;
  /** The query actually sent to Brave. */
  query: string;
  /** The SAME top hits injected into the prompt, in the same order — so an
   *  inline "[n]" citation in the answer lines up with chip n. */
  results: SearchSourceResult[];
};

/**
 * toolCallId of the SYNTHETIC webSearch tool part the chat route emits for
 * grounded turns. Stable — at most one server-side search runs per turn —
 * and recognizable, though the route strips ALL tool-webSearch parts from
 * model-bound history regardless (see historyForModel in chat/route.ts).
 */
export const SERVER_SEARCH_TOOL_CALL_ID = "server-search-1";

/**
 * The synthetic `tool-webSearch` UI message part for a grounded turn — the
 * exact shape an AI SDK client assembles from the streamed
 * tool-input-available + tool-output-available chunks, and the exact shape
 * extractSources reads (type "tool-webSearch", output.results). Persisted
 * ahead of the text part so Sources chips survive reload.
 */
export function toWebSearchToolPart(grounding: SearchGrounding): {
  type: "tool-webSearch";
  toolCallId: string;
  state: "output-available";
  input: { query: string };
  output: { query: string; results: SearchSourceResult[] };
  providerExecuted: true;
} {
  return {
    type: "tool-webSearch",
    toolCallId: SERVER_SEARCH_TOOL_CALL_ID,
    state: "output-available",
    input: { query: grounding.query },
    output: { query: grounding.query, results: grounding.results },
    providerExecuted: true,
  };
}

/**
 * The whole gate + search + format in one call for the chat route. Returns
 * the grounding (system-prompt block + the raw hits for the synthetic
 * sources part), or null when any gate fails or the search errors/times
 * out/returns nothing (→ today's behavior).
 */
export async function maybeGroundWithSearch(opts: {
  /** Final resolved model id (AFTER any thinking-toggle reasoner swap). */
  modelId: string;
  /** The composer Search toggle — false means OFF (same gate as the tool). */
  search: boolean | undefined;
  /** Workspace turns never search (same gate as the tool). */
  workspaceId: string | undefined;
  /** Text of the LATEST user message only. */
  userText: string;
  timeoutMs?: number;
}): Promise<SearchGrounding | null> {
  const { modelId, search, workspaceId, userText } = opts;
  if (search === false) return null;
  if (workspaceId) return null;
  if (!isWeakToolCaller(modelId)) return null;
  if (!hasSearchIntent(userText)) return null;
  const query = userText.replace(/\s+/g, " ").trim().slice(0, MAX_QUERY_CHARS);
  if (!query) return null;
  const hits = await searchWithTimeout(
    query,
    opts.timeoutMs ?? SEARCH_GROUNDING_TIMEOUT_MS,
  );
  if (!hits || hits.length === 0) return null;
  const top = hits.slice(0, MAX_INJECTED_HITS);
  console.log(`[chat] server-side search: ${top.length} hits for "${query}"`);
  return {
    block: formatSearchGrounding(query, hits),
    query,
    results: top.map((h) => ({
      title: h.title,
      url: h.url,
      snippet: (h.snippet || h.extraSnippets?.[0] || "")
        .trim()
        .slice(0, MAX_SNIPPET_CHARS),
    })),
  };
}
