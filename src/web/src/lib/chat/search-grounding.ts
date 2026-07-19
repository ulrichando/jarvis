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
// info (the cheap `hasSearchIntent` regex — the GATE), we run a bounded Brave
// search server-side and inject the results into the system prompt so the
// answer is grounded regardless of the model's tool-calling ability.
//
// NEW in the 2026-07 rework — query REFINEMENT, scoped: once the regex has
// gated a weak-caller turn IN, one small task-model call (default
// `deepseek-v4-flash`, env override JARVIS_SEARCH_QUERYGEN_MODEL, hard
// ~3.5s cap, tiny output) rewrites the raw message into 1–3 keyword-style
// queries — "who won Barcelona's last match" searches better as "barcelona
// last match result" than as the verbatim sentence. Refinement is strictly
// best-effort: on error / timeout / NONE / unusable output it falls back to
// the raw user text and the search runs anyway. Query-gen can only improve
// the query, never veto a search the regex approved, and it NEVER runs for
// regex-negative turns, strong callers, workspace turns, or toggle-off — so
// non-search turns and Claude/GPT/Gemini pay zero added latency.
//
// Strong tool-callers (Anthropic / OpenAI / Google) are deliberately never
// grounded here — they call the tool fine on their own (iteratively, which
// is strictly stronger), and double-searching would waste a Brave call
// (free tier: ~2000 queries/month) plus latency.
//
// KILL-SWITCH — JARVIS_SEARCH_QUERYGEN=0 skips the query-gen LLM and uses
// the raw user text as the query; with the weak-caller + regex gates above
// that equals the pre-refinement (#280) behavior exactly.
//
// Failure policy: any error / timeout / empty result set returns null and
// the turn proceeds exactly as before (the webSearch tool is still offered
// to the model). When grounding DID fire, the route drops the webSearch
// tool for that turn so the model doesn't double-search.

import { generateText } from "ai";
import { searchBrave } from "@/lib/search/pipeline";
import { getModel } from "@/lib/ai/models";
import { MODELS_META } from "@/lib/ai/models-meta";

/** Structural subset of RichHit this module needs (same trick as web-search.ts). */
export type GroundingHit = {
  title: string;
  url: string;
  snippet: string;
  extraSnippets?: string[];
};

/** Overall budget for the server-side Brave search (hard cap; falls through on expiry). */
export const SEARCH_GROUNDING_TIMEOUT_MS = 5000;

/** Hard cap on the query-generation LLM call. */
export const QUERYGEN_TIMEOUT_MS = 3500;

/** Cheap task model for query generation (env override below). */
const QUERYGEN_DEFAULT_MODEL = "deepseek-v4-flash";
const QUERYGEN_MAX_OUTPUT_TOKENS = 64;

/** Hits actually injected into the prompt (Brave is asked for a few more). */
// 15 to match the mobile app (jarvis-android WebSearchClient keeps hits.take(15)).
const MAX_INJECTED_HITS = 15;
const MAX_SNIPPET_CHARS = 500;
const MAX_QUERY_CHARS = 300;

/** Query-gen may propose up to 3 queries; at most 2 become Brave calls. */
const MAX_QUERIES = 3;
const MAX_BRAVE_CALLS = 2;
/** Don't start a 2nd Brave call with less than this left of the budget
 * (searchBrave's internal rate-limit slot spacing alone is ~1.05s). */
const MIN_SECOND_CALL_BUDGET_MS = 1500;

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

// Regex tiers — hasSearchIntent is THE intent gate (weak callers only; see
// maybeGroundWithSearch). EXPLICIT_SEARCH_RE additionally short-circuits
// query-gen for explicit commands ("search for X" needs no LLM to become
// the query "X"), and NON_SEARCH_PATTERNS keeps "what is …" / "research …"
// generous without firing on code work, creative writing, or
// conversation-local requests.

/** Explicit search commands — bypass query-gen AND the exclusions. */
const EXPLICIT_SEARCH_RE =
  /\b(?:search\s+(?:the\s+web\s+)?for|search\s+the\s+web|web\s+search|look\s+up|google)\b/i;

/** When the message STARTS with an explicit command, strip the command so
 * the query is just the payload ("search for X" → "X"). Mid-sentence
 * commands keep the whole text. */
const EXPLICIT_PREFIX_RE =
  /^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+|would\s+you\s+)?(?:please\s+)?(?:search\s+(?:the\s+web\s+)?for|search\s+the\s+web|web\s+search(?:\s+for)?|look\s+up|google)\s+/i;

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
 * THE intent gate: true when the user's latest message reads as an
 * information lookup that live search results would improve. Only ever
 * consulted for weak tool-callers (see maybeGroundWithSearch), so it errs
 * toward firing — a false positive costs one cheap Brave call (+ one tiny
 * query-gen call); a false negative makes the model tell the user search
 * is broken.
 */
export function hasSearchIntent(text: string): boolean {
  if (!text) return false;
  if (EXPLICIT_SEARCH_RE.test(text)) return true;
  if (NON_SEARCH_PATTERNS.some((re) => re.test(text))) return false;
  return SEARCH_INTENT_PATTERNS.some((re) => re.test(text));
}

/** Collapse whitespace + cap length — applied to every outgoing query. */
function normalizeQuery(text: string): string {
  return text.replace(/\s+/g, " ").trim().slice(0, MAX_QUERY_CHARS);
}

/** A prior conversation turn handed to query-gen for context. */
export type QueryGenTurn = { role: "user" | "assistant"; text: string };

const QUERYGEN_SYSTEM = `You rewrite a user's chat message into web-search queries for another AI assistant.

Output 1 to 3 concise, keyword-style search queries, one per line. No numbering, no quotes, no preamble, no commentary. Use the earlier turns only to resolve pronouns and vague references ("their away record" → the team's name). If you truly cannot form a useful query, output exactly NONE. Today's date is {date}.`;

function buildQueryGenPrompt(
  userText: string,
  recentTurns?: readonly QueryGenTurn[],
): string {
  const parts: string[] = [];
  for (const t of (recentTurns ?? []).slice(-2)) {
    const text = t.text.replace(/\s+/g, " ").trim().slice(0, 400);
    if (text) {
      parts.push(`${t.role === "user" ? "User" : "Assistant"} (earlier): ${text}`);
    }
  }
  parts.push(
    `Latest user message: ${userText.replace(/\s+/g, " ").trim().slice(0, 2000)}`,
  );
  return parts.join("\n");
}

/** Hardening caps for a single parsed query: a real keyword query is short;
 * anything longer is prose that leaked through — truncate, don't trust. */
const QUERYGEN_MAX_QUERY_WORDS = 12;
const QUERYGEN_MAX_QUERY_CHARS = 120;

/** Preamble / apology / meta lines a chatty task model emits around (or
 * instead of) its queries — "Sure, here are the queries:", "I'm sorry, I
 * can't…", "No search needed." — never valid Brave queries. */
const QUERYGEN_META_LINE_RE =
  /\b(?:sorry|here (?:are|is)|queries|i can'?t|cannot|no search)\b/i;

/**
 * Parse the task model's raw output into queries. Exported for direct
 * testing. One query per line with list markers / wrapping quotes / code
 * fences stripped; "NONE" verdicts, preamble/apology/meta lines, and
 * lead-in lines ending in ":" are DROPPED; surviving lines are truncated
 * to a keyword-sized query, deduped, capped at MAX_QUERIES.
 *
 * Returns [] when nothing usable survives — the caller
 * (generateSearchQueries) then falls back to the raw user text, so a
 * misbehaving task model can never veto a search the regex gated in, and
 * its prose can never reach Brave as a query.
 */
export function parseQueryGenOutput(text: string): string[] {
  const cleaned = (text ?? "")
    .replace(/^\s*```[a-z]*\s*\n?/i, "")
    .replace(/\n?\s*```\s*$/i, "")
    .trim();
  if (!cleaned) return [];
  const seen = new Set<string>();
  const queries: string[] = [];
  for (const raw of cleaned.split(/\r?\n/)) {
    const line = raw
      .replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "")
      .replace(/^["'`]+|["'`]+$/g, "")
      .trim();
    if (line.length <= 1) continue;
    if (/^none[.!]?$/i.test(line)) continue;
    // Prose guards: a line ending in ":" is a lead-in, not a query; the
    // meta regex catches preambles, apologies, and refusals.
    if (line.endsWith(":")) continue;
    if (QUERYGEN_META_LINE_RE.test(line)) continue;
    // Truncate runaway prose to a keyword-sized query instead of trusting it.
    const q = normalizeQuery(line)
      .split(" ")
      .slice(0, QUERYGEN_MAX_QUERY_WORDS)
      .join(" ")
      .slice(0, QUERYGEN_MAX_QUERY_CHARS)
      .trim();
    const key = q.toLowerCase();
    if (!q || seen.has(key)) continue;
    seen.add(key);
    queries.push(q);
    if (queries.length >= MAX_QUERIES) break;
  }
  return queries;
}

/** The query-gen LLM call under a hard timeout; null means "timed out". */
async function queryGenWithTimeout(
  userText: string,
  recentTurns: readonly QueryGenTurn[] | undefined,
  timeoutMs: number,
): Promise<string[] | null> {
  const modelId =
    (process.env.JARVIS_SEARCH_QUERYGEN_MODEL || "").trim() ||
    QUERYGEN_DEFAULT_MODEL;
  // getModel may throw (e.g. MissingApiKeyError when the task model's
  // provider has no key) — the caller's catch routes that to the regex
  // fallback, so search survives without the task model.
  const { model } = await getModel(modelId);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), timeoutMs);
  });
  try {
    const call = generateText({
      model,
      system: QUERYGEN_SYSTEM.replace(
        "{date}",
        new Date().toISOString().slice(0, 10),
      ),
      prompt: buildQueryGenPrompt(userText, recentTurns),
      maxOutputTokens: QUERYGEN_MAX_OUTPUT_TOKENS,
      temperature: 0,
      // Abort the underlying request too, not just the race — otherwise a
      // hung provider connection lingers past the turn.
      abortSignal: AbortSignal.timeout(timeoutMs),
    }).then((r) => parseQueryGenOutput(r.text));
    // If the race expires first, a later rejection must not surface as an
    // unhandled promise rejection.
    call.catch(() => {});
    return await Promise.race([call, expired]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

/**
 * Query REFINEMENT for a turn the caller has already decided to search
 * (weak tool-caller + hasSearchIntent — see maybeGroundWithSearch). Returns
 * 1–3 Brave queries; the raw user text is the guaranteed fallback, so a
 * gated-in turn ALWAYS gets at least one query — the task model can only
 * improve the query, never veto the search.
 *
 * Order of the short-circuits (regex-negative text returns [] up front and
 * NEVER reaches the LLM — the caller gates on this too; kept here so a
 * direct caller can't burn a query-gen call on "thanks"):
 *   1. kill-switch (JARVIS_SEARCH_QUERYGEN=0) → [raw text], no LLM — with
 *      the caller's weak-caller + regex gates this is byte-for-byte the
 *      pre-refinement (#280) behavior;
 *   2. explicit commands ("search for X") → [X], no LLM;
 *   3. the task-model call — on error / timeout / NONE / empty /
 *      all-lines-dropped → [raw text]. Never [] for a gated-in turn.
 */
export async function generateSearchQueries(opts: {
  /** Text of the LATEST user message only. */
  userText: string;
  /** Up to the last 2 prior turns, for pronoun/topic context. */
  recentTurns?: readonly QueryGenTurn[];
  timeoutMs?: number;
}): Promise<string[]> {
  const text = (opts.userText ?? "").trim();
  if (!text) return [];
  if (!hasSearchIntent(text)) return [];

  if (process.env.JARVIS_SEARCH_QUERYGEN === "0") {
    return [normalizeQuery(text)];
  }

  if (EXPLICIT_SEARCH_RE.test(text)) {
    const stripped = normalizeQuery(text.replace(EXPLICIT_PREFIX_RE, ""));
    return [stripped || normalizeQuery(text)];
  }

  try {
    const queries = await queryGenWithTimeout(
      text,
      opts.recentTurns,
      opts.timeoutMs ?? QUERYGEN_TIMEOUT_MS,
    );
    if (queries !== null && queries.length > 0) return queries;
    if (queries === null) {
      console.warn("[chat] search query-gen timed out; using raw text");
    }
  } catch (err) {
    console.warn(
      "[chat] search query-gen failed; using raw text:",
      err instanceof Error ? err.message : err,
    );
  }
  // FALLBACK: refinement is best-effort — the regex already decided this
  // turn searches, so any query-gen failure (or unusable/NONE output)
  // searches with the raw user text as the single query.
  return [normalizeQuery(text)];
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
      count: 20, // Brave max — fetch enough candidates to keep up to 15 after dedupe.
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
  /** The query(ies) actually sent to Brave — joined with " · " when the
   *  merged top-2 path ran. */
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
 * Whether the route should hand the model the `webSearch` tool this turn.
 * Grounded turns (weak caller, server-side search ran) DROP it — the
 * results are already in the system prompt, so calling the tool again would
 * double-search (wasted Brave quota + a slower answer). Non-grounded turns
 * keep it: strong callers ALWAYS ground to null (isWeakToolCaller gate), so
 * they always get the tool and their iterative search path is untouched;
 * weak callers keep it too when the regex didn't fire or the search fell
 * through.
 */
export function shouldOfferWebSearchTool(
  search: boolean | undefined,
  grounding: SearchGrounding | null,
): boolean {
  return search !== false && !grounding;
}

/**
 * The whole gate + refinement + search + format in one call for the chat
 * route. Gate order (each is free — no LLM, no network):
 *
 *   1. Search toggle off → null;
 *   2. workspace turn → null;
 *   3. STRONG tool-caller → null — Claude / GPT / Gemini keep their
 *      iterative webSearch tool path, unchanged, zero added latency;
 *   4. regex says the message isn't search-y → null — non-search turns
 *      never pay the query-gen LLM call.
 *
 * Only a weak caller + a regex-positive message reaches query-gen, which
 * REFINES the query (best-effort, raw-text fallback inside
 * generateSearchQueries) and then runs the bounded Brave pass. Returns the
 * grounding (system-prompt block + the raw hits for the synthetic sources
 * part), or null when any gate fails or the search errors/times out/returns
 * nothing (→ today's behavior: the webSearch tool is still offered).
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
  /** Up to the last 2 prior turns, handed to query-gen for context. */
  recentTurns?: readonly QueryGenTurn[];
  /** Brave budget override (tests). */
  timeoutMs?: number;
  /** Query-gen budget override (tests). */
  queryGenTimeoutMs?: number;
}): Promise<SearchGrounding | null> {
  const { modelId, search, workspaceId, userText } = opts;
  if (search === false) return null;
  if (workspaceId) return null;
  if (!isWeakToolCaller(modelId)) return null;
  if (!userText || !userText.trim()) return null;
  if (!hasSearchIntent(userText)) return null;

  // Gated in: this turn WILL search. Query-gen only refines the query —
  // generateSearchQueries falls back to the raw user text on any failure,
  // so the empty check below is pure defense.
  const queries = await generateSearchQueries({
    userText,
    recentTurns: opts.recentTurns,
    timeoutMs: opts.queryGenTimeoutMs,
  });
  if (queries.length === 0) return null;

  // Bounded Brave execution: the top query always runs; the 2nd query runs
  // ONLY when the first came back thin (< MAX_INJECTED_HITS unique hits)
  // and enough of the overall budget remains. Results merge deduped by URL.
  const deadline = Date.now() + (opts.timeoutMs ?? SEARCH_GROUNDING_TIMEOUT_MS);
  const merged: GroundingHit[] = [];
  const seenUrls = new Set<string>();
  const usedQueries: string[] = [];
  let attempted = 0;
  for (const q of queries.slice(0, MAX_BRAVE_CALLS)) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    if (
      attempted > 0 &&
      (merged.length >= MAX_INJECTED_HITS || remaining < MIN_SECOND_CALL_BUDGET_MS)
    ) {
      break;
    }
    attempted += 1;
    const hits = await searchWithTimeout(q, remaining);
    if (!hits || hits.length === 0) continue;
    const before = merged.length;
    for (const h of hits) {
      if (!h?.url || seenUrls.has(h.url)) continue;
      seenUrls.add(h.url);
      merged.push(h);
    }
    if (merged.length > before) usedQueries.push(q);
  }
  if (merged.length === 0) return null;

  const query = usedQueries.join(" · ") || queries[0];
  const top = merged.slice(0, MAX_INJECTED_HITS);
  console.log(`[chat] server-side search: ${top.length} hits for "${query}"`);
  return {
    block: formatSearchGrounding(query, merged),
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
