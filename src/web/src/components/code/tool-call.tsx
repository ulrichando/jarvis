"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileCode,
  FileText,
  ListChecks,
  Pencil,
  Search,
  Terminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { CodeBlock } from "@/components/markdown/code-block";

// Inline rendering of a CLI tool call (claude.ai/code-style): a file Write
// shows its path + the actual code, Edit shows a red/green diff, Bash shows the
// command + its output. The data already lives in the transcript (assistant
// `tool_use` blocks + the paired `tool_result`); this is purely how we present
// it instead of the old bare "⚙ Write".
//
// View modes:
//   summary → headers only (one-line trace), no bodies.
//   normal  → the tool's ACTION (Write→code, Edit→diff, Bash→command+output);
//             reads/searches stay one-liners and file-op confirmations hide.
//   verbose → normal PLUS every tool's raw result/output (Read file contents,
//             "file created" confirmations, search matches, untruncated), with
//             all bodies expanded.

export type ToolUse = { id: string; name: string; input: Record<string, unknown> };
export type ToolResult = { text: string; isError: boolean };
type ViewMode = "normal" | "verbose" | "summary";

const EXT_LANG: Record<string, string> = {
  ts: "typescript", tsx: "tsx", js: "javascript", jsx: "jsx", mjs: "javascript",
  py: "python", rb: "ruby", go: "go", rs: "rust", java: "java", c: "c", h: "c",
  cpp: "cpp", cc: "cpp", hpp: "cpp", cs: "csharp", php: "php", sh: "bash",
  bash: "bash", zsh: "bash", json: "json", yaml: "yaml", yml: "yaml",
  toml: "toml", md: "markdown", html: "html", css: "css", scss: "scss",
  sql: "sql", swift: "swift", kt: "kotlin", lua: "lua", r: "r", dart: "dart",
};

function extToLang(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANG[ext] ?? "text";
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** Strip the container's /workspace/<repo>/ prefix for a tidy display path. */
function relPath(p: string): string {
  return str(p).replace(/^\/workspace\/[^/]+\//, "") || str(p);
}

function lineCount(s: string): number {
  return s ? s.split("\n").length : 0;
}

const MAX_OUTPUT = 6000;

// Minimal old→new diff: removed lines (red) then added lines (green). Not an
// LCS diff — for a single Edit's old/new this reads clearly and stays cheap.
function MiniDiff({ oldText, newText }: { oldText: string; newText: string }) {
  const rows: Array<{ sign: "-" | "+"; line: string }> = [
    ...oldText.split("\n").map((line) => ({ sign: "-" as const, line })),
    ...newText.split("\n").map((line) => ({ sign: "+" as const, line })),
  ];
  return (
    <div className="overflow-x-auto rounded-md border border-border/60 font-mono text-[12px] leading-relaxed">
      {rows.map((r, i) => (
        <div
          key={i}
          className={
            r.sign === "-"
              ? "whitespace-pre bg-red-500/10 px-2 text-red-700 dark:text-red-300"
              : "whitespace-pre bg-emerald-500/10 px-2 text-emerald-700 dark:text-emerald-300"
          }
        >
          <span className="select-none opacity-50">{r.sign} </span>
          {r.line || " "}
        </div>
      ))}
    </div>
  );
}

function OutputBlock({ text, isError, full }: { text: string; isError: boolean; full?: boolean }) {
  const shown = !full && text.length > MAX_OUTPUT ? text.slice(0, MAX_OUTPUT) + "\n… (truncated — switch off Verbose or open the file)" : text;
  return (
    <pre
      className={`overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-muted/30 px-2.5 py-1.5 font-mono text-[12px] leading-relaxed ${
        isError ? "text-red-600 dark:text-red-300" : "text-muted-foreground"
      }`}
    >
      {shown}
    </pre>
  );
}

type Spec = {
  Icon: LucideIcon;
  verb: string;
  arg: string;
  /** The tool's action visualization (code / diff / checklist / JSON). The
   *  RESULT/output is layered on separately (Bash always, others in verbose). */
  body: React.ReactNode | null;
  /** Body is large → keep it collapsed by default in normal view. */
  long: boolean;
};

function buildSpec(name: string, input: Record<string, unknown>): Spec {
  switch (name) {
    case "Write":
    case "CreateFile": {
      const path = str(input.file_path ?? input.path);
      const content = str(input.content);
      return {
        Icon: FileCode,
        verb: "Write",
        arg: relPath(path),
        body: content ? <CodeBlock code={content} language={extToLang(path)} /> : null,
        long: lineCount(content) > 24,
      };
    }
    case "Edit":
    case "StrReplace": {
      const path = str(input.file_path ?? input.path);
      const oldText = str(input.old_string ?? input.old_str);
      const newText = str(input.new_string ?? input.new_str);
      return {
        Icon: Pencil,
        verb: "Edit",
        arg: relPath(path),
        body: oldText || newText ? <MiniDiff oldText={oldText} newText={newText} /> : null,
        long: lineCount(oldText) + lineCount(newText) > 24,
      };
    }
    case "MultiEdit": {
      const path = str(input.file_path ?? input.path);
      const edits = Array.isArray(input.edits) ? (input.edits as Array<Record<string, unknown>>) : [];
      return {
        Icon: Pencil,
        verb: `Edit (${edits.length})`,
        arg: relPath(path),
        body: edits.length ? (
          <div className="space-y-1.5">
            {edits.map((e, i) => (
              <MiniDiff key={i} oldText={str(e.old_string)} newText={str(e.new_string)} />
            ))}
          </div>
        ) : null,
        long: edits.length > 2,
      };
    }
    case "Bash":
      // Command is the header; the OUTPUT is layered on by the component.
      return { Icon: Terminal, verb: "$", arg: str(input.command), body: null, long: false };
    case "Read":
      return { Icon: FileText, verb: "Read", arg: relPath(str(input.file_path ?? input.path)), body: null, long: false };
    case "Glob":
      return { Icon: Search, verb: "Glob", arg: str(input.pattern), body: null, long: false };
    case "Grep":
      return { Icon: Search, verb: "Grep", arg: str(input.pattern), body: null, long: false };
    case "LS":
      return { Icon: FileText, verb: "List", arg: relPath(str(input.path)), body: null, long: false };
    case "TodoWrite": {
      const todos = Array.isArray(input.todos) ? (input.todos as Array<Record<string, unknown>>) : [];
      return {
        Icon: ListChecks,
        verb: "Plan",
        arg: `${todos.length} item${todos.length === 1 ? "" : "s"}`,
        body: todos.length ? (
          <ul className="space-y-0.5 text-[12.5px] text-muted-foreground">
            {todos.map((t, i) => {
              const status = str(t.status);
              const mark = status === "completed" ? "✓" : status === "in_progress" ? "▸" : "○";
              return (
                <li key={i} className={status === "completed" ? "line-through opacity-60" : ""}>
                  <span className="select-none">{mark} </span>
                  {str(t.content)}
                </li>
              );
            })}
          </ul>
        ) : null,
        long: todos.length > 8,
      };
    }
    default: {
      const firstStr = Object.values(input).find((v) => typeof v === "string") as string | undefined;
      return {
        Icon: Wrench,
        verb: name || "Tool",
        arg: firstStr ? String(firstStr).slice(0, 120) : "",
        body: Object.keys(input).length > 0 ? <CodeBlock code={JSON.stringify(input, null, 2)} language="json" /> : null,
        long: false,
      };
    }
  }
}

export function ToolCall({
  use,
  result,
  viewMode,
}: {
  use: ToolUse;
  result?: ToolResult;
  viewMode: ViewMode;
}) {
  const spec = buildSpec(use.name, use.input ?? {});
  const output = result?.text ?? "";
  // Normal shows output only for Bash (the command's result is the point);
  // Verbose shows the raw result/output for EVERY tool. (Summary: nothing.)
  const showOutput =
    !!output && viewMode !== "summary" && (viewMode === "verbose" || use.name === "Bash");
  const hasContent = spec.body != null || showOutput;
  const longContent = spec.long || output.length > 800;

  // Derive the open state from the view mode so toggling Normal⇄Verbose updates
  // existing cards live; a manual click overrides until the mode changes.
  const [override, setOverride] = useState<{ mode: ViewMode; open: boolean } | null>(null);
  const autoOpen = viewMode === "verbose" ? true : viewMode === "summary" ? false : !longContent;
  const open = override && override.mode === viewMode ? override.open : autoOpen;
  const expandable = viewMode !== "summary" && hasContent;
  const showBody = expandable && open;

  return (
    <div className="flex gap-2.5">
      <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500/70" />
      <div className="min-w-0 flex-1">
        <button
          type="button"
          disabled={!expandable}
          onClick={() => setOverride({ mode: viewMode, open: !open })}
          className="flex max-w-full items-center gap-1.5 text-left text-[13px] text-muted-foreground hover:text-foreground disabled:cursor-default disabled:hover:text-muted-foreground"
        >
          <spec.Icon className="size-3.5 shrink-0 opacity-70" />
          <span className="shrink-0 font-medium text-foreground/80">{spec.verb}</span>
          {spec.arg && (
            <span className="truncate font-mono text-[12px] text-muted-foreground">{spec.arg}</span>
          )}
          {result?.isError && <span className="shrink-0 text-[11px] text-red-500">error</span>}
          {expandable &&
            (open ? (
              <ChevronDown className="size-3 shrink-0 opacity-60" />
            ) : (
              <ChevronRight className="size-3 shrink-0 opacity-60" />
            ))}
        </button>
        {showBody && (
          <div className="mt-1.5 space-y-1.5">
            {spec.body}
            {showOutput && (
              <OutputBlock text={output} isError={!!result?.isError} full={viewMode === "verbose"} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
