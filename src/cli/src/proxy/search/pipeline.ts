// Shared web-search pipeline — the I/O half.
//
// Source chain: Brave Search API (primary, when BRAVE_SEARCH_API_KEY is
// set) → SearXNG (when SEARXNG_URL is set) → DuckDuckGo HTML scrape.
// Brave's ranked results fix the measured VPS failure mode (SearXNG
// degraded to bing homepages + mwmbl junk); the keyless chain is exactly
// the pre-Brave behavior so nothing regresses without a key.
//
// Also provides bounded page-content enrichment: fetch the top N result
// pages IN PARALLEL, each under a short timeout and all under one overall
// budget, and extract readable main text — a slow page can never stall
// the turn.
//
// MIRRORED FILE — exists at BOTH
//   src/cli/src/proxy/search/pipeline.ts   (hub proxy, bun runtime)
//   src/web/src/lib/search/pipeline.ts     (Next.js voice-search route, node)
// and must stay byte-identical (src/web/tests/search-sync.test.ts).
//
// Env (all optional; defaults in parens):
//   BRAVE_SEARCH_API_KEY      — enables the Brave primary source
//   SEARXNG_URL               — enables the SearXNG fallback
//   SEARCH_MAX_RESULTS  (8)   — cleaned results returned per search
//   SEARCH_FETCH_PAGES  (3)   — top results to fetch page content for (0=off)
//   SEARCH_FETCH_TIMEOUT_MS (2500) — per-page fetch timeout
//   SEARCH_FETCH_BUDGET_MS  (4000) — overall content-fetch budget
//   SEARCH_CONTENT_MAX_CHARS (4000) — extracted text cap per page
//
// Query rewriting (runResearchPipeline stage 1):
//   SEARCH_QUERY_REWRITE (heuristic) — off | heuristic | llm
//   SEARCH_REWRITE_MAX_QUERIES (2)   — decomposition cap (1..3); each extra
//                                      query costs ~1.05s + quota on Brave's
//                                      free tier (1 req/s serialization)
//   SEARCH_REWRITE_TIMEOUT_MS (1500) — llm-rewrite budget; on timeout the
//                                      heuristic queries are used instead
//
// Semantic rerank (runResearchPipeline stage 4; Cohere or Jina):
//   RERANK_PROVIDER (auto)   — auto | cohere | jina | off. auto picks by
//                              whichever key is set (Cohere preferred)
//   COHERE_API_KEY / JINA_API_KEY — enables the stage; NO key → the
//                              existing heuristic order is kept (no regression)
//   RERANK_MODEL             — override (defaults: cohere rerank-v3.5,
//                              jina jina-reranker-v2-base-multilingual)
//   RERANK_TOP_N (8)         — results the rerank API scores/returns
//   RERANK_TIMEOUT_MS (1200) — hard budget; timeout → heuristic order kept
//   RERANK_MAX_PER_MINUTE (8)— client-side throttle (Cohere trial keys are
//                              10 req/min); over budget → rerank skipped

import {
  bestSnippet,
  buildRewritePrompt,
  cleanResults,
  extractMainText,
  heuristicQueryRewrite,
  isPubliclyRoutableUrl,
  looksLikeDuckDuckGoBlockPage,
  mergeHits,
  parseBraveWebResponse,
  parseDuckDuckGoHtml,
  parseLlmQueryRewrite,
  parseSearxngResponse,
  type RichHit,
} from "./core";

export type SearchProvider = "brave" | "searxng" | "duckduckgo";

export type PipelineResult = { hits: RichHit[]; provider: SearchProvider };

export type PipelineOptions = {
  /** Cap on cleaned results (default env SEARCH_MAX_RESULTS or 8). */
  maxResults?: number;
  /** Per-backend search timeout (default 8000ms). */
  searchTimeoutMs?: number;
  /** Backend-failure logger (default: silent). */
  log?: (message: string) => void;
};

export type EnrichOptions = {
  /** Top results to fetch content for (default env SEARCH_FETCH_PAGES or 3; 0 disables). */
  pages?: number;
  /** Per-page timeout (default env SEARCH_FETCH_TIMEOUT_MS or 2500ms). */
  timeoutMs?: number;
  /** Overall budget for the whole content phase (default env SEARCH_FETCH_BUDGET_MS or 4000ms). */
  budgetMs?: number;
  /** Extracted-text cap per page (default env SEARCH_CONTENT_MAX_CHARS or 4000). */
  maxChars?: number;
};

const BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search";
const DDG_ENDPOINT = "https://html.duckduckgo.com/html/";
const USER_AGENT =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

function envInt(name: string, fallback: number): number {
  const n = parseInt(process.env[name] ?? "", 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Brave Search API ──────────────────────────────────────────────────────
// Contract (api-dashboard.search.brave.com docs): GET /res/v1/web/search
// with X-Subscription-Token auth; q/count/extra_snippets/result_filter
// params; response web.results[].{title,url,description,extra_snippets}.
// Free plan: 1 request/second, ~2000 queries/month — hence the serialized
// ≥1.05s spacing between calls and the single 429 retry.

// Tunable so tests don't sleep real seconds; production keeps the defaults.
export const __braveTuningForTests = { minIntervalMs: 1050, retryDelayMs: 1100 };

let braveQueue: Promise<unknown> = Promise.resolve();
let braveLastStart = 0;

function braveSlot(): Promise<void> {
  const slot = braveQueue.then(async () => {
    const wait = braveLastStart + __braveTuningForTests.minIntervalMs - Date.now();
    if (wait > 0) await sleep(wait);
    braveLastStart = Date.now();
  });
  braveQueue = slot.catch(() => {});
  return slot;
}

export async function searchBrave(
  query: string,
  opts: { count?: number; timeoutMs?: number } = {},
): Promise<RichHit[]> {
  const key = (process.env.BRAVE_SEARCH_API_KEY ?? "").trim();
  if (!key) throw new Error("BRAVE_SEARCH_API_KEY not set");
  const count = Math.min(20, Math.max(1, opts.count ?? 16));
  const timeoutMs = opts.timeoutMs ?? 6000;

  const call = async (withExtraSnippets: boolean): Promise<Response> => {
    await braveSlot();
    const params = new URLSearchParams({
      q: query,
      count: String(count),
      // Plain-text descriptions (no <strong> highlight markup to strip).
      text_decorations: "0",
      result_filter: "web",
    });
    if (withExtraSnippets) params.set("extra_snippets", "1");
    return fetch(`${BRAVE_ENDPOINT}?${params.toString()}`, {
      headers: { Accept: "application/json", "X-Subscription-Token": key },
      signal: AbortSignal.timeout(timeoutMs),
    });
  };

  let res = await call(true);
  if (res.status === 429) {
    // Free-plan rate limit (1 req/s). One respectful retry, then give up
    // and let the caller fall back to SearXNG.
    const retryAfter = parseInt(res.headers.get("retry-after") ?? "", 10);
    const delay =
      Number.isFinite(retryAfter) && retryAfter > 0
        ? Math.min(retryAfter * 1000, 2500)
        : __braveTuningForTests.retryDelayMs;
    await sleep(delay);
    res = await call(true);
  }
  if (res.status === 400 || res.status === 422) {
    // extra_snippets is plan-gated on some Brave tiers — retry without it
    // rather than losing the whole search.
    res = await call(false);
  }
  if (!res.ok) throw new Error(`Brave HTTP ${res.status}`);
  return parseBraveWebResponse(await res.json());
}

// ── SearXNG ───────────────────────────────────────────────────────────────

export async function searchSearxng(
  query: string,
  opts: { timeoutMs?: number } = {},
): Promise<RichHit[]> {
  const base = (process.env.SEARXNG_URL ?? "").trim().replace(/\/+$/, "");
  if (!base) throw new Error("SEARXNG_URL not set");
  // SearXNG needs `search.formats: [html, json]` server-side or /search?format=json 403s.
  const url = `${base}/search?q=${encodeURIComponent(query)}&format=json&pageno=1`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(opts.timeoutMs ?? 8000),
  });
  if (!res.ok) throw new Error(`SearXNG HTTP ${res.status}`);
  return parseSearxngResponse(await res.json());
}

// ── DuckDuckGo HTML ───────────────────────────────────────────────────────

export async function searchDuckDuckGo(
  query: string,
  opts: { timeoutMs?: number } = {},
): Promise<RichHit[]> {
  const res = await fetch(`${DDG_ENDPOINT}?q=${encodeURIComponent(query)}`, {
    method: "POST",
    headers: {
      "User-Agent": USER_AGENT,
      Accept: "text/html,application/xhtml+xml",
      "Accept-Language": "en-US,en;q=0.9",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: `q=${encodeURIComponent(query)}&b=&kl=us-en`,
    signal: AbortSignal.timeout(opts.timeoutMs ?? 8000),
  });
  if (!res.ok) throw new Error(`DuckDuckGo HTTP ${res.status}`);
  const html = await res.text();
  const hits = parseDuckDuckGoHtml(html);
  if (hits.length === 0 && looksLikeDuckDuckGoBlockPage(html)) {
    // THROW rather than return [] — a blocked search must not masquerade
    // as "no results exist".
    throw new Error("DuckDuckGo returned an anti-bot/CAPTCHA page (search blocked)");
  }
  return hits;
}

// ── Source chain ──────────────────────────────────────────────────────────

/**
 * Run the search across the source chain and clean the results. Throws
 * only when EVERY attempted backend errors; a backend that succeeds with
 * zero results yields `{ hits: [] }` (a real "nothing found").
 */
export async function runSearchPipeline(
  query: string,
  opts: PipelineOptions = {},
): Promise<PipelineResult> {
  const max = opts.maxResults ?? envInt("SEARCH_MAX_RESULTS", 15);
  const searchTimeoutMs = opts.searchTimeoutMs ?? 8000;
  const log = opts.log ?? (() => {});
  const errors: string[] = [];
  let emptyProvider: SearchProvider | null = null;

  if ((process.env.BRAVE_SEARCH_API_KEY ?? "").trim()) {
    try {
      const raw = await searchBrave(query, {
        count: Math.min(20, max * 2),
        timeoutMs: searchTimeoutMs,
      });
      // Brave's own ranking is good — filter junk/dupes but keep its order.
      const hits = cleanResults(raw, query, { max, preserveOrder: true });
      if (hits.length) return { hits, provider: "brave" };
      emptyProvider = "brave";
    } catch (e) {
      errors.push(`brave: ${(e as Error).message}`);
      log(`Brave search failed, falling back: ${(e as Error).message}`);
    }
  }

  if ((process.env.SEARXNG_URL ?? "").trim()) {
    try {
      const raw = await searchSearxng(query, { timeoutMs: searchTimeoutMs });
      // The degraded engine mix (bing+mwmbl) ranks badly — rerank + filter.
      const hits = cleanResults(raw, query, { max });
      if (hits.length) return { hits, provider: "searxng" };
      if (!emptyProvider) emptyProvider = "searxng";
    } catch (e) {
      errors.push(`searxng: ${(e as Error).message}`);
      log(`SearXNG search failed, falling back: ${(e as Error).message}`);
    }
  }

  try {
    const raw = await searchDuckDuckGo(query, { timeoutMs: searchTimeoutMs });
    return { hits: cleanResults(raw, query, { max }), provider: "duckduckgo" };
  } catch (e) {
    errors.push(`duckduckgo: ${(e as Error).message}`);
    log(`DuckDuckGo search failed: ${(e as Error).message}`);
  }

  if (emptyProvider) return { hits: [], provider: emptyProvider };
  throw new Error(`web search failed (${errors.join("; ")})`);
}

// ── Page-content enrichment ───────────────────────────────────────────────

async function fetchPageText(
  url: string,
  timeoutMs: number,
  maxChars: number,
  budget: AbortSignal | null,
): Promise<string | null> {
  if (!isPubliclyRoutableUrl(url)) return null;
  const timeout = AbortSignal.timeout(timeoutMs);
  // AbortSignal.any needs node ≥20.3 / any modern bun — degrade to the
  // per-page timeout alone when unavailable.
  const signal =
    budget && typeof AbortSignal.any === "function"
      ? AbortSignal.any([timeout, budget])
      : timeout;
  const res = await fetch(url, {
    headers: {
      "User-Agent": USER_AGENT,
      Accept: "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.1",
      "Accept-Language": "en-US,en;q=0.9",
    },
    redirect: "follow",
    signal,
  });
  if (!res.ok) return null;
  const ctype = res.headers.get("content-type") ?? "";
  if (ctype && !/text\/html|application\/xhtml|text\/plain/i.test(ctype)) return null;
  const clen = parseInt(res.headers.get("content-length") ?? "", 10);
  if (Number.isFinite(clen) && clen > 3_000_000) return null;
  const body = await res.text();
  const text = /text\/plain/i.test(ctype)
    ? body.replace(/\s+/g, " ").trim().slice(0, maxChars)
    : extractMainText(body, maxChars);
  // Under ~200 chars means extraction failed (JS shell, paywall stub) —
  // the caller keeps the search snippet instead.
  return text.length >= 200 ? text : null;
}

/**
 * Fetch + extract main text for the top N hits, attaching it as
 * `hit.content`. All fetches run in PARALLEL; each page gets `timeoutMs`
 * and the whole phase is additionally capped by `budgetMs` — worst case
 * the search returns with snippets only, never late. Failures are
 * per-page and silent by design.
 */
export async function enrichHitsWithContent(
  hits: RichHit[],
  opts: EnrichOptions = {},
): Promise<RichHit[]> {
  const pages = opts.pages ?? envInt("SEARCH_FETCH_PAGES", 3);
  if (pages <= 0 || hits.length === 0) return hits;
  const timeoutMs = opts.timeoutMs ?? envInt("SEARCH_FETCH_TIMEOUT_MS", 2500);
  const budgetMs = opts.budgetMs ?? envInt("SEARCH_FETCH_BUDGET_MS", 4000);
  const maxChars = opts.maxChars ?? envInt("SEARCH_CONTENT_MAX_CHARS", 4000);

  const budget = AbortSignal.timeout(budgetMs);
  const targets = hits.slice(0, pages);
  const texts = await Promise.all(
    targets.map((h) =>
      fetchPageText(h.url, timeoutMs, maxChars, budget).catch(() => null),
    ),
  );
  return hits.map((h, i) => {
    const text = i < texts.length ? texts[i] : null;
    return text ? { ...h, content: text } : h;
  });
}

// ── Query rewriting / decomposition ───────────────────────────────────────
// Stage 1 of the research pass (the pattern Perplexity's pipeline opens
// with: parse intent → formulate retrieval queries). The heuristic tier is
// pure + instant (see core.ts); the optional LLM tier is injected by the
// caller (the hub proxy reuses its provider path) and is hard-bounded — a
// slow or failing rewrite degrades to the heuristic queries, never stalls
// the search.

export type QueryRewriteMode = "off" | "heuristic" | "llm";

/** Caller-injected LLM call for `llm` mode. Must resolve to the raw
 *  completion text; the pipeline enforces `timeoutMs` around it either way. */
export type LlmRewriteFn = (prompt: string, timeoutMs: number) => Promise<string>;

export type RewriteOptions = {
  /** Default: env SEARCH_QUERY_REWRITE, else "heuristic". */
  mode?: QueryRewriteMode;
  /** Decomposition cap, clamped 1..3 (default env SEARCH_REWRITE_MAX_QUERIES or 2). */
  maxQueries?: number;
  /** LLM-rewrite budget (default env SEARCH_REWRITE_TIMEOUT_MS or 1500ms). */
  timeoutMs?: number;
  llmRewrite?: LlmRewriteFn;
  log?: (message: string) => void;
};

function envRewriteMode(): QueryRewriteMode {
  const v = (process.env.SEARCH_QUERY_REWRITE ?? "").trim().toLowerCase();
  if (v === "off" || v === "0" || v === "false" || v === "none") return "off";
  if (v === "llm") return "llm";
  return "heuristic";
}

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`${what} timed out after ${ms}ms`)),
      ms,
    );
    p.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
}

/**
 * Turn the raw question into 1..N retrieval queries. `off` → [query]
 * untouched (today's behavior exactly); `heuristic` → pure rewrite;
 * `llm` → bounded LLM call, degrading to the heuristic queries on any
 * failure/timeout or when no llmRewrite fn was injected.
 */
export async function expandQueries(
  query: string,
  opts: RewriteOptions = {},
): Promise<string[]> {
  const mode = opts.mode ?? envRewriteMode();
  if (mode === "off") return [query];
  const maxQueries = Math.max(
    1,
    Math.min(3, opts.maxQueries ?? envInt("SEARCH_REWRITE_MAX_QUERIES", 2)),
  );
  const heuristic = heuristicQueryRewrite(query, maxQueries);
  if (mode !== "llm" || !opts.llmRewrite) return heuristic;

  const timeoutMs = opts.timeoutMs ?? envInt("SEARCH_REWRITE_TIMEOUT_MS", 1500);
  const log = opts.log ?? (() => {});
  try {
    const raw = await withTimeout(
      opts.llmRewrite(buildRewritePrompt(query, maxQueries), timeoutMs),
      timeoutMs,
      "query rewrite",
    );
    return parseLlmQueryRewrite(raw, query, maxQueries);
  } catch (e) {
    log(`query rewrite failed: ${(e as Error).message} — using heuristic queries`);
    return heuristic;
  }
}

// ── Semantic rerank (Cohere Rerank / Jina Reranker) ───────────────────────
// Stage 4: rescore the retrieved+extracted candidates by TRUE relevance to
// the query with a cross-encoder rerank API — the standard enterprise-RAG
// precision stage. Contracts implemented:
//   Cohere: POST https://api.cohere.com/v2/rerank
//     { model, query, documents: string[], top_n } → { results: [{ index,
//     relevance_score }] }  (Bearer auth; trial keys: 10 req/min, ~1000/mo)
//   Jina:   POST https://api.jina.ai/v1/rerank — same request shape plus
//     return_documents:false → { results: [{ index, relevance_score }] }
//     (Bearer auth; free keys: 10M tokens, ~100 req/min)
// EVERY failure mode (no key, throttle, timeout, HTTP error, junk body)
// returns the input order unchanged — the heuristic rerank remains the
// floor, so a missing key is a no-op, not a regression.

export type RerankProviderName = "cohere" | "jina";

const COHERE_RERANK_ENDPOINT = "https://api.cohere.com/v2/rerank";
const JINA_RERANK_ENDPOINT = "https://api.jina.ai/v1/rerank";

const RERANK_DEFAULT_MODEL: Record<RerankProviderName, string> = {
  cohere: "rerank-v3.5",
  jina: "jina-reranker-v2-base-multilingual",
};

/** Pick the rerank provider: RERANK_PROVIDER=off disables; auto (default)
 *  selects by whichever API key is present, Cohere first. */
export function resolveRerankProvider(): {
  name: RerankProviderName;
  key: string;
} | null {
  const pref = (process.env.RERANK_PROVIDER ?? "auto").trim().toLowerCase();
  if (pref === "off" || pref === "none" || pref === "0") return null;
  const cohere = (process.env.COHERE_API_KEY ?? "").trim();
  const jina = (process.env.JINA_API_KEY ?? "").trim();
  if (pref === "cohere") return cohere ? { name: "cohere", key: cohere } : null;
  if (pref === "jina") return jina ? { name: "jina", key: jina } : null;
  if (cohere) return { name: "cohere", key: cohere };
  if (jina) return { name: "jina", key: jina };
  return null;
}

// Client-side sliding-window throttle (free tiers: Cohere trial 10 req/min).
// Over budget → SKIP the rerank (keep heuristic order) rather than sleep —
// sleeping would blow the latency budget for zero user-visible gain.
export const __rerankTuningForTests = { windowMs: 60_000 };
let rerankCallTimes: number[] = [];

export function __resetRerankThrottleForTests(): void {
  rerankCallTimes = [];
}

function rerankThrottled(maxPerMinute: number): boolean {
  const now = Date.now();
  rerankCallTimes = rerankCallTimes.filter(
    (t) => now - t < __rerankTuningForTests.windowMs,
  );
  if (rerankCallTimes.length >= maxPerMinute) return true;
  rerankCallTimes.push(now);
  return false;
}

export type RerankOptions = {
  /** Results the API scores/returns (default env RERANK_TOP_N or 8). */
  topN?: number;
  /** Hard budget (default env RERANK_TIMEOUT_MS or 1200ms). */
  timeoutMs?: number;
  log?: (message: string) => void;
};

/** Document text the reranker scores: title + extracted content (preferred)
 *  or the search snippets. Capped — rerank pricing/latency scales with tokens. */
function rerankDocText(hit: RichHit): string {
  const body = hit.content ? hit.content.slice(0, 1200) : bestSnippet(hit, 600);
  return body ? `${hit.title}\n${body}` : hit.title;
}

/**
 * Reorder hits by semantic relevance to the query via the configured rerank
 * API, attaching `rerankScore` to scored hits (unscored ones keep their
 * relative order after the scored block). NEVER throws — any failure
 * returns the input unchanged.
 */
export async function rerankHits(
  query: string,
  hits: RichHit[],
  opts: RerankOptions = {},
): Promise<RichHit[]> {
  if (hits.length <= 1) return hits;
  const resolved = resolveRerankProvider();
  if (!resolved) return hits;
  const log = opts.log ?? (() => {});

  const maxPerMinute = envInt("RERANK_MAX_PER_MINUTE", 8);
  if (rerankThrottled(maxPerMinute)) {
    log(`rerank skipped: client throttle (${maxPerMinute}/min) — keeping heuristic order`);
    return hits;
  }

  const timeoutMs = opts.timeoutMs ?? envInt("RERANK_TIMEOUT_MS", 1200);
  const topN = Math.min(hits.length, Math.max(1, opts.topN ?? envInt("RERANK_TOP_N", 15)));
  const model =
    (process.env.RERANK_MODEL ?? "").trim() || RERANK_DEFAULT_MODEL[resolved.name];
  const documents = hits.map(rerankDocText);
  const body =
    resolved.name === "cohere"
      ? { model, query, documents, top_n: topN }
      : { model, query, documents, top_n: topN, return_documents: false };

  try {
    const res = await fetch(
      resolved.name === "cohere" ? COHERE_RERANK_ENDPOINT : JINA_RERANK_ENDPOINT,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${resolved.key}`,
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      },
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as {
      results?: Array<{ index?: number; relevance_score?: number }>;
    };
    const results = Array.isArray(data?.results) ? data.results : [];
    const ranked: RichHit[] = [];
    const used = new Set<number>();
    for (const r of results) {
      const i = r?.index;
      if (typeof i !== "number" || !Number.isInteger(i)) continue;
      if (i < 0 || i >= hits.length || used.has(i)) continue;
      used.add(i);
      ranked.push(
        typeof r.relevance_score === "number"
          ? { ...hits[i], rerankScore: r.relevance_score }
          : { ...hits[i] },
      );
    }
    if (!ranked.length) return hits; // junk body — keep heuristic order
    for (let i = 0; i < hits.length; i++) if (!used.has(i)) ranked.push(hits[i]);
    return ranked;
  } catch (e) {
    log(`rerank failed (${resolved.name}): ${(e as Error).message} — keeping heuristic order`);
    return hits;
  }
}

// ── Full research pass ────────────────────────────────────────────────────

export type ResearchOptions = PipelineOptions & {
  /** Stage 1 config; default mode comes from SEARCH_QUERY_REWRITE. */
  rewrite?: RewriteOptions;
  /** LLM rewrite call for `llm` mode (shorthand for rewrite.llmRewrite). */
  llmRewrite?: LlmRewriteFn;
  /** false disables stage 4; an object tunes it. Default: on (a missing
   *  rerank key already makes it a no-op). */
  rerank?: boolean | RerankOptions;
  /** false disables stage 3 (content fetch); an object tunes it. */
  enrich?: EnrichOptions | false;
};

export type ResearchResult = PipelineResult & {
  /** The retrieval queries actually executed (1..3). */
  queries: string[];
};

/**
 * The complete research pass, Perplexity-style:
 *   1. query rewrite / decomposition   (bounded; off/heuristic are instant)
 *   2. retrieval per query + merge     (Brave self-serializes ≥1.05s apart)
 *   3. page-content enrichment         (parallel, per-page timeout + budget)
 *   4. semantic rerank                 (Cohere/Jina; heuristic-order floor)
 * With rewrite off/heuristic on a simple query, no rerank key, and default
 * enrichment this is byte-for-byte the old search+enrich behavior.
 */
export async function runResearchPipeline(
  query: string,
  opts: ResearchOptions = {},
): Promise<ResearchResult> {
  const log = opts.log ?? (() => {});
  const max = opts.maxResults ?? envInt("SEARCH_MAX_RESULTS", 15);
  const pipeOpts: PipelineOptions = {
    maxResults: max,
    searchTimeoutMs: opts.searchTimeoutMs,
    log,
  };

  // 1. Rewrite / decompose.
  const queries = await expandQueries(query, {
    ...(opts.rewrite ?? {}),
    llmRewrite: opts.rewrite?.llmRewrite ?? opts.llmRewrite,
    log: opts.rewrite?.log ?? log,
  });

  // 2. Retrieve (+ merge across sub-queries).
  let hits: RichHit[];
  let provider: SearchProvider;
  if (queries.length === 1) {
    ({ hits, provider } = await runSearchPipeline(queries[0], pipeOpts));
  } else {
    const settled = await Promise.allSettled(
      queries.map((q) => runSearchPipeline(q, pipeOpts)),
    );
    const ok = settled.filter(
      (s): s is PromiseFulfilledResult<PipelineResult> => s.status === "fulfilled",
    );
    if (!ok.length) throw (settled[0] as PromiseRejectedResult).reason;
    provider = ok[0].value.provider;
    hits = mergeHits(ok.map((r) => r.value.hits), max);
  }

  // 3. Enrich with page content.
  if (opts.enrich !== false) {
    hits = await enrichHitsWithContent(hits, opts.enrich ?? {});
  }

  // 4. Semantic rerank against the ORIGINAL question (full intent).
  if (opts.rerank !== false) {
    const rr = typeof opts.rerank === "object" ? opts.rerank : {};
    hits = await rerankHits(query, hits, { ...rr, log: rr.log ?? log });
  }

  return { hits, provider, queries };
}
