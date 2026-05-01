"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import { memo } from "react";
import { cn } from "@/lib/utils";
import { CodeBlock } from "./code-block";

const components: Components = {
  h1: ({ className, ...props }) => (
    <h1
      className={cn(
        "mt-6 mb-3 text-2xl font-semibold tracking-tight first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h2: ({ className, ...props }) => (
    <h2
      className={cn(
        "mt-6 mb-3 text-xl font-semibold tracking-tight first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }) => (
    <h3
      className={cn(
        "mt-5 mb-2 text-lg font-semibold tracking-tight first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  p: ({ className, ...props }) => (
    <p className={cn("leading-7 [&:not(:first-child)]:mt-4", className)} {...props} />
  ),
  a: ({ className, ...props }) => (
    <a
      className={cn(
        "font-medium text-primary underline underline-offset-4 decoration-primary/30 hover:decoration-primary",
        className,
      )}
      target="_blank"
      rel="noreferrer"
      {...props}
    />
  ),
  ul: ({ className, ...props }) => (
    <ul className={cn("my-3 ml-6 list-disc [&>li]:mt-1", className)} {...props} />
  ),
  ol: ({ className, ...props }) => (
    <ol className={cn("my-3 ml-6 list-decimal [&>li]:mt-1", className)} {...props} />
  ),
  blockquote: ({ className, ...props }) => (
    <blockquote
      className={cn(
        "mt-4 border-l-2 border-border pl-6 italic text-muted-foreground",
        className,
      )}
      {...props}
    />
  ),
  hr: ({ className, ...props }) => (
    <hr className={cn("my-6 border-border", className)} {...props} />
  ),
  table: ({ className, ...props }) => (
    <div className="my-4 w-full overflow-x-auto">
      <table
        className={cn("w-full border-collapse text-sm", className)}
        {...props}
      />
    </div>
  ),
  th: ({ className, ...props }) => (
    <th
      className={cn(
        "border border-border bg-muted/50 px-3 py-2 text-left font-medium",
        className,
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }) => (
    <td className={cn("border border-border px-3 py-2", className)} {...props} />
  ),
  code: ({ className, children, ...props }) => {
    const match = /language-(\w+)/.exec(className ?? "");
    const content = String(children ?? "").replace(/\n$/, "");
    const isInline = !match && !content.includes("\n");
    if (isInline) {
      return (
        <code
          className={cn(
            "rounded-md border bg-muted px-1.5 py-0.5 font-mono text-[0.9em]",
            className,
          )}
          {...props}
        >
          {children}
        </code>
      );
    }
    return <CodeBlock code={content} language={match?.[1] ?? "text"} />;
  },
  pre: ({ children }) => <>{children}</>,
};

// `rehypeRaw` keeps inline HTML, which is what we want for things like
// math + tables + Tailwind-styled elements the model might emit. But
// the design playbook's `<boltArtifact>` / `<boltAction>` tags also
// show up in two paths: (1) very briefly during streaming before the
// parser strips them, and (2) in saved conversation history, since the
// server persists the full assistant text. ReactMarkdown then tries to
// render them as React components and warns about unknown HTML elements.
//
// Plus any inline `<script>` the model wrote in design code (it sometimes
// dumps a sample) — React skips executing scripts in component trees
// and warns. Strip all three before rendering.
function stripDesignTags(content: string): string {
  return content
    // Whole boltArtifact + everything inside (case-insensitive).
    .replace(/<boltartifact\b[\s\S]*?<\/boltartifact>/gi, "")
    // Open boltArtifact that didn't close (mid-stream truncation).
    .replace(/<boltartifact\b[\s\S]*$/i, "")
    // Lone boltAction blocks not wrapped in artifact.
    .replace(/<boltaction\b[\s\S]*?<\/boltaction>/gi, "")
    .replace(/<boltaction\b[\s\S]*$/i, "")
    // Inline <script> tags in prose. The parser already redirects
    // module scripts to the bundle endpoint — but those run inside an
    // iframe, NOT in the chat thread. A script tag in CHAT prose is
    // either a sample the model dumped or a leftover from a partial
    // artifact. Either way, don't render it.
    .replace(/<script\b[\s\S]*?<\/script>/gi, "");
}

export const Markdown = memo(function Markdown({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  const safe = stripDesignTags(content);
  return (
    <div
      className={cn(
        "prose prose-neutral dark:prose-invert max-w-none text-[15px] leading-7",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeRaw, rehypeKatex]}
        components={components}
      >
        {safe}
      </ReactMarkdown>
    </div>
  );
});
