"use client";

import { useEffect, useState } from "react";
import { Check, ChevronsDownUp, ChevronsUpDown, Copy } from "lucide-react";
import { codeToHtml } from "shiki";
import { cn } from "@/lib/utils";

type CodeBlockProps = {
  code: string;
  language?: string;
  className?: string;
};

// Threshold for the "Show all" affordance — long files collapse to
// roughly the first ~20 lines with a fade gradient + button to
// expand. Matches Claude / ChatGPT canvas behavior. Short snippets
// always render full.
const COLLAPSE_THRESHOLD = 20;

export function CodeBlock({ code, language = "text", className }: CodeBlockProps) {
  const lineCount = code.split("\n").length;
  const isLong = lineCount > COLLAPSE_THRESHOLD;
  const [expanded, setExpanded] = useState(!isLong);
  const [html, setHtml] = useState<string>("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // github-dark / github-dark-dimmed are the de-facto standard
        // for AI-chat code blocks (Claude, ChatGPT, Vercel AI Elements).
        // Dark in both themes — code reads more naturally on dark even
        // when the surrounding chat is light.
        const out = await codeToHtml(code, {
          lang: language,
          theme: "github-dark-dimmed",
        });
        if (!cancelled) setHtml(out);
      } catch {
        if (!cancelled)
          setHtml(
            `<pre><code>${code
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")}</code></pre>`,
          );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, language]);

  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className={cn(
        "group/codeblock relative my-4 overflow-hidden rounded-lg border border-zinc-800 bg-[#22272e]",
        className,
      )}
    >
      {/* Header bar: language label (left), copy button (right). The
          dark zinc-900 backdrop matches GitHub's dark-dimmed theme so
          the header reads as part of the code block, not a separate
          chrome element. */}
      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900 px-3 py-1.5">
        <span className="font-mono text-[11px] uppercase tracking-wider text-zinc-400">
          {language}
          {isLong && (
            <span className="ml-2 normal-case tracking-normal text-zinc-500">
              · {lineCount} lines
            </span>
          )}
        </span>
        <div className="flex items-center gap-1">
          {isLong && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center gap-1 rounded px-2 py-0.5 text-[11px] text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
              aria-label={expanded ? "Collapse" : "Expand"}
            >
              {expanded ? (
                <>
                  <ChevronsDownUp className="size-3" /> Collapse
                </>
              ) : (
                <>
                  <ChevronsUpDown className="size-3" /> Show all
                </>
              )}
            </button>
          )}
          <button
            type="button"
            onClick={copy}
            className="flex items-center gap-1 rounded px-2 py-0.5 text-[11px] text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          >
            {copied ? (
              <>
                <Check className="size-3" />
                Copied
              </>
            ) : (
              <>
                <Copy className="size-3" />
                Copy
              </>
            )}
          </button>
        </div>
      </div>

      {/* Body: collapsed view shows the first ~20 lines with a fade
          mask at the bottom. Expanded view shows the whole thing. */}
      <div
        className={cn(
          "relative overflow-x-auto text-[13px] leading-6",
          "[&_pre]:bg-transparent! [&_pre]:p-4 [&_pre]:m-0!",
          "[&_code]:bg-transparent! [&_code]:font-mono",
          !expanded && "max-h-104 overflow-y-hidden",
        )}
        dangerouslySetInnerHTML={{
          __html:
            html ||
            `<pre><code>${code
              .replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#39;")}</code></pre>`,
        }}
      />

      {/* Fade-out mask + expand affordance shown only while collapsed. */}
      {isLong && !expanded && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="absolute inset-x-0 bottom-0 flex h-20 items-end justify-center bg-linear-to-t from-[#22272e] via-[#22272e]/90 to-transparent pb-3 text-[11px] font-medium text-zinc-300 hover:text-zinc-100 transition-colors"
        >
          <span className="rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1">
            Show all {lineCount} lines
          </span>
        </button>
      )}
    </div>
  );
}
