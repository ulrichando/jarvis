"use client";

import { ExternalLink } from "lucide-react";
import type { UIMessage } from "ai";
import { cn } from "@/lib/utils";

type Source = {
  title: string;
  url: string;
  snippet?: string;
};

// Extract web-search results from a UIMessage's tool parts. The AI
// SDK serializes tool results as parts with type `tool-${toolName}`
// (or `dynamic-tool` for dynamically-shaped ones), with the JSON
// result on `.output` once the call completes. Only `webSearch` is
// recognized today; if other source-producing tools land later, add
// more extractors here.
export function extractSources(message: UIMessage): Source[] {
  const seen = new Set<string>();
  const out: Source[] = [];
  for (const p of message.parts) {
    const part = p as {
      type: string;
      output?: unknown;
      result?: unknown;
    };
    if (part.type !== "tool-webSearch") continue;
    const data = (part.output ?? part.result) as
      | { results?: { title?: string; url?: string; snippet?: string }[] }
      | undefined;
    const items = data?.results ?? [];
    for (const r of items) {
      if (!r?.url || !r?.title) continue;
      if (seen.has(r.url)) continue;
      seen.add(r.url);
      out.push({ title: r.title, url: r.url, snippet: r.snippet });
    }
  }
  return out;
}

function faviconFor(url: string): string {
  try {
    const u = new URL(url);
    // Google's favicon proxy — works without auth, caches well, no
    // CORS hassle. Falls back gracefully via onError below.
    return `https://www.google.com/s2/favicons?domain=${u.hostname}&sz=32`;
  } catch {
    return "";
  }
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/**
 * Sources strip — Perplexity-style row of numbered citation chips
 * rendered below an assistant message. Each chip shows the favicon
 * + host + a hover-revealed preview card with the result title and
 * snippet. Clicking opens the URL in a new tab.
 *
 * Numbering is 1-indexed, in the order the model received the
 * results — so an inline `[1]` reference in the body lines up with
 * the first chip below the body, matching the Perplexity / Bing
 * Chat / Claude Search convention.
 */
export function Sources({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-1.5">
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
        Sources
      </div>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((s, i) => (
          <SourceChip key={s.url} index={i + 1} source={s} />
        ))}
      </div>
    </div>
  );
}

function SourceChip({ index, source }: { index: number; source: Source }) {
  const fav = faviconFor(source.url);
  const host = hostOf(source.url);
  return (
    <a
      href={source.url}
      target="_blank"
      rel="noreferrer"
      // `group` enables the hover-reveal preview card. The card is
      // absolutely positioned so it doesn't shift layout, with a
      // small upward offset so it reads as anchored to the chip.
      className={cn(
        "group relative inline-flex items-center gap-1.5",
        "rounded-full border border-border bg-card/60 px-2 py-0.5",
        "text-[11.5px] text-foreground/85 transition-colors",
        "hover:border-border hover:bg-card hover:text-foreground",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
      )}
    >
      <span className="text-[10px] font-mono text-muted-foreground tabular-nums">
        {index}
      </span>
      {fav ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={fav}
          alt=""
          className="block size-3 rounded-sm"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.visibility = "hidden";
          }}
        />
      ) : null}
      <span className="max-w-[140px] truncate">{host}</span>

      {/* Hover preview card */}
      <span
        className={cn(
          "pointer-events-none absolute bottom-full left-0 z-20 mb-1.5",
          "w-72 rounded-lg border border-border bg-popover p-3 shadow-lg",
          "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100",
          "transition-opacity duration-150",
        )}
        role="tooltip"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="text-[12px] font-medium text-foreground line-clamp-2">
            {source.title}
          </div>
          <ExternalLink className="size-3 shrink-0 text-muted-foreground" />
        </div>
        <div className="mt-1 text-[10.5px] text-muted-foreground/80 truncate">
          {host}
        </div>
        {source.snippet && (
          <div className="mt-1.5 text-[11.5px] leading-snug text-muted-foreground line-clamp-3">
            {source.snippet}
          </div>
        )}
      </span>
    </a>
  );
}
