// Shared web-search result pipeline — the PURE half (no I/O, no env reads).
//
// Turns raw backend responses (Brave Search API / SearXNG / DuckDuckGo HTML)
// into a small, clean, deduplicated, relevance-ordered result set, and
// extracts readable main text from fetched pages so the model gets real
// content instead of 90-char snippets. The measured failure this fixes
// (2026-07 VPS probe): the datacenter IP degrades SearXNG to bing+mwmbl,
// which return generic news HOMEPAGES ("BBC News", "CNN") and junk-crawl
// spam — the model can't synthesize from those and re-searches, looking
// "stuck".
//
// MIRRORED FILE — exists at BOTH
//   src/cli/src/proxy/search/core.ts   (hub proxy, bun runtime)
//   src/web/src/lib/search/core.ts     (Next.js voice-search route, node)
// and must stay byte-identical (src/web/tests/search-sync.test.ts enforces
// it). The hub Docker image builds from src/cli alone and the web image
// from src/web alone, so a real shared package would complicate both
// builds — a test-enforced mirror is the deliberate tradeoff.

export type RichHit = {
  title: string;
  url: string;
  /** Best short excerpt from the search backend ("" when none). */
  snippet: string;
  /** Brave extra_snippets — additional real-text excerpts, no fetch needed. */
  extraSnippets?: string[];
  /** SearXNG engine that produced the hit (used to down-rank junk crawls). */
  engine?: string;
  /** Main article text fetched + extracted from the page (enrichment step). */
  content?: string;
  /** Cohere/Jina rerank relevance (0..1); set only when a rerank API ran. */
  rerankScore?: number;
};

// ── Small text utilities ──────────────────────────────────────────────────

export function stripTags(s: string): string {
  // Loop until stable — a single pass lets `<<a>script>` collapse into a
  // fresh `<script>` tag (js/incomplete-multi-character-sanitization).
  let prev: string;
  do {
    prev = s;
    s = s.replace(/<[^>]+>/g, "");
  } while (s !== prev);
  return s;
}

export function decodeEntities(s: string): string {
  // Decode &amp; LAST: doing it first turns `&amp;lt;` into `&lt;` then `<`
  // (double-decoding). Last keeps `&amp;lt;` → `&lt;` (js/double-escaping).
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/gi, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(parseInt(n, 10)))
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&amp;/g, "&");
}

function cleanText(s: string): string {
  return decodeEntities(stripTags(s)).replace(/\s+/g, " ").trim();
}

/** Truncate at a word boundary with an ellipsis; never mid-word when avoidable. */
export function truncateAtWord(s: string, max: number): string {
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd() + "…";
}

const STOPWORDS = new Set([
  "the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "is", "are",
  "was", "were", "be", "been", "what", "who", "when", "where", "which", "how",
  "why", "with", "about", "this", "that", "these", "those", "at", "by", "from",
  "as", "it", "its", "do", "does", "did", "can", "could", "will", "would",
  "should", "me", "my", "i", "you", "your", "we", "our", "they", "their",
  // Query filler that matches every news site's chrome — never signal.
  "latest", "news", "current", "recent", "today", "now", "new", "update",
  "updates", "developments",
]);

export function tokenize(s: string): string[] {
  return s
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length >= 2 && !STOPWORDS.has(t));
}

// ── URL classification ────────────────────────────────────────────────────

// eTLD+1 heuristic: enough two-level public suffixes to dedup real-world
// news/reference domains correctly (bbc.co.uk, smh.com.au, …) without
// shipping the full public-suffix list.
const TWO_LEVEL_TLDS = new Set([
  "co.uk", "org.uk", "ac.uk", "gov.uk", "net.uk", "me.uk",
  "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
  "com.au", "net.au", "org.au", "edu.au", "gov.au",
  "co.nz", "org.nz", "net.nz", "govt.nz",
  "co.in", "net.in", "org.in", "gov.in",
  "co.za", "org.za", "gov.za",
  "com.br", "com.mx", "com.ar", "com.tr", "com.cn", "com.hk",
  "com.sg", "com.tw", "com.my", "com.vn", "com.ph",
  "co.kr", "or.kr", "co.id", "co.th", "co.il",
]);

/** Registrable domain (eTLD+1) for dedup: "https://www.bbc.co.uk/x" → "bbc.co.uk". */
export function registrableDomain(url: string): string {
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/^www\./, "");
    const parts = host.split(".");
    if (parts.length <= 2) return host;
    const lastTwo = parts.slice(-2).join(".");
    if (TWO_LEVEL_TLDS.has(lastTwo)) return parts.slice(-3).join(".");
    return lastTwo;
  } catch {
    return url.toLowerCase();
  }
}

/** Bare-domain homepage: path "/" (or empty) and no query — "BBC News" et al. */
export function isBareHomepage(url: string): boolean {
  try {
    const u = new URL(url);
    return (u.pathname === "/" || u.pathname === "") && !u.search;
  } catch {
    return false;
  }
}

/**
 * A homepage is a GOOD result when the user asked for the site itself
 * ("openai" → openai.com). Match query tokens against the domain label.
 */
export function isNavigationalMatch(queryTokens: string[], url: string): boolean {
  const domain = registrableDomain(url);
  const label = domain.split(".")[0] ?? "";
  if (!label) return false;
  return queryTokens.some(
    (t) =>
      t === label ||
      (t.length >= 4 && label.includes(t)) ||
      (label.length >= 4 && t.includes(label)),
  );
}

// Search engines' own pages and content-free aggregator shells. These were
// literally in the measured top-8 ("German Google News") — never useful.
const JUNK_HOSTS = new Set([
  "duckduckgo.com", "bing.com", "search.brave.com", "search.yahoo.com",
  "mwmbl.org",
]);

// SearXNG engines whose results are low-trust (tiny junk crawls).
const LOW_TRUST_ENGINES = new Set(["mwmbl"]);

export function isJunkHit(hit: RichHit): boolean {
  let u: URL;
  try {
    u = new URL(hit.url);
  } catch {
    return true;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return true;
  const host = u.hostname.toLowerCase().replace(/^www\./, "");
  if (JUNK_HOSTS.has(host)) return true;
  if (host.startsWith("news.google.")) return true; // aggregator shell
  if (host.startsWith("google.") && u.pathname.startsWith("/search")) return true;
  return false;
}

/**
 * SSRF guard for the content-fetch step: search backends are not fully
 * trusted, so never fetch loopback / RFC1918 / link-local / bare-hostname
 * URLs (which would resolve to in-network services like `searxng:8080`).
 */
export function isPubliclyRoutableUrl(url: string): boolean {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;
  const host = u.hostname.toLowerCase();
  if (!host.includes(".")) return false; // bare service names (searxng, hub…)
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return false;
  if (host.endsWith(".internal")) return false;
  const v4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const [a, b] = [parseInt(v4[1], 10), parseInt(v4[2], 10)];
    if (a === 127 || a === 10 || a === 0) return false;
    if (a === 192 && b === 168) return false;
    if (a === 172 && b >= 16 && b <= 31) return false;
    if (a === 169 && b === 254) return false;
    if (a >= 224) return false; // multicast/reserved
  }
  if (host.startsWith("[")) return false; // IPv6 literal — skip outright
  return true;
}

/** Canonical key for exact-URL dedup: drop hash, utm_* noise, trailing slash. */
export function normalizeUrlKey(url: string): string {
  try {
    const u = new URL(url);
    const params = new URLSearchParams();
    for (const [k, v] of u.searchParams) {
      if (!/^(utm_|fbclid|gclid|ref$)/i.test(k)) params.append(k, v);
    }
    const qs = params.toString();
    return (
      u.hostname.toLowerCase().replace(/^www\./, "") +
      u.pathname.replace(/\/+$/, "") +
      (qs ? `?${qs}` : "")
    );
  } catch {
    return url;
  }
}

// ── Backend response parsing ──────────────────────────────────────────────

/** Brave Web Search API: { web: { results: [{ title, url, description, extra_snippets }] } } */
export function parseBraveWebResponse(data: unknown): RichHit[] {
  const results = (data as { web?: { results?: unknown } })?.web?.results;
  if (!Array.isArray(results)) return [];
  const hits: RichHit[] = [];
  for (const r of results) {
    const o = (r ?? {}) as Record<string, unknown>;
    const title = cleanText(String(o.title ?? ""));
    const url = String(o.url ?? "").trim();
    if (!title || !url) continue;
    const extra = Array.isArray(o.extra_snippets)
      ? o.extra_snippets.map((s) => cleanText(String(s ?? ""))).filter(Boolean)
      : [];
    hits.push({
      title,
      url,
      // Brave descriptions carry <strong> highlight markup — strip it.
      snippet: cleanText(String(o.description ?? "")),
      ...(extra.length ? { extraSnippets: extra } : {}),
    });
  }
  return hits;
}

/** SearXNG JSON API: { results: [{ title, url, content, engine }] } */
export function parseSearxngResponse(data: unknown): RichHit[] {
  const results = (data as { results?: unknown })?.results;
  if (!Array.isArray(results)) return [];
  const hits: RichHit[] = [];
  for (const r of results) {
    const o = (r ?? {}) as Record<string, unknown>;
    const title = cleanText(String(o.title ?? ""));
    const url = String(o.url ?? "").trim();
    if (!title || !url) continue;
    hits.push({
      title,
      url,
      snippet: cleanText(String(o.content ?? "")),
      ...(o.engine ? { engine: String(o.engine) } : {}),
    });
  }
  return hits;
}

function unwrapDuckDuckGoRedirect(href: string): string {
  try {
    const absolute = href.startsWith("//") ? "https:" + href : href;
    const parsed = new URL(absolute, "https://duckduckgo.com/");
    const uddg = parsed.searchParams.get("uddg");
    if (uddg) return decodeURIComponent(uddg);
    return parsed.toString();
  } catch {
    return href;
  }
}

/** DuckDuckGo HTML results — anchors plus the result__snippet that follows each. */
export function parseDuckDuckGoHtml(html: string): RichHit[] {
  const hits: RichHit[] = [];
  const seen = new Set<string>();
  const anchorRe =
    /<a[^>]+class="[^"]*\bresult__a\b[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi;
  const snippetRe =
    /class="[^"]*\bresult__snippet\b[^"]*"[^>]*>([\s\S]*?)<\/(?:a|td|div|span)>/i;

  type Anchor = { url: string; title: string; end: number };
  const anchors: Anchor[] = [];
  let m: RegExpExecArray | null;
  while ((m = anchorRe.exec(html)) !== null) {
    const url = unwrapDuckDuckGoRedirect(decodeEntities(m[1]));
    const title = cleanText(m[2]);
    if (!url || !title) continue;
    anchors.push({ url, title, end: anchorRe.lastIndex });
  }
  for (let i = 0; i < anchors.length; i++) {
    const a = anchors[i];
    if (seen.has(a.url)) continue;
    seen.add(a.url);
    // The snippet element sits between this result anchor and the next one.
    const sliceEnd = i + 1 < anchors.length ? anchors[i + 1].end : html.length;
    const between = html.slice(a.end, sliceEnd);
    const sm = snippetRe.exec(between);
    hits.push({ title: a.title, url: a.url, snippet: sm ? cleanText(sm[1]) : "" });
  }
  return hits;
}

/**
 * A DuckDuckGo anti-bot / CAPTCHA interstitial returns HTTP 200 with NO
 * result anchors — detect it so a blocked search never masquerades as
 * "no results exist".
 */
export function looksLikeDuckDuckGoBlockPage(html: string): boolean {
  const h = html.toLowerCase();
  return (
    h.includes("anomaly") ||
    h.includes("challenge-form") ||
    h.includes("detected unusual") ||
    h.includes("unusual traffic") ||
    h.includes("are you a robot") ||
    h.includes("captcha")
  );
}

// ── Cleaning / dedup / rerank ─────────────────────────────────────────────

/**
 * Relevance heuristic for backends with junk-heavy ranking (SearXNG's
 * bing+mwmbl mix). Token overlap, title-weighted; homepages and
 * low-trust-engine hits sink.
 */
export function scoreHit(hit: RichHit, queryTokens: string[]): number {
  const titleTokens = new Set(tokenize(hit.title));
  const snippetTokens = new Set(tokenize(hit.snippet));
  let overlap = 0;
  for (const t of queryTokens) {
    if (titleTokens.has(t)) overlap += 2;
    else if (snippetTokens.has(t)) overlap += 1;
  }
  let score = queryTokens.length ? overlap / (2 * queryTokens.length) : 0;
  if (!hit.snippet) score -= 0.15;
  if (hit.engine && LOW_TRUST_ENGINES.has(hit.engine)) score -= 0.3;
  if (isBareHomepage(hit.url)) score -= 0.4;
  return score;
}

export type CleanOptions = {
  /** Cap on returned results (default 8). */
  max?: number;
  /** Keep the backend's order (Brave ranks well); default false = rerank. */
  preserveOrder?: boolean;
};

/**
 * The full pure cleanup: drop junk + bare homepages (keeping navigational
 * matches), rerank unless the source ranks well, dedup by exact URL then
 * registrable domain, cap to the top N. Never returns empty when the input
 * had any structurally-valid hit (the filters are heuristics — degrade to
 * unfiltered rather than to nothing).
 */
export function cleanResults(
  hits: RichHit[],
  query: string,
  opts: CleanOptions = {},
): RichHit[] {
  const max = opts.max ?? 8;
  const queryTokens = tokenize(query);

  const valid = hits.filter((h) => h.title && h.url && !isJunkHit(h));
  const filtered = valid.filter(
    (h) => !isBareHomepage(h.url) || isNavigationalMatch(queryTokens, h.url),
  );
  // Safety valve: heuristics must degrade, not blank the result set.
  let pool = filtered.length ? filtered : valid;

  if (!opts.preserveOrder) {
    const scored = pool.map((h, i) => ({ h, i, s: scoreHit(h, queryTokens) }));
    scored.sort((a, b) => b.s - a.s || a.i - b.i);
    pool = scored.map((x) => x.h);
  }

  const out: RichHit[] = [];
  const seenUrl = new Set<string>();
  const seenDomain = new Set<string>();
  for (const h of pool) {
    const urlKey = normalizeUrlKey(h.url);
    if (seenUrl.has(urlKey)) continue;
    const domain = registrableDomain(h.url);
    if (seenDomain.has(domain)) continue;
    seenUrl.add(urlKey);
    seenDomain.add(domain);
    out.push(h);
    if (out.length >= max) break;
  }
  return out;
}

/** Best no-fetch excerpt: description + Brave extra_snippets, deduped, capped. */
export function bestSnippet(hit: RichHit, maxChars = 400): string {
  const parts: string[] = [];
  for (const p of [hit.snippet, ...(hit.extraSnippets ?? [])]) {
    const t = (p ?? "").trim();
    if (t && !parts.includes(t)) parts.push(t);
  }
  return truncateAtWord(parts.join(" … "), maxChars);
}

// ── Main-text extraction (readability-lite, dependency-free) ──────────────

function stripBlocks(html: string, tags: string[]): string {
  for (const tag of tags) {
    const re = new RegExp(`<${tag}\\b[^>]*>[\\s\\S]*?<\\/${tag}>`, "gi");
    let prev: string;
    do {
      prev = html;
      html = html.replace(re, " ");
    } while (html !== prev);
  }
  return html;
}

function pickContentRegion(html: string): string {
  // Prefer semantic containers when they carry real content.
  const articleRe = /<article\b[^>]*>([\s\S]*?)<\/article>/gi;
  let best = "";
  let m: RegExpExecArray | null;
  while ((m = articleRe.exec(html)) !== null) {
    if (m[1].length > best.length) best = m[1];
  }
  if (best.length > 500) return best;
  const main = /<main\b[^>]*>([\s\S]*?)<\/main>/i.exec(html);
  if (main && main[1].length > 500) return main[1];
  const body = /<body\b[^>]*>([\s\S]*?)<\/body>/i.exec(html);
  return body ? body[1] : html;
}

/**
 * Extract readable article text from an HTML page. Deliberately
 * dependency-free (regex-based): runs identically under bun and node, and
 * page fetches are already time-boxed to ~2s so a heavyweight DOM parse
 * would dominate the budget. Quality target is "clean text for an LLM",
 * not perfect readability output.
 */
export function extractMainText(html: string, maxChars = 4000): string {
  // Bound the regex work on pathological pages.
  html = html.slice(0, 600_000);
  html = html.replace(/<!--[\s\S]*?-->/g, " ");
  html = stripBlocks(html, [
    "script", "style", "noscript", "svg", "iframe", "canvas", "template",
    "object", "select",
  ]);
  let region = pickContentRegion(html);
  region = stripBlocks(region, ["nav", "header", "footer", "aside", "form", "button", "figure"]);

  const text = region
    .replace(/<(?:br|hr)\s*\/?>/gi, "\n")
    .replace(/<li\b[^>]*>/gi, "\n- ")
    .replace(
      /<\/(?:p|div|h[1-6]|li|tr|section|blockquote|pre|ul|ol|table|dd|dt|figcaption)>/gi,
      "\n",
    );

  const lines = decodeEntities(stripTags(text))
    .split("\n")
    .map((l) => l.replace(/\s+/g, " ").trim())
    .filter((l) => l.length > 2);

  // Drop consecutive duplicates (repeated nav crumbs / cookie banners).
  const deduped: string[] = [];
  for (const l of lines) {
    if (deduped[deduped.length - 1] !== l) deduped.push(l);
  }

  return truncateAtWord(deduped.join("\n"), maxChars);
}

// ── Query rewriting / decomposition (pure half) ───────────────────────────
//
// Perplexity-style stage 1: turn the user's raw question into 1–3 queries
// that retrieve well. The heuristic layer is deliberately conservative —
// it only (a) strips unambiguous conversational courtesy prefixes and
// (b) splits inputs that contain TWO explicit questions. A simple query
// passes through untouched, so single-query behavior never regresses.

const COURTESY_PREFIXES: RegExp[] = [
  /^(?:hey|hi|ok|okay)[,!]?\s+jarvis[,!]?\s+/i,
  /^jarvis[,!]?\s+/i,
  /^please\s+/i,
  /^(?:can|could|would|will)\s+you\s+(?:please\s+)?/i,
  /^(?:tell|show)\s+me\s+(?:about\s+)?/i,
  /^(?:find\s+out|look\s+up)\s+(?:about\s+)?/i,
  /^search(?:\s+the\s+web)?\s+for\s+/i,
  /^(?:i(?:'d|\s+would)?\s+(?:like|want)\s+to\s+know|i\s+was\s+wondering)\s+(?:about\s+)?/i,
];

/** Strip conversational filler prefixes; never strips down to nothing. */
export function stripCourtesyPrefix(query: string): string {
  const original = query.trim();
  let q = original;
  let changed = true;
  while (changed) {
    changed = false;
    for (const re of COURTESY_PREFIXES) {
      const next = q.replace(re, "").trim();
      if (next !== q) {
        q = next;
        changed = true;
      }
    }
  }
  // Too aggressive → keep the original (a query must still say something).
  if (!q || (q.split(/\s+/).length < 2 && tokenize(q).length < 1)) return original;
  return q;
}

// Interrogative words that mark the start of a second, separate question.
const INTERROGATIVE_RE =
  /(?:what|what's|when|where|who|whose|whom|how|why|which|is|are|was|were|does|do|did|can|will|should)/;

/**
 * Rewrite the raw question into 1..maxQueries retrieval queries. Splits
 * ONLY on explicit multi-question markers ("…? …" or ", and how …") so
 * "research on AI and ML" style conjunctions never split. Returns the
 * cleaned single query when there is nothing to decompose.
 */
export function heuristicQueryRewrite(query: string, maxQueries = 3): string[] {
  const cleaned = stripCourtesyPrefix(query);
  if (maxQueries <= 1) return [cleaned];

  // "who won? what was the score" — sentence boundary after a question mark.
  let parts = cleaned
    .split(/(?<=\?)\s+(?=\S)/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length === 1) {
    // "what is X and how does Y work" — a second explicit interrogative.
    const re = new RegExp(
      `,?\\s+and\\s+(?:also\\s+)?(?=${INTERROGATIVE_RE.source}\\b)`,
      "i",
    );
    const m = cleaned.split(re);
    if (m.length > 1) parts = m.map((p) => p.trim()).filter(Boolean);
  }

  // Every fragment must stand alone as a query, or don't split at all.
  const good = parts.filter(
    (p) => p.split(/\s+/).length >= 3 && tokenize(p).length >= 1,
  );
  if (good.length <= 1) return [cleaned];

  const out: string[] = [];
  for (const p of good) {
    const sub = stripCourtesyPrefix(p.replace(/^and\s+(?:also\s+)?/i, ""));
    if (!out.some((e) => e.toLowerCase() === sub.toLowerCase())) out.push(sub);
    if (out.length >= maxQueries) break;
  }
  return out.length ? out : [cleaned];
}

/** Compact prompt for the optional LLM rewrite stage (see pipeline.ts). */
export function buildRewritePrompt(question: string, maxQueries: number): string {
  return (
    `Rewrite the user's question into at most ${maxQueries} short web-search ` +
    `queries optimized for retrieval. Split compound questions into separate ` +
    `queries; keep key names, numbers and dates; drop conversational filler. ` +
    `Respond with ONLY a JSON array of query strings, nothing else.\n\n` +
    `Question: ${question}`
  );
}

/**
 * Parse the LLM rewrite response: a JSON array (possibly code-fenced) or a
 * plain/numbered list, one query per line. Anything unusable degrades to
 * [original] — the rewrite stage can never lose the user's question.
 */
export function parseLlmQueryRewrite(
  raw: string,
  original: string,
  maxQueries = 3,
): string[] {
  const out: string[] = [];
  const push = (s: unknown) => {
    if (typeof s !== "string" || out.length >= maxQueries) return;
    const q = s
      .trim()
      .replace(/^\d+[.)]\s*/, "")
      .replace(/^[-*]\s*/, "")
      .replace(/^["'`]+|["'`]+$/g, "")
      .trim();
    if (!q || q.length > 200) return;
    if (q.endsWith(":") || q.split(/\s+/).length > 15) return; // prose, not a query
    if (tokenize(q).length === 0) return;
    if (!out.some((e) => e.toLowerCase() === q.toLowerCase())) out.push(q);
  };

  const text = (raw ?? "").trim();
  const jsonMatch = text.match(/\[[\s\S]*\]/);
  if (jsonMatch) {
    try {
      const arr = JSON.parse(jsonMatch[0]);
      if (Array.isArray(arr)) for (const s of arr) push(s);
    } catch {
      // fall through to line parsing
    }
  }
  if (!out.length) for (const line of text.split("\n")) push(line);
  return out.length ? out : [original];
}

/**
 * Merge per-query result lists (each already cleaned) round-robin, so every
 * sub-question contributes its best hits first; dedup by exact URL and
 * registrable domain; cap to max.
 */
export function mergeHits(lists: RichHit[][], max: number): RichHit[] {
  const out: RichHit[] = [];
  const seenUrl = new Set<string>();
  const seenDomain = new Set<string>();
  const longest = lists.reduce((n, l) => Math.max(n, l.length), 0);
  for (let i = 0; i < longest && out.length < max; i++) {
    for (const list of lists) {
      if (out.length >= max) break;
      const h = list[i];
      if (!h) continue;
      const urlKey = normalizeUrlKey(h.url);
      if (seenUrl.has(urlKey)) continue;
      const domain = registrableDomain(h.url);
      if (seenDomain.has(domain)) continue;
      seenUrl.add(urlKey);
      seenDomain.add(domain);
      out.push(h);
    }
  }
  return out;
}

// ── LLM-facing formatting ─────────────────────────────────────────────────

/**
 * The best supporting passage for a hit relative to the query: the
 * extracted-content region with the highest query-token overlap (extended
 * to fill maxChars), else the best snippet. This is what the gateway ships
 * as `cited_text` on each result item — the plaintext analogue of the
 * cited_text Anthropic's own web_search citations carry.
 */
export function bestSupportingPassage(
  hit: RichHit,
  query: string,
  maxChars = 600,
): string {
  const content = (hit.content ?? "").trim();
  if (!content) return bestSnippet(hit, maxChars);
  const queryTokens = tokenize(query);
  const lines = content
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length >= 40);
  if (!lines.length || !queryTokens.length) return truncateAtWord(content, maxChars);

  let bestIdx = 0;
  let bestScore = -1;
  for (let i = 0; i < lines.length; i++) {
    const lineTokens = new Set(tokenize(lines[i]));
    let s = 0;
    for (const t of queryTokens) if (lineTokens.has(t)) s++;
    if (s > bestScore) {
      bestScore = s;
      bestIdx = i;
    }
  }
  if (bestScore <= 0) return truncateAtWord(content, maxChars);

  let out = lines[bestIdx];
  for (let i = bestIdx + 1; i < lines.length && out.length < maxChars; i++) {
    out += " " + lines[i];
  }
  return truncateAtWord(out.replace(/\s+/g, " "), maxChars);
}

/** Digest evidence cap: ~6–8 numbered chunks is the grounding sweet spot. */
export const DIGEST_MAX_SOURCES = 8;

/**
 * One self-sufficient research digest: numbered evidence entries with
 * title, URL, and real page content (or the best snippets), followed by
 * the citation-grounding contract. Written so the model can answer + cite
 * after ONE search instead of re-searching, in Claude's citation style
 * (claims attributed to named, linked sources), and ABSTAIN instead of
 * fabricating when the evidence is weak.
 */
export function formatHitsAsDigest(
  query: string,
  hits: RichHit[],
  perHitChars = 1500,
): string {
  const parts: string[] = [];
  parts.push(
    `Web search results for "${query}". Each entry: [n] title, URL, then extracted page content or search snippets.`,
  );
  hits.slice(0, DIGEST_MAX_SOURCES).forEach((h, i) => {
    const body = h.content
      ? truncateAtWord(h.content, perHitChars)
      : bestSnippet(h, Math.min(perHitChars, 600));
    parts.push(`[${i + 1}] ${h.title}\nURL: ${h.url}${body ? `\n${body}` : ""}`);
  });
  parts.push(
    [
      "Ground your answer in these sources:",
      "- Synthesize ONLY from the numbered sources above; they are sufficient — do not search again for the same question.",
      '- Attribute each substantive claim to its source inline, e.g. "According to [title](url), …" or append ([title](url)) after the claim. Preserve the exact URLs; cite only sources you actually used.',
      "- If the sources do not reliably answer part of the question, say plainly that you couldn't find reliable sources on that part — never fill the gap from memory or fabricate a citation.",
    ].join("\n"),
  );
  return parts.join("\n\n");
}
