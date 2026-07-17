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

import {
  cleanResults,
  extractMainText,
  isPubliclyRoutableUrl,
  looksLikeDuckDuckGoBlockPage,
  parseBraveWebResponse,
  parseDuckDuckGoHtml,
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
  const max = opts.maxResults ?? envInt("SEARCH_MAX_RESULTS", 8);
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
