"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, Download, Palette, Play, Plus, Share2, Sparkles, X } from "lucide-react";
import type { TreeEntry } from "@/lib/workspace/client";
import { Chat } from "@/components/chat/chat";
import { Button } from "@/components/ui/button";
import { useSettings } from "@/hooks/use-settings";
import { cn } from "@/lib/utils";
import { DEFAULT_FORMAT, FORMAT_LABEL, type Format } from "@/lib/design/format";
import { BrandPanel } from "./brand-panel";
import { DesignFilesPanel } from "./design-files-panel";
import { DesignPreview, type DesignComment } from "./design-preview";
import { FormatSelector } from "./format-selector";

type DesignTab = { kind: "files" } | { kind: "file"; entry: TreeEntry };

function buildEditPrompt(c: DesignComment): string {
  const text = c.text ? `, current content: "${c.text}"` : "";
  return [
    `Edit ONLY this element in ${c.filePath} — leave every other element identical, byte-for-byte.`,
    ``,
    `Element: <${c.tag}>${text}`,
    `CSS path (best-effort): ${c.selector}`,
    ``,
    `Change requested: ${c.comment}`,
    ``,
    `Return the FULL updated ${c.filePath} as a single boltAction file write.`,
  ].join("\n");
}

function tabKey(t: DesignTab): string {
  return t.kind === "files" ? "__files" : `f:${t.entry.path}`;
}

export function DesignView({
  workspaceId,
  workspaceName,
}: {
  workspaceId: string;
  workspaceName: string;
}) {
  const [tabs, setTabs] = useState<DesignTab[]>([{ kind: "files" }]);
  const [activeKey, setActiveKey] = useState<string>("__files");
  const [selected, setSelected] = useState<TreeEntry | null>(null);
  const [format, setFormat] = useState<Format>(DEFAULT_FORMAT);
  const [showBrand, setShowBrand] = useState(false);
  const [streaming, setStreaming] = useState<{ filePath: string; content: string } | null>(null);
  const [prefillPrompt, setPrefillPrompt] = useState<{ id: string; text: string } | undefined>(undefined);
  const { data: settings } = useSettings();

  const handleComment = (c: DesignComment) => {
    setPrefillPrompt({
      id: `${Date.now()}`,
      text: buildEditPrompt(c),
    });
  };

  const openFile = (entry: TreeEntry) => {
    if (entry.type === "dir") return;
    setSelected(entry);
    setTabs((prev) => {
      const k = `f:${entry.path}`;
      if (prev.some((t) => tabKey(t) === k)) return prev;
      return [...prev, { kind: "file", entry }];
    });
    setActiveKey(`f:${entry.path}`);
  };

  const closeTab = (k: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => tabKey(t) !== k);
      // Always keep at least the Design Files tab.
      if (next.length === 0 || !next.some((t) => t.kind === "files")) {
        return [{ kind: "files" }, ...next.filter((t) => t.kind !== "files")];
      }
      return next;
    });
    if (activeKey === k) setActiveKey("__files");
  };

  const initials = (settings?.user?.name ?? "U").slice(0, 1).toUpperCase();
  const showFiles = activeKey === "__files";

  return (
    <div className="flex h-full flex-col bg-background">
      {/* ── Top bar ─────────────────────────────────────────── */}
      <header className="flex h-12 shrink-0 items-stretch border-b border-border/60">
        <div className="flex w-95 shrink-0 items-center gap-2 border-r border-border/60 px-3">
          <span className="flex size-7 items-center justify-center rounded-md bg-orange-500/15 text-orange-400">
            <Palette className="size-3.5" />
          </span>
          <span className="text-[14px] font-semibold tracking-tight">
            {workspaceName}
          </span>
        </div>

        <div className="flex flex-1 items-stretch overflow-x-auto">
          {tabs.map((t) => {
            const k = tabKey(t);
            const active = activeKey === k;
            const label = t.kind === "files" ? "Design Files" : t.entry.name;
            return (
              <button
                key={k}
                type="button"
                onClick={() => {
                  setActiveKey(k);
                  if (t.kind === "file") setSelected(t.entry);
                }}
                className={cn(
                  "group flex items-center gap-2 border-r border-border/60 px-4 text-[13px] transition-colors",
                  active
                    ? "bg-background text-foreground"
                    : "bg-muted/20 text-muted-foreground hover:bg-muted/30",
                )}
              >
                {t.kind === "files" ? (
                  <Palette className="size-3.5 text-muted-foreground" />
                ) : null}
                <span className="truncate max-w-48">{label}</span>
                {t.kind === "file" && (
                  <span
                    role="button"
                    tabIndex={-1}
                    aria-label="Close tab"
                    className="ml-1 rounded p-0.5 text-muted-foreground/60 opacity-60 hover:bg-muted hover:text-foreground hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(k);
                    }}
                  >
                    <X className="size-3" />
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="flex shrink-0 items-center gap-2 px-3">
          <Button
            variant={showBrand ? "secondary" : "ghost"}
            size="sm"
            className="rounded-md"
            onClick={() => setShowBrand((v) => !v)}
          >
            <Sparkles className="size-3.5" />
            Brand
          </Button>
          {selected && selected.type !== "dir" && (
            <details className="relative">
              <summary className="flex cursor-pointer list-none items-center gap-1 rounded-md px-2 py-1 text-[13px] text-muted-foreground hover:bg-muted">
                <Play className="size-3.5" />
                Present
                <ChevronDown className="size-3" />
              </summary>
              <div className="absolute right-0 top-full z-20 mt-1 w-52 overflow-hidden rounded-md border border-border/60 bg-popover shadow-md">
                <a
                  href={`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <Play className="size-3.5" />
                  Open in new tab
                </a>
                <a
                  href={`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`}
                  download={selected.name}
                  className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <Download className="size-3.5" />
                  Download HTML
                </a>
                <a
                  href={`/api/design/export?workspaceId=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(selected.path)}&format=${format}`}
                  className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <Download className="size-3.5" />
                  Download PDF
                </a>
              </div>
            </details>
          )}
          <Button
            size="sm"
            className="rounded-md bg-foreground text-background hover:bg-foreground/90"
          >
            <Share2 className="size-3.5" />
            Share
          </Button>
          <Link
            href="/settings"
            className="flex size-7 items-center justify-center rounded-full bg-primary/20 font-mono text-[11px] font-semibold text-primary"
          >
            {initials}
          </Link>
        </div>
      </header>

      {/* ── Body: chat | files-or-preview-only | preview ──── */}
      <div className="flex flex-1 min-h-0">
        {/* Left: chat */}
        <aside className="flex w-95 shrink-0 flex-col border-r border-border/60">
          <ChatTabsHeader />
          <FormatSelector value={format} onChange={setFormat} />
          <div className="flex-1 min-h-0">
            <Chat
              embedded
              mode="design"
              format={format}
              workspaceId={workspaceId}
              workspaceName={workspaceName}
              composerPlaceholder={`Describe the ${FORMAT_LABEL[format].toLowerCase()} you want to create…`}
              onStreamingFile={(filePath, content) =>
                setStreaming({ filePath, content })
              }
              onFileComplete={(filePath) => {
                // Keep the streaming preview up briefly so the user sees the
                // final state, then clear it so the iframe falls back to the
                // file on disk (which the existing parser has now written).
                setStreaming((cur) =>
                  cur && cur.filePath === filePath ? null : cur,
                );
              }}
              prefillPrompt={prefillPrompt}
            />
          </div>
        </aside>

        {/* Center: brand editor when toggled; otherwise files+preview or
            preview-only depending on the active tab. */}
        {showBrand ? (
          <div className="flex flex-1 min-w-0">
            <div className="flex flex-1 min-w-0 flex-col border-r border-border/60">
              <BrandPanel workspaceId={workspaceId} />
            </div>
            <div className="flex w-[42%] min-w-80 shrink-0 flex-col">
              <DesignPreview workspaceId={workspaceId} selected={selected} streaming={streaming} />
            </div>
          </div>
        ) : showFiles ? (
          <div className="flex flex-1 min-w-0">
            <div className="flex flex-1 min-w-0 flex-col">
              <DesignFilesPanel
                workspaceId={workspaceId}
                selectedPath={selected?.path ?? null}
                onSelectFile={openFile}
                format={format}
                onStarter={(prompt) =>
                  setPrefillPrompt({ id: `${Date.now()}`, text: prompt })
                }
              />
            </div>
            <div className="flex w-[42%] min-w-80 shrink-0 flex-col border-l border-border/60">
              <DesignPreview workspaceId={workspaceId} selected={selected} streaming={streaming} />
            </div>
          </div>
        ) : (
          <div className="flex flex-1 min-w-0 flex-col">
            <DesignPreview
              workspaceId={workspaceId}
              selected={selected}
              streaming={streaming}
              showToolbar
              onComment={handleComment}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function ChatTabsHeader() {
  const [tab, setTab] = useState<"chat" | "comments">("chat");
  return (
    <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/60 px-2">
      <div className="flex items-center">
        {(["chat", "comments"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={cn(
              "px-2.5 py-1.5 text-[13px] font-medium capitalize transition-colors",
              tab === t
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>
      <Button variant="ghost" size="icon-sm" aria-label="New thread">
        <Plus className="size-3.5" />
      </Button>
    </div>
  );
}
