// Web tools: fetch a URL and search the web.
// web_search uses DuckDuckGo's HTML endpoint (no API key needed).

import type { ToolRunner } from "../types.ts";

const MAX_OUTPUT = 16_384;

function truncate(s: string, limit = MAX_OUTPUT): string {
  return s.length > limit ? s.slice(0, limit) + `\n[truncated; original ${s.length} chars]` : s;
}

function stripHtml(html: string): string {
  return html
    .replace(/<script\b[\s\S]*?<\/script>/gi, "")
    .replace(/<style\b[\s\S]*?<\/style>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

export const webFetchTool: ToolRunner = {
  def: {
    name: "web_fetch",
    description: "Fetch a URL. Returns response text (HTML stripped to plain text for text/html). Output truncated to 16KB.",
    input_schema: {
      type: "object",
      properties: {
        url: { type: "string", description: "Absolute http(s) URL" },
        method: { type: "string", description: "HTTP method (default GET)" },
        headers: { type: "object", description: "Extra headers as a key-value object" },
      },
      required: ["url"],
    },
  },
  async run(input: unknown) {
    const { url, method = "GET", headers } = input as {
      url: string; method?: string; headers?: Record<string, string>;
    };
    if (typeof url !== "string" || !/^https?:\/\//.test(url)) {
      return { output: "web_fetch: url must be http(s)", is_error: true };
    }
    try {
      const r = await fetch(url, {
        method,
        headers: { "user-agent": "misty-core/1.0", ...(headers ?? {}) },
        redirect: "follow",
      });
      const ct = r.headers.get("content-type") ?? "";
      const body = await r.text();
      const text = ct.includes("text/html") ? stripHtml(body) : body;
      return { output: `HTTP ${r.status} ${r.statusText} (${ct})\n${truncate(text)}` };
    } catch (err) {
      return { output: `web_fetch: ${err instanceof Error ? err.message : String(err)}`, is_error: true };
    }
  },
};

export const webSearchTool: ToolRunner = {
  def: {
    name: "web_search",
    description: "Search the web (DuckDuckGo). Returns a list of result titles + URLs + snippets.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
        limit: { type: "number", description: "Max results (default 10, max 20)" },
      },
      required: ["query"],
    },
  },
  async run(input: unknown) {
    const { query, limit } = input as { query: string; limit?: number | string };
    if (typeof query !== "string" || query.length === 0) {
      return { output: "web_search: query is required", is_error: true };
    }
    const limitNum = typeof limit === "string" ? Number(limit) : (limit ?? 10);
    const n = Math.min(Math.max(1, Number.isFinite(limitNum) ? limitNum : 10), 20);
    try {
      const r = await fetch(`https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`, {
        method: "POST",
        headers: { "user-agent": "Mozilla/5.0 (misty-core)" },
      });
      const html = await r.text();
      // Parse result blocks. DDG HTML results have anchor class "result__a" + snippet "result__snippet".
      const results: { title: string; url: string; snippet: string }[] = [];
      const anchorRe = /<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
      const snippetRe = /<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)<\/a>/g;
      const urls: { url: string; title: string }[] = [];
      let m;
      while ((m = anchorRe.exec(html)) !== null) {
        // DDG wraps outbound URLs like /l/?uddg=https%3A%2F%2F...
        let u = m[1]!;
        const uddg = u.match(/[?&]uddg=([^&]+)/);
        if (uddg) u = decodeURIComponent(uddg[1]!);
        urls.push({ url: u, title: stripHtml(m[2]!) });
      }
      const snippets: string[] = [];
      while ((m = snippetRe.exec(html)) !== null) snippets.push(stripHtml(m[1]!));
      for (let i = 0; i < Math.min(urls.length, n); i++) {
        results.push({ title: urls[i]!.title, url: urls[i]!.url, snippet: snippets[i] ?? "" });
      }
      if (results.length === 0) return { output: "(no results)" };
      const formatted = results.map((r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${r.snippet}`).join("\n\n");
      return { output: truncate(formatted) };
    } catch (err) {
      return { output: `web_search: ${err instanceof Error ? err.message : String(err)}`, is_error: true };
    }
  },
};
