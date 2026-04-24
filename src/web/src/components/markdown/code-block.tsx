"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { codeToHtml } from "shiki";
import { cn } from "@/lib/utils";

type CodeBlockProps = {
  code: string;
  language?: string;
  className?: string;
};

export function CodeBlock({ code, language = "text", className }: CodeBlockProps) {
  const [html, setHtml] = useState<string>("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const out = await codeToHtml(code, {
          lang: language,
          themes: { light: "github-light", dark: "github-dark" },
          defaultColor: false,
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
        "group relative my-4 overflow-hidden rounded-xl border bg-muted/40",
        className,
      )}
    >
      <div className="flex items-center justify-between border-b bg-muted/60 px-4 py-1.5">
        <span className="text-xs font-mono text-muted-foreground lowercase">
          {language}
        </span>
        <button
          type="button"
          onClick={copy}
          className="text-xs text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100 flex items-center gap-1"
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
        className="overflow-x-auto text-sm [&_pre]:!bg-transparent [&_pre]:p-4 [&_code]:!bg-transparent"
        dangerouslySetInnerHTML={{ __html: html || `<pre><code>${code}</code></pre>` }}
      />
    </div>
  );
}
