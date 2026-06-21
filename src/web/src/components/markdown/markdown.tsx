"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { memo } from "react";
import { cn } from "@/lib/utils";
import { CodeBlock } from "./code-block";

// React 19 warns when a `key` prop is included in a spread (`{...stripKey(props)}`)
// because it's the runtime's intrinsic — must be passed directly. The
// `react-markdown` v9 component signature passes `key` through the same
// props object we destructure, so a naive `{...stripKey(props)}` re-spread trips
// the warning every time a list / paragraph / link renders. Strip it
// before spreading. The actual key the parent fragment uses is preserved
// — we're just removing the warning-trigger from the prop bag.
function stripKey<T extends Record<string, unknown>>(p: T) {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { key, ...rest } = p as T & { key?: unknown };
  return rest;
}

const components: Components = {
  h1: ({ className, ...props }) => (
    <h1
      className={cn(
        "mt-6 mb-3 text-2xl font-semibold tracking-tight first:mt-0",
        className,
      )}
      {...stripKey(props)}
    />
  ),
  h2: ({ className, ...props }) => (
    <h2
      className={cn(
        "mt-6 mb-3 text-xl font-semibold tracking-tight first:mt-0",
        className,
      )}
      {...stripKey(props)}
    />
  ),
  h3: ({ className, ...props }) => (
    <h3
      className={cn(
        "mt-5 mb-2 text-lg font-semibold tracking-tight first:mt-0",
        className,
      )}
      {...stripKey(props)}
    />
  ),
  p: ({ className, ...props }) => (
    <p className={cn("leading-7 [&:not(:first-child)]:mt-4", className)} {...stripKey(props)} />
  ),
  a: ({ className, children, ...props }) => {
    // Citation refs come through as a numeric-only link text:
    // `[1]` → <a>1</a>, `[12]` → <a>12</a>. Detect that shape and
    // render the link as a Perplexity-style inline superscript chip
    // — small, monospace, no underline. Everything else stays a
    // normal underlined link. The detection has to look at React's
    // children as a string since the renderer hasn't materialized
    // the DOM yet.
    const innerText = String(
      Array.isArray(children) ? children.join("") : (children ?? ""),
    ).trim();
    const isCitation = /^\d{1,3}$/.test(innerText);
    if (isCitation) {
      return (
        <a
          className={cn(
            "mx-0.5 inline-flex items-center justify-center align-super",
            "h-4 min-w-4 rounded-full px-1",
            "bg-primary/15 text-[10px] font-mono font-medium tabular-nums text-primary",
            "no-underline hover:bg-primary/25 transition-colors",
          )}
          target="_blank"
          rel="noreferrer"
          {...stripKey(props)}
        >
          {children}
        </a>
      );
    }
    return (
      <a
        className={cn(
          "font-medium text-primary underline underline-offset-4 decoration-primary/30 hover:decoration-primary",
          className,
        )}
        target="_blank"
        rel="noreferrer"
        {...stripKey(props)}
      >
        {children}
      </a>
    );
  },
  ul: ({ className, ...props }) => (
    <ul className={cn("my-3 ml-6 list-disc [&>li]:mt-1", className)} {...stripKey(props)} />
  ),
  ol: ({ className, ...props }) => (
    <ol className={cn("my-3 ml-6 list-decimal [&>li]:mt-1", className)} {...stripKey(props)} />
  ),
  blockquote: ({ className, ...props }) => (
    <blockquote
      className={cn(
        "mt-4 border-l-2 border-border pl-6 italic text-muted-foreground",
        className,
      )}
      {...stripKey(props)}
    />
  ),
  hr: ({ className, ...props }) => (
    <hr className={cn("my-6 border-border", className)} {...stripKey(props)} />
  ),
  table: ({ className, ...props }) => (
    <div className="my-4 w-full overflow-x-auto">
      <table
        className={cn("w-full border-collapse text-sm", className)}
        {...stripKey(props)}
      />
    </div>
  ),
  th: ({ className, ...props }) => (
    <th
      className={cn(
        "border border-border bg-muted/50 px-3 py-2 text-left font-medium",
        className,
      )}
      {...stripKey(props)}
    />
  ),
  td: ({ className, ...props }) => (
    <td className={cn("border border-border px-3 py-2", className)} {...stripKey(props)} />
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
          {...stripKey(props)}
        >
          {children}
        </code>
      );
    }
    return <CodeBlock code={content} language={match?.[1] ?? "text"} />;
  },
  pre: ({ children }) => <>{children}</>,
  // Generated images persist into assistant text as markdown `![alt](url)`
  // (chat/route.ts onFinish). rehype-sanitize's defaultSchema allows <img>
  // with relative src, so /api/media/<id> survives; constrain the size here to
  // match the live GeneratedImageCards render.
  img: ({ className, alt, ...props }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      alt={alt ?? ""}
      className={cn(
        "my-3 block w-full max-w-sm rounded-2xl border border-border/60 object-contain",
        className,
      )}
      {...stripKey(props)}
    />
  ),
};

// Sanitize schema extending defaultSchema so that:
// - Shiki syntax-highlight spans (style="color:…" + class names) survive.
// - rehype-katex spans/divs (class + style) survive.
// - The streaming caret (<span data-stream-caret>) survives (data-* on span).
// - Event handlers, <script>, <iframe>, and other XSS vectors are stripped
//   by the defaultSchema baseline (which allows none of those).
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    span: [
      ...(defaultSchema.attributes?.span ?? []),
      "className",
      "style",
      // streaming caret marker
      "dataStreamCaret",
    ],
    code: [...(defaultSchema.attributes?.code ?? []), "className", "style"],
    pre: [...(defaultSchema.attributes?.pre ?? []), "className", "style"],
    div: [...(defaultSchema.attributes?.div ?? []), "className", "style"],
    // KaTeX wraps math in <math> and uses annotation/semantics/mrow etc.
    // defaultSchema doesn't list these; allow them through with safe attrs.
    math: ["xmlns", "display"],
    annotation: ["encoding"],
    // Allow SVG-namespace math markup if KaTeX falls back to it
    svg: ["xmlns", "viewBox", "width", "height", "aria-hidden", "focusable"],
  },
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
// State-machine strip: walks the text character by character,
// tracking which bolt block (artifact / action / results / plan /
// verify) we're currently inside. Only emits characters when we're
// OUTSIDE every block. This is what the streaming message-parser
// already does; doing it here too means the renderer NEVER sees
// boltArtifact-internal content even if the model emitted malformed
// tags, nested actions, unbalanced closers, content-after-close,
// markdown fences mixed with bolt markers, or any other shape.
//
// Replaces a long chain of regex strips. Regex on raw text was
// fragile: every model output had a slightly different shape, each
// new regex iteration revealed a new edge case (multi-line attrs,
// continuation lines, fragment leftovers). The state machine is
// robust by construction — it doesn't care about what's INSIDE
// a block; only about block boundaries.
//
// Recognized blocks (case-insensitive open + close tags):
//   boltartifact, boltaction, boltactionresults, jarvisplan, jarvisverify
function stripBlocksByStateMachine(content: string): string {
  const BLOCKS = [
    "boltartifact",
    "boltaction",
    "boltactionresults",
    "jarvisplan",
    "jarvisverify",
  ];
  const lower = content.toLowerCase();
  let out = "";
  let i = 0;
  // Stack of open block names — handles nested boltAction inside
  // boltArtifact correctly. We're "outside" only when stack is empty.
  const stack: string[] = [];

  while (i < content.length) {
    if (content[i] === "<") {
      // Try to match an open or close tag for any known block.
      let matched = false;
      for (const tag of BLOCKS) {
        // Open tag: `<tag` followed by space or `>` (after the b in tag).
        if (lower.startsWith("<" + tag, i)) {
          const after = lower.charCodeAt(i + 1 + tag.length);
          // boundary char: space, >, /, tab, newline, or end
          const isBoundary =
            after === 32 || after === 62 || after === 47 ||
            after === 9 || after === 10 || after === 13 ||
            Number.isNaN(after);
          if (isBoundary) {
            // Skip until '>' (or end). This consumes the entire open tag.
            const close = content.indexOf(">", i);
            if (close < 0) {
              // No closing `>` — model truncated mid-tag. Drop rest.
              return out;
            }
            stack.push(tag);
            i = close + 1;
            matched = true;
            break;
          }
        }
        // Close tag: `</tag>` (boundary after t).
        if (lower.startsWith("</" + tag, i)) {
          const after = lower.charCodeAt(i + 2 + tag.length);
          const isBoundary =
            after === 32 || after === 62 || after === 9 ||
            after === 10 || after === 13 || Number.isNaN(after);
          if (isBoundary) {
            const close = content.indexOf(">", i);
            if (close < 0) return out;
            // Pop the matching tag from the stack (or any if mismatched).
            const idx = stack.lastIndexOf(tag);
            if (idx >= 0) stack.splice(idx, 1);
            else stack.length = 0; // unbalanced — reset, skip the tag
            i = close + 1;
            matched = true;
            break;
          }
        }
      }
      if (matched) continue;
    }
    // Outside any block — emit the character.
    if (stack.length === 0) {
      out += content[i];
    }
    i++;
  }
  return out;
}

function stripDesignTags(content: string): string {
  let s = stripBlocksByStateMachine(content);

  // Defensive cleanups for content that escaped the bolt blocks
  // (legacy DB rows, model hallucinations). These are CHEAP regex
  // passes applied to whatever the state machine emitted.
  //
  // Architecture decision: we DELIBERATELY do NOT try to strip raw
  // JSX/HTML that the model emits OUTSIDE bolt blocks. Past attempts
  // (line-based filters, multi-line tag matchers, JSX expression
  // strippers) over-stripped legitimate prose — every model output
  // shape was different and each fix revealed a new edge case. The
  // RIGHT fix for "model leaked JSX outside boltAction" is the
  // system prompt instruction (jarvis-prompt.ts: "NO MARKDOWN CODE
  // FENCES IN CHAT"), not a render-time regex. If the model still
  // leaks despite the prompt, that's a model-behavior issue to fix
  // upstream, not a chat-display issue to fix downstream.
  s = s
    // <preview>...</preview> — model hallucination from training data.
    .replace(/<preview\b[\s\S]*?<\/preview>/gi, "")
    .replace(/<preview\b[\s\S]*$/i, "")
    // Inline <script> — never executable in chat anyway, just noise.
    .replace(/<script\b[\s\S]*?<\/script>/gi, "")
    // <boltActionResults> + nested <result>/<command>/<stdout>/<stderr>/<note>:
    // synthetic tool-feedback blocks the chat layer appends to assistant
    // messages so the model can read its own command output on the next
    // turn. Stripped from streaming output by the parser, but persisted
    // history goes straight from DB → ReactMarkdown — and rehypeRaw
    // would otherwise try to render <command>, <stdout>, etc. as custom
    // elements and warn. Drop the whole block here.
    .replace(/<boltactionresults\b[\s\S]*?<\/boltactionresults>/gi, "")
    .replace(/<boltactionresults\b[\s\S]*$/i, "")
    // <jarvisPlan> blocks — surfaced via the PlanCard component, not
    // the Markdown body. Stripped during streaming; this catches the
    // persisted-history path the same way as the results block above.
    .replace(/<jarvisplan\b[\s\S]*?<\/jarvisplan>/gi, "")
    .replace(/<jarvisplan\b[\s\S]*$/i, "")
    // <jarvisVerify> blocks — synthetic verify-pass output the chat
    // layer appends after a turn drains. Rendered separately by a
    // VerifyCard-style component (or just dropped from the visible
    // body). Same DB-persisted history pattern as boltActionResults.
    .replace(/<jarvisverify\b[\s\S]*?<\/jarvisverify>/gi, "")
    .replace(/<jarvisverify\b[\s\S]*$/i, "")
    // <preview>...</preview> — sometimes the model wraps its output
    // in a `preview` tag (likely a hallucinated leftover from
    // training data). It has no semantic meaning to the runtime and
    // rehypeRaw tries to render it as a custom element, triggering
    // the "tag <preview> is unrecognized" console warning. Drop it.
    .replace(/<preview\b[\s\S]*?<\/preview>/gi, "")
    .replace(/<preview\b[\s\S]*$/i, "")
    // Inline <script> tags in prose. The parser already redirects
    // module scripts to the bundle endpoint — but those run inside an
    // iframe, NOT in the chat thread. A script tag in CHAT prose is
    // either a sample the model dumped or a leftover from a partial
    // artifact. Either way, don't render it.
    .replace(/<script\b[\s\S]*?<\/script>/gi, "")
    // Auto-continue synthetic prompt — the chat layer's plumbing
    // when finish=length truncates a turn. We now skip persisting it
    // (chat/route.ts), but legacy DB rows may still have it. Strip
    // from RENDER so refreshes don't surface internal stage
    // direction next to the user's real messages. Match the canary
    // prefix and the closing instruction so we only catch this exact
    // synthetic content, not a user who happened to type the words.
    .replace(
      /Continue your previous output exactly where you stopped[\s\S]*?Close any open boltAction[^.]*\.?/g,
      "",
    )
    // Generic JSX-component tag drop: any `<Name…>` or `</Name>` whose
    // name contains an uppercase letter is a JSX component reference
    // (AnimatePresence, motion.div, Hero, Footer, Section, etc.) that
    // the model has dumped into prose while explaining code. HTML
    // element names are always lowercase, so the uppercase test is a
    // reliable JSX-vs-HTML discriminator. We strip the WRAPPER tags
    // only — the inner content is kept, since it's usually
    // human-readable prose the user still wants to see. Without this
    // strip rehypeRaw renders these as unknown custom elements and
    // React 19 fires "tag <foo> is unrecognized" for every one.
    //
    // Self-closing tags (`<motion.div />`) and dotted tags
    // (`<motion.div>` → DOM lowercases to `<motion.div>` which
    // browsers reject as malformed; the regex below catches those
    // by allowing `.` in the name segment).
    .replace(/<\/?([a-zA-Z][a-zA-Z0-9.]*)\b[^>]*>/g, (match, name: string) => {
      // JSX components: name has an uppercase letter (Hero, AnimatePresence)
      // OR a dot (motion.div, Disclosure.Panel). Both are invalid HTML
      // tag names and trip the "unrecognized tag" warning.
      const isJsxComponent = /[A-Z]/.test(name) || name.includes(".");
      return isJsxComponent ? "" : match;
    });
  return s;
}

// Split a markdown string into top-level "blocks" — sequences
// separated by blank lines, with fenced code blocks (``` … ```) kept
// intact even when they contain blank lines internally. This is the
// streamdown.ai pattern: render each block as its own memoized
// ReactMarkdown subtree so during streaming, only the LAST (growing)
// block re-renders. Earlier blocks become referentially stable
// strings and React.memo bails out, which is the difference between
// a thread that stutters at 1k+ tokens and one that doesn't.
//
// Conservative on purpose: we don't try to be smart about list
// continuations or table rows. A block is a paragraph-delimited
// chunk, fenced code blocks excepted. ReactMarkdown sees each chunk
// in isolation, so any cross-block markdown construct (e.g. a list
// interrupted by a blank line) renders as two separate lists. That's
// a tiny visual cost compared to the per-token re-render savings.
function splitBlocks(content: string): string[] {
  const blocks: string[] = [];
  let i = 0;
  let buf = "";
  let inFence = false;
  let fenceMarker = "";
  const lines = content.split("\n");
  for (i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fenceMatch = /^(\s*)(```+|~~~+)/.exec(line);
    if (fenceMatch) {
      const marker = fenceMatch[2];
      if (!inFence) {
        inFence = true;
        fenceMarker = marker;
      } else if (line.trimStart().startsWith(fenceMarker)) {
        inFence = false;
        fenceMarker = "";
      }
    }
    if (!inFence && line.trim() === "" && buf.length > 0) {
      // Blank line outside a fence ends the current block.
      blocks.push(buf);
      buf = "";
      continue;
    }
    buf += (buf.length > 0 ? "\n" : "") + line;
  }
  if (buf.length > 0) blocks.push(buf);
  return blocks;
}

// Per-block memoized renderer. memo() bails out when `content` is
// referentially equal — and since splitBlocks returns the SAME string
// (cached by its substring of the parent) for completed blocks across
// renders, those subtrees stop re-rendering during streaming.
const Block = memo(function Block({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema], rehypeKatex]}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
});

export const Markdown = memo(function Markdown({
  content,
  className,
  // When true, append an inline blinking caret to the LAST block's
  // last paragraph so the cursor sits flush against the most-recent
  // character — instead of dropping below the prose as a stray
  // siblings of <Markdown> would. Implemented by emitting a raw
  // <span data-stream-caret> at the end of the last block's content;
  // rehypeRaw passes it through as an inline element so it joins the
  // last paragraph's last line, and CSS styles + animates it.
  isStreaming,
}: {
  content: string;
  className?: string;
  isStreaming?: boolean;
}) {
  const safe = stripDesignTags(content);
  const blocks = splitBlocks(safe);
  // Tail-block streaming caret. We mutate ONLY the last block so all
  // earlier (settled) blocks stay referentially identical across
  // renders — the per-block memo bailout keeps holding.
  const blocksWithCaret =
    isStreaming && blocks.length > 0
      ? [
          ...blocks.slice(0, -1),
          blocks[blocks.length - 1] + '<span data-stream-caret></span>',
        ]
      : blocks;
  return (
    // 15px body / 1.7 line-height — the Claude/ChatGPT reading rhythm.
    // Tailwind's `leading-7` is 1.75rem absolute (so 1.75 / 0.94 at
    // 15px ≈ 1.86, too loose). Custom `[1.7]` lands at the visually
    // intentional value the research surfaced. `prose` resets paragraph
    // margins; `[&_p]:my-3` reasserts a tighter rhythm than prose's
    // default margin so paragraph spacing matches the inter-turn gap.
    <div
      className={cn(
        "prose prose-neutral dark:prose-invert max-w-none text-[15px] leading-[1.7]",
        "[&_p]:my-4 [&_ul]:my-3 [&_ol]:my-3 [&_li]:my-1 [&_h1]:mt-8 [&_h2]:mt-7 [&_h3]:mt-6",
        className,
      )}
    >
      {blocksWithCaret.map((b, i) => (
        <Block key={i} content={b} />
      ))}
    </div>
  );
});
