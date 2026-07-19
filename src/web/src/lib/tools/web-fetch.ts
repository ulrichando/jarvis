import { tool } from "ai";
import { z } from "zod";
import {
  decodeEntities,
  extractMainText,
  isPubliclyRoutableUrl,
  stripTags,
  truncateAtWord,
} from "@/lib/search/core";

// webFetch — read ONE specific URL the user pasted (companion to webSearch,
// which is for open-ended queries). Fetches server-side, strips the page to
// readable text with the same dependency-free readability-lite extractor the
// search pipeline uses (core.ts::extractMainText), and returns
// { url, title, text } — or { url, error } on any failure. execute NEVER
// throws: a thrown tool error would surface to the model as a hard tool
// failure instead of an explainable result.
//
// SSRF: core.ts::isPubliclyRoutableUrl gates BOTH the initial URL and every
// redirect hop (redirects are followed manually so a public URL can't bounce
// the fetch into localhost / RFC1918 / the cloud metadata IP). Non-http(s)
// schemes are rejected by the same guard before any fetch happens.
//
// PDF text extraction is NOT available yet — no PDF library is installed in
// src/web and this deliberately doesn't add one. application/pdf responses
// return an explanatory error instead of binary soup.

const FETCH_TIMEOUT_MS = 8_000; // one budget across all redirect hops
const MAX_TEXT_CHARS = 10_000; // returned-text cap (truncated at a word)
const MAX_RESPONSE_BYTES = 5_000_000; // reject before buffering (same pattern as /api/stt content-length check)
const MAX_REDIRECTS = 5;

// Browser-ish UA — same shape the search pipeline's page fetcher sends, so
// pages that block "curl-looking" clients still serve HTML.
const USER_AGENT =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

const inputSchema = z.object({
  // Plain string (not z.string().url()): an invalid URL must come back as a
  // { url, error } RESULT the model can relay, not a zod input-validation
  // failure. isPubliclyRoutableUrl already rejects unparseable URLs.
  url: z
    .string()
    .describe("The exact http(s) URL to fetch, as the user provided it"),
});

export type WebFetchResult =
  | { url: string; title: string; text: string }
  | { url: string; error: string };

function extractTitle(html: string): string {
  const m = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html.slice(0, 200_000));
  if (!m) return "";
  return decodeEntities(stripTags(m[1])).replace(/\s+/g, " ").trim().slice(0, 300);
}

/**
 * Fetch one URL and reduce it to readable text. Exported for reuse (e.g. a
 * voice-search route) and for direct unit testing.
 */
export async function fetchUrlAsText(url: string): Promise<WebFetchResult> {
  if (!isPubliclyRoutableUrl(url)) {
    return {
      url,
      error:
        "Blocked: only public http(s) URLs can be fetched (private, loopback, link-local and metadata addresses, and non-http schemes, are refused).",
    };
  }

  try {
    // One shared timeout for the whole fetch, redirects included. Guarded:
    // AbortSignal.timeout needs node ≥17.3 / any modern runtime — degrade to
    // no timeout rather than crash where it's missing (e.g. old jsdom).
    const signal =
      typeof AbortSignal.timeout === "function"
        ? AbortSignal.timeout(FETCH_TIMEOUT_MS)
        : undefined;

    // Manual redirect walk: `redirect: "follow"` would happily follow a
    // public URL into http://127.0.0.1/... — re-check every hop instead.
    let current = url;
    let res: {
      ok: boolean;
      status: number;
      headers: Headers;
      text(): Promise<string>;
    } | null = null;
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
      if (!isPubliclyRoutableUrl(current)) {
        return {
          url,
          error: `Blocked: the page redirected to a non-public address (${current}).`,
        };
      }
      const r = await fetch(current, {
        headers: {
          "User-Agent": USER_AGENT,
          Accept:
            "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.1",
          "Accept-Language": "en-US,en;q=0.9",
        },
        redirect: "manual",
        signal,
      });
      if (r.status >= 300 && r.status < 400) {
        const loc = r.headers.get("location");
        if (loc) {
          current = new URL(loc, current).toString();
          continue;
        }
      }
      res = r;
      break;
    }
    if (!res) return { url, error: `Gave up after ${MAX_REDIRECTS} redirects.` };
    if (!res.ok) return { url, error: `HTTP ${res.status}` };

    const ctype = (res.headers.get("content-type") ?? "").toLowerCase();
    if (ctype.includes("application/pdf")) {
      return {
        url,
        error:
          "This URL is a PDF — PDF text extraction isn't available yet. Ask the user to paste the relevant text instead.",
      };
    }
    if (/^(?:image|audio|video|font)\//.test(ctype) || ctype.includes("octet-stream")) {
      return { url, error: `Not a readable text page (content-type: ${ctype}).` };
    }

    // Reject oversize responses on the declared length BEFORE res.text()
    // buffers the whole body into memory (same pattern as /api/stt).
    const clen = parseInt(res.headers.get("content-length") ?? "", 10);
    if (Number.isFinite(clen) && clen > MAX_RESPONSE_BYTES) {
      return {
        url,
        error: `Page too large to fetch (${clen} bytes; limit is ${MAX_RESPONSE_BYTES}).`,
      };
    }

    const body = await res.text();
    // Belt-and-suspenders for chunked responses that omit content-length.
    if (body.length > MAX_RESPONSE_BYTES) {
      return {
        url,
        error: `Page too large to fetch (over ${MAX_RESPONSE_BYTES} bytes).`,
      };
    }

    // Absent content-type → assume HTML (what real pages overwhelmingly are).
    const isHtml =
      !ctype || ctype.includes("text/html") || ctype.includes("application/xhtml");
    const title = isHtml ? extractTitle(body) : "";
    const text = isHtml
      ? extractMainText(body, MAX_TEXT_CHARS)
      : truncateAtWord(
          body.replace(/\r\n/g, "\n").replace(/[ \t]+/g, " ").trim(),
          MAX_TEXT_CHARS,
        );
    if (!text) return { url, error: "No readable text found at this URL." };
    return { url, title, text };
  } catch (err) {
    const name = err instanceof Error ? err.name : "";
    if (name === "TimeoutError" || name === "AbortError") {
      return {
        url,
        error: `Timed out after ${FETCH_TIMEOUT_MS / 1000}s fetching the page.`,
      };
    }
    return {
      url,
      error: `Fetch failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

export const webFetchTool = tool({
  description:
    "Fetch and read the full text of a specific web page or PDF the user provided. Use when the user pastes/links a URL and wants its contents — NOT for open-ended search (use webSearch for that).",
  inputSchema,
  execute: async ({ url }) => fetchUrlAsText(url.trim()),
});
