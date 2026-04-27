"use client";

import Link from "next/link";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  CheckCircle2,
  XCircle,
  FileCode,
  Terminal,
  Play,
  ExternalLink,
  Download,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { javascript } from "@codemirror/lang-javascript";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { oneDark } from "@codemirror/theme-one-dark";
import type { Extension } from "@codemirror/state";
import type { TrackedAction, ArtifactData, Action } from "@/lib/actions/types";
import { cn } from "@/lib/utils";

// Avoid SSR for CodeMirror — it touches `document`.
const CodeMirror = dynamic(() => import("@uiw/react-codemirror"), { ssr: false });

function downloadFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

type ArtifactCard = {
  artifact: ArtifactData;
  actions: TrackedAction[];
};

type Props = {
  artifacts: Map<string, ArtifactCard>;
  workspaceId: string | null;
  workspaceName: string | null;
  previewPort?: number | null;
  embedded?: boolean;
};

export function ArtifactPanel({
  artifacts,
  workspaceId,
  workspaceName,
  previewPort,
  embedded = false,
}: Props) {
  if (artifacts.size === 0) return null;
  return (
    <div className="space-y-2 mt-3">
      {[...artifacts.values()].map((card) => (
        <ArtifactCardView
          key={card.artifact.id}
          card={card}
          workspaceId={workspaceId}
          workspaceName={workspaceName}
          previewPort={previewPort ?? null}
          embedded={embedded}
        />
      ))}
    </div>
  );
}

function ArtifactCardView({
  card,
  workspaceId,
  workspaceName,
  previewPort,
  embedded,
}: {
  card: ArtifactCard;
  workspaceId: string | null;
  workspaceName: string | null;
  previewPort: number | null;
  embedded: boolean;
}) {
  const [open, setOpen] = useState(true);
  const total = card.actions.length;
  const succeeded = card.actions.filter((a) => a.status === "success").length;
  const failed = card.actions.filter((a) => a.status === "error").length;
  const running = card.actions.filter(
    (a) => a.status === "running" || a.status === "queued",
  ).length;

  const workbenchHref = workspaceId
    ? previewPort
      ? `/workbench/${workspaceId}?preview=${previewPort}`
      : `/workbench/${workspaceId}`
    : null;

  return (
    <div className="rounded-lg border border-border/60 bg-card/40 overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 hover:bg-accent/30"
      >
        {open ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
        <span className="text-[13px] font-medium truncate flex-1 text-left">
          {card.artifact.title}
        </span>
        {previewPort && (
          <span className="rounded bg-emerald-500/15 text-emerald-400 px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
            preview ready
          </span>
        )}
        <span className="text-[11px] text-muted-foreground">
          {running > 0
            ? `${succeeded}/${total} done · ${running} running`
            : failed > 0
              ? `${failed} failed`
              : `${succeeded}/${total}`}
        </span>
        {workbenchHref && !embedded && (
          <Link
            href={workbenchHref}
            onClick={(e) => e.stopPropagation()}
            className="rounded px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-accent flex items-center gap-1"
            title={`Open ${workspaceName ?? "workspace"}`}
          >
            <ExternalLink className="size-3" />
            {previewPort ? "open preview" : "workbench"}
          </Link>
        )}
      </button>
      {open && (
        <div className="border-t border-border/40 divide-y divide-border/30">
          {card.actions.map((a) => (
            <ActionRow key={a.actionId} a={a} />
          ))}
        </div>
      )}
      {(() => {
        const completedFiles = card.actions.filter(
          (a) => a.action.type === "file" && a.status === "success"
        );
        return <FileDownloadSection files={completedFiles} />;
      })()}
    </div>
  );
}

function ActionRow({ a }: { a: TrackedAction }) {
  // Auto-expand a file action while it is streaming so the user can
  // watch the AI write code character-by-character. Auto-collapse once
  // the action succeeds — by then the content is in the workbench
  // editor / on disk and the chat card just shows the filename.
  const [expanded, setExpanded] = useState(false);
  const isFileStreaming =
    a.action.type === "file" &&
    (a.status === "running" || a.status === "queued");

  useEffect(() => {
    if (isFileStreaming) setExpanded(true);
    else if (a.status === "success") setExpanded(false);
  }, [isFileStreaming, a.status]);

  const icon = (() => {
    if (a.status === "running" || a.status === "queued")
      return <Loader2 className="size-3.5 animate-spin text-muted-foreground" />;
    if (a.status === "success")
      return <CheckCircle2 className="size-3.5 text-emerald-500" />;
    if (a.status === "error") return <XCircle className="size-3.5 text-destructive" />;
    return null;
  })();

  const typeIcon = (() => {
    if (a.action.type === "file")
      return <FileCode className="size-3.5 text-muted-foreground" />;
    if (a.action.type === "shell")
      return <Terminal className="size-3.5 text-muted-foreground" />;
    if (a.action.type === "start")
      return <Play className="size-3.5 text-muted-foreground" />;
    return null;
  })();

  const label = describeAction(a.action);
  const canExpand = a.action.type === "file" || a.action.type === "shell" || a.action.type === "start";

  return (
    <div>
      <button
        onClick={() => canExpand && setExpanded((v) => !v)}
        disabled={!canExpand}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-1.5 text-[12px]",
          canExpand && "hover:bg-accent/40",
        )}
      >
        {icon}
        {typeIcon}
        <span
          className={cn(
            "font-mono truncate flex-1 text-left",
            a.status === "error" && "text-destructive",
          )}
        >
          {label}
        </span>
        {a.action.type === "file" && a.status === "running" && (
          <span className="text-[10px] text-muted-foreground">
            {a.action.content.length}b
          </span>
        )}
        {a.status === "error" && a.error && (
          <span
            className="text-[10px] text-destructive truncate max-w-50"
            title={a.error}
          >
            {a.error}
          </span>
        )}
        {canExpand && (
          <ChevronRight
            className={cn(
              "size-3.5 text-muted-foreground transition-transform",
              expanded && "rotate-90",
            )}
          />
        )}
      </button>

      {expanded && a.action.type === "file" && (
        <div className="border-t border-border/30 max-h-72 overflow-auto">
          <FileContentView
            path={a.action.filePath}
            content={a.action.content}
            streaming={isFileStreaming}
          />
        </div>
      )}
      {expanded && a.action.type !== "file" && (
        <div className="border-t border-border/30 px-3 py-2 font-mono text-[11px] whitespace-pre-wrap text-muted-foreground bg-background/50 max-h-40 overflow-auto">
          {a.action.content}
        </div>
      )}
    </div>
  );
}

function FileContentView({
  path,
  content,
  streaming,
}: {
  path: string;
  content: string;
  streaming: boolean;
}) {
  const ext = (path.split(".").pop() ?? "").toLowerCase();
  const extensions = useMemo<Extension[]>(() => {
    if (["ts", "tsx", "js", "jsx", "mjs", "cjs"].includes(ext))
      return [javascript({ jsx: true, typescript: ext.startsWith("t") })];
    if (ext === "html" || ext === "htm") return [html()];
    if (ext === "css") return [css()];
    if (ext === "json") return [json()];
    if (ext === "md" || ext === "markdown") return [markdown()];
    return [];
  }, [ext]);

  return (
    <CodeMirror
      value={content}
      theme={oneDark}
      extensions={extensions}
      editable={false}
      basicSetup={{ lineNumbers: true, foldGutter: false, highlightActiveLine: false }}
      style={{ fontSize: 12 }}
      // Keep the cursor pinned to the bottom while streaming so the user
      // sees new content as it arrives.
      onCreateEditor={(view) => {
        if (streaming) {
          view.dispatch({ selection: { anchor: content.length } });
          view.scrollDOM.scrollTop = view.scrollDOM.scrollHeight;
        }
      }}
    />
  );
}

function describeAction(a: Action): string {
  if (a.type === "file") return a.filePath || "(file)";
  if (a.type === "shell") return a.content.split("\n")[0]?.slice(0, 80) ?? "shell";
  if (a.type === "start")
    return `▶ ${a.content.split("\n")[0]?.slice(0, 80)}`;
  return "(action)";
}

function FileCard({ filePath, content }: { filePath: string; content: string }) {
  const filename = filePath.split("/").pop() ?? filePath;
  const ext = filename.split(".").pop()?.toUpperCase() ?? "";
  const basename = ext ? filename.slice(0, -(ext.length + 1)) : filename;

  return (
    <div className="flex items-center gap-3 rounded-xl border border-border/50 bg-card/50 px-4 py-3">
      <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-background/60 border border-border/40">
        <FileCode className="size-5 text-muted-foreground/70" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13.5px] font-medium text-foreground truncate">{basename}</div>
        <div className="text-[11px] text-muted-foreground uppercase tracking-wide">{ext}</div>
      </div>
      <button
        type="button"
        onClick={() => downloadFile(filename, content)}
        className="shrink-0 rounded-lg border border-border/60 bg-accent/30 px-3 py-1.5 text-[12.5px] font-medium text-foreground hover:bg-accent/60 transition-colors"
      >
        Download
      </button>
    </div>
  );
}

function FileDownloadSection({ files }: { files: TrackedAction[] }) {
  if (files.length === 0) return null;

  const downloadAll = () => {
    files.forEach((f, i) => {
      if (f.action.type !== "file") return;
      const action = f.action;
      setTimeout(
        () =>
          downloadFile(
            action.filePath.split("/").pop() ?? action.filePath,
            action.content,
          ),
        i * 50,
      );
    });
  };

  return (
    <div className="border-t border-border/40 px-4 py-3 space-y-2">
      {files.map(
        (f) =>
          f.action.type === "file" && (
            <FileCard key={f.actionId} filePath={f.action.filePath} content={f.action.content} />
          ),
      )}
      {files.length > 1 && (
        <button
          type="button"
          onClick={downloadAll}
          className="flex items-center gap-1.5 rounded-lg border border-border/50 px-3 py-1.5 text-[12.5px] text-muted-foreground hover:bg-accent/30 hover:text-foreground transition-colors"
        >
          <Download className="size-3.5" />
          Download all
        </button>
      )}
    </div>
  );
}
