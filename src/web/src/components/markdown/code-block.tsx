"use client";

import { useEffect, useState } from "react";
import { Check, Code2, Copy, X } from "lucide-react";
import { codeToHtml } from "shiki";
import { cn } from "@/lib/utils";

type CodeBlockProps = {
  code: string;
  language?: string;
  className?: string;
};

// Long code blocks (>10 lines) collapse to a TINY one-line pill in the
// chat thread — `<code> · html · 142 lines`. Tap opens a popover with
// the actual code. Short snippets (<=10 lines) render as a small framed
// block so quick examples still inline naturally.
//
// In design mode the model writes complete files; we don't want those
// occupying half the chat. The file panel + preview are the right place
// for full code; chat stays a conversation.
const PILL_LINES = 10;

export function CodeBlock({ code, language = "text", className }: CodeBlockProps) {
  const lineCount = code.split("\n").length;
  const isLong = lineCount > PILL_LINES;
  const [open, setOpen] = useState(false);
  const [html, setHtml] = useState<string>("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    // Highlight only when needed: short blocks always, long blocks only
    // once the user opens the popover. Saves work on cold-load.
    if (isLong && !open) return;
    let cancelled = false;
    (async () => {
      try {
        const out = await codeToHtml(code, {
          lang: language,
          theme: "one-dark-pro",
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
  }, [code, language, isLong, open]);

  const copy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // Compact pill view for long blocks. Click to view the code in an
  // overlay; copy button always available without opening.
  if (isLong) {
    return (
      <>
        <span
          className={cn(
            "my-1.5 inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-muted/50 px-2.5 py-1 text-[11px] text-muted-foreground hover:bg-muted/80 cursor-pointer align-middle",
            className,
          )}
          role="button"
          tabIndex={0}
          onClick={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen(true);
            }
          }}
          title="View code"
        >
          <Code2 className="size-3" />
          <span className="font-mono lowercase">{language}</span>
          <span className="text-muted-foreground/60">· {lineCount} lines</span>
        </span>
        {open && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
            role="dialog"
            aria-modal
            onClick={() => setOpen(false)}
          >
            <div
              className="w-full max-w-3xl max-h-[80vh] overflow-hidden rounded-xl border bg-card shadow-2xl flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between border-b bg-muted/60 px-4 py-2">
                <span className="text-xs font-mono text-muted-foreground lowercase">
                  {language} · {lineCount} lines
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={copy}
                    className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-muted"
                  >
                    {copied ? (
                      <>
                        <Check className="size-3" /> Copied
                      </>
                    ) : (
                      <>
                        <Copy className="size-3" /> Copy
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    className="text-muted-foreground hover:text-foreground rounded p-1 hover:bg-muted"
                    aria-label="Close"
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              </div>
              <div
                className="flex-1 overflow-auto text-sm [&_pre]:bg-transparent! [&_pre]:p-4 [&_pre]:m-0! [&_code]:bg-transparent!"
                dangerouslySetInnerHTML={{
                  __html: html || `<pre><code>${code}</code></pre>`,
                }}
              />
            </div>
          </div>
        )}
      </>
    );
  }

  // Short code block — render inline as before.
  return (
    <div
      className={cn(
        "group relative my-3 overflow-hidden rounded-lg border bg-muted/40",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b bg-muted/60 px-3 py-1">
        <span className="text-[11px] font-mono text-muted-foreground lowercase">
          {language}
        </span>
        <button
          type="button"
          onClick={copy}
          className="text-[11px] text-muted-foreground opacity-0 hover:text-foreground group-hover:opacity-100 flex items-center gap-1"
        >
          {copied ? (
            <>
              <Check className="size-3" /> Copied
            </>
          ) : (
            <>
              <Copy className="size-3" /> Copy
            </>
          )}
        </button>
      </div>
      <div
        className="overflow-x-auto text-sm [&_pre]:bg-transparent! [&_pre]:p-3 [&_pre]:m-0! [&_code]:bg-transparent!"
        dangerouslySetInnerHTML={{ __html: html || `<pre><code>${code}</code></pre>` }}
      />
    </div>
  );
}
