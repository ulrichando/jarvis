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

// Search-intent heuristic — deliberately TIGHT. A false positive costs a
// Brave call (quota + ~1-2s latency) and injects an irrelevant grounding
// block; a false negative just falls back to today's behavior (the model
// still has the webSearch tool). So every pattern requires either an
// explicit search command or a live-info keyword in a disambiguating
// context (bare "current"/"today"/"score" alone do NOT fire — they're
// everywhere in coding chats).
const SEARCH_INTENT_PATTERNS: RegExp[] = [
  // Explicit search commands.
  /\b(?:search\s+(?:the\s+web\s+)?for|search\s+the\s+web|look\s+up|google)\b/i,
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
];

/**
 * True when the user's latest message clearly needs current / real-time
 * information. Mirrors hasImageIntent's role for the forced-image path.
 */
export function hasSearchIntent(text: string): boolean {
  if (!text) return false;
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

/**
 * The whole gate + search + format in one call for the chat route. Returns
 * the grounding block to append to the system prompt, or null when any gate
 * fails or the search errors/times out/returns nothing (→ today's behavior).
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
}): Promise<string | null> {
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
  const used = Math.min(hits.length, MAX_INJECTED_HITS);
  console.log(`[chat] server-side search: ${used} hits for "${query}"`);
  return formatSearchGrounding(query, hits);
}
