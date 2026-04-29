"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, Download, ExternalLink, Maximize, Palette, Play, Plus, Share2, Sliders, X } from "lucide-react";
import type { TreeEntry } from "@/lib/workspace/client";
import { Chat } from "@/components/chat/chat";
import { Button } from "@/components/ui/button";
import { useSettings } from "@/hooks/use-settings";
import { useResizableColumn } from "@/hooks/use-resizable-column";
import { cn } from "@/lib/utils";
import { formatFromFilename } from "@/lib/design/format";
import { extractTweaks, type Tweak } from "@/lib/design/tweaks";
import { useQuery } from "@tanstack/react-query";
import { apiReadFile } from "@/lib/workspace/client";
import { useDesignComments, type DesignCommentRecord } from "@/hooks/use-design-comments";
import { BrandPanel } from "./brand-panel";
import { DesignFilesPanel } from "./design-files-panel";
import { DesignPreview, type DesignComment } from "./design-preview";
import { TweaksPanel } from "./tweaks-panel";

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
  const [showBrand, setShowBrand] = useState(false);
  const [streaming, setStreaming] = useState<{ filePath: string; content: string } | null>(null);
  const [prefillPrompt, setPrefillPrompt] = useState<{ id: string; text: string } | undefined>(undefined);
  const [showTweaks, setShowTweaks] = useState(false);
  const [tweakOverrides, setTweakOverrides] = useState<Record<string, Tweak["value"]>>({});
  const [chatTab, setChatTab] = useState<"chat" | "comments">("chat");
  // Bumping `chatKey` remounts <Chat>, throwing away its in-memory messages so
  // the user starts a fresh thread (the + button in the chat header).
  const [chatKey, setChatKey] = useState(0);
  const designComments = useDesignComments(workspaceId);
  const { data: settings } = useSettings();

  // Fetch the selected file's content so we can extract its declared tweaks.
  // Same queryKey as HtmlPreview's useQuery — react-query dedupes the fetch.
  const isHtmlFile =
    selected?.type === "file" && /\.html?$/i.test(selected.name);
  const { data: fileContent = "" } = useQuery({
    queryKey: ["design-file", workspaceId, selected?.path],
    queryFn: () =>
      selected ? apiReadFile(workspaceId, selected.path) : Promise.resolve(""),
    enabled: Boolean(isHtmlFile && selected),
  });
  const tweaks = useMemo<Tweak[]>(
    () => (isHtmlFile && fileContent ? extractTweaks(fileContent) : []),
    [isHtmlFile, fileContent],
  );

  // Reset overrides when the selected file changes — overrides are per-file
  // and we don't want last file's accent applied to the next one.
  useEffect(() => {
    setTweakOverrides({});
  }, [selected?.path]);

  const chatColumn = useResizableColumn({
    storageKey: "design.chatColumnWidth",
    defaultWidth: 380,
    min: 280,
    max: 640,
  });

  const closeDetails = (e: React.MouseEvent) => {
    e.currentTarget.closest("details")?.removeAttribute("open");
  };

  const presentInThisTab = () => {
    if (!selected) return;
    window.location.href = `/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`;
  };
  const presentFullscreen = () => {
    // Put the live preview iframe into fullscreen — works because the iframe
    // is in our document, not a cross-origin doc. Fall back to a new tab if
    // the browser blocks the request.
    const iframe = document.querySelector<HTMLIFrameElement>(
      "iframe[title]:not([sandbox=''])",
    );
    if (iframe?.requestFullscreen) {
      iframe.requestFullscreen().catch(() => presentInNewTab());
    } else {
      presentInNewTab();
    }
  };
  const presentInNewTab = () => {
    if (!selected) return;
    window.open(
      `/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`,
      "_blank",
      "noopener",
    );
  };

  const handleComment = (c: DesignComment) => {
    designComments.add({
      filePath: c.filePath,
      selector: c.selector,
      tag: c.tag,
      text: c.text,
      comment: c.comment,
    });
    setPrefillPrompt({
      id: `${Date.now()}`,
      text: buildEditPrompt(c),
    });
  };

  const handleNewThread = () => {
    setChatKey((k) => k + 1);
    setChatTab("chat");
  };

  const handleReuseComment = (c: DesignCommentRecord) => {
    setChatTab("chat");
    setPrefillPrompt({
      id: `${Date.now()}`,
      text: buildEditPrompt({
        filePath: c.filePath,
        selector: c.selector,
        tag: c.tag,
        text: c.text,
        comment: c.comment,
      }),
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
        <div
          className="flex shrink-0 items-center gap-2 border-r border-border/60 px-3"
          style={{ width: chatColumn.width }}
        >
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
          {isHtmlFile && (
            <button
              type="button"
              role="switch"
              aria-checked={showTweaks}
              onClick={() => setShowTweaks((v) => !v)}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1 text-[13px] transition-colors",
                showTweaks
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
              title="Tweaks — live knobs the design declared"
            >
              <Sliders className="size-3.5" />
              Tweaks
              <span
                className={cn(
                  "relative inline-flex h-4 w-7 items-center rounded-full transition-colors",
                  showTweaks ? "bg-primary" : "bg-muted",
                )}
              >
                <span
                  className={cn(
                    "inline-block size-3 rounded-full bg-background shadow transition-transform",
                    showTweaks ? "translate-x-3.5" : "translate-x-0.5",
                  )}
                />
              </span>
            </button>
          )}
          {selected && selected.type !== "dir" && (
            <details className="relative">
              <summary className="flex cursor-pointer list-none items-center gap-1 rounded-md px-2 py-1 text-[13px] text-muted-foreground hover:bg-muted">
                Present
                <ChevronDown className="size-3" />
              </summary>
              <div className="absolute right-0 top-full z-20 mt-1 w-52 overflow-hidden rounded-md border border-border/60 bg-popover shadow-md">
                <button
                  type="button"
                  onClick={(e) => { closeDetails(e); presentInThisTab(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted"
                >
                  <ExternalLink className="size-3.5" />
                  In this tab
                </button>
                <button
                  type="button"
                  onClick={(e) => { closeDetails(e); presentFullscreen(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted"
                >
                  <Maximize className="size-3.5" />
                  Fullscreen
                </button>
                <button
                  type="button"
                  onClick={(e) => { closeDetails(e); presentInNewTab(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted"
                >
                  <Play className="size-3.5" />
                  New tab
                </button>
                <div className="my-1 h-px bg-border/60" />
                <a
                  href={(() => {
                    const f = formatFromFilename(selected.name);
                    const q = `workspaceId=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(selected.path)}`;
                    return `/api/design/export?${q}${f ? `&format=${f}` : ""}`;
                  })()}
                  className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <Download className="size-3.5" />
                  Export PDF
                </a>
                <a
                  href={`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`}
                  download={selected.name}
                  className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <Download className="size-3.5" />
                  Export HTML
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
      <div className="relative flex flex-1 min-h-0">
        {/* Left: chat — width is user-resizable via the divider below */}
        <aside
          className="flex shrink-0 flex-col border-r border-border/60"
          style={{ width: chatColumn.width }}
        >
          <ChatTabsHeader
            tab={chatTab}
            onTabChange={setChatTab}
            commentsCount={designComments.items.length}
            onNewThread={handleNewThread}
          />
          <div className="flex-1 min-h-0">
            {chatTab === "chat" ? (
              <Chat
                key={chatKey}
                embedded
                mode="design"
                workspaceId={workspaceId}
                workspaceName={workspaceName}
                composerPlaceholder="Describe what you want to create — slides, prototype, landing, one-pager, infographic…"
                onStreamingFile={(filePath, content) =>
                  setStreaming({ filePath, content })
                }
                onFileComplete={(filePath) => {
                  setStreaming((cur) =>
                    cur && cur.filePath === filePath ? null : cur,
                  );
                }}
                prefillPrompt={prefillPrompt}
              />
            ) : (
              <CommentsList
                items={designComments.items}
                onReuse={handleReuseComment}
                onRemove={designComments.remove}
                onClear={designComments.clear}
              />
            )}
          </div>
        </aside>

        {/* Draggable splitter — drag horizontally to resize the chat column.
            The hit-target is wider than the visible bar for easier grabbing. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat column"
          onMouseDown={chatColumn.startDrag}
          className={cn(
            "group relative w-1 shrink-0 cursor-col-resize select-none",
            "before:absolute before:inset-y-0 before:-left-1 before:-right-1 before:content-['']",
            chatColumn.dragging
              ? "bg-primary/40"
              : "bg-transparent hover:bg-primary/20",
          )}
        />

        {/* Center: brand editor when toggled; otherwise files+preview or
            preview-only depending on the active tab. */}
        {showBrand ? (
          <div className="flex flex-1 min-w-0">
            <div className="flex flex-1 min-w-0 flex-col border-r border-border/60">
              <BrandPanel workspaceId={workspaceId} />
            </div>
            <div className="flex w-[42%] min-w-80 shrink-0 flex-col">
              <DesignPreview
                workspaceId={workspaceId}
                selected={selected}
                streaming={streaming}
                tweaks={tweaks}
                tweakOverrides={tweakOverrides}
              />
            </div>
          </div>
        ) : showFiles ? (
          <div className="flex flex-1 min-w-0">
            <div className="flex flex-1 min-w-0 flex-col">
              <DesignFilesPanel
                workspaceId={workspaceId}
                selectedPath={selected?.path ?? null}
                onSelectFile={openFile}
                onStarter={(prompt) =>
                  setPrefillPrompt({ id: `${Date.now()}`, text: prompt })
                }
                onToggleBrand={() => setShowBrand((v) => !v)}
                brandActive={showBrand}
              />
            </div>
            <div className="flex w-[42%] min-w-80 shrink-0 flex-col border-l border-border/60">
              <DesignPreview
                workspaceId={workspaceId}
                selected={selected}
                streaming={streaming}
                tweaks={tweaks}
                tweakOverrides={tweakOverrides}
              />
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
              tweaks={tweaks}
              tweakOverrides={tweakOverrides}
            />
          </div>
        )}

        {showTweaks && isHtmlFile && (
          <TweaksPanel
            tweaks={tweaks}
            values={tweakOverrides}
            onChange={(id, value) =>
              setTweakOverrides((o) => ({ ...o, [id]: value }))
            }
            onClose={() => setShowTweaks(false)}
          />
        )}
      </div>
    </div>
  );
}

function ChatTabsHeader({
  tab,
  onTabChange,
  commentsCount,
  onNewThread,
}: {
  tab: "chat" | "comments";
  onTabChange: (t: "chat" | "comments") => void;
  commentsCount: number;
  onNewThread: () => void;
}) {
  return (
    <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/60 px-2">
      <div className="flex items-center">
        {(["chat", "comments"] as const).map((t) => {
          const active = t === tab;
          return (
            <button
              key={t}
              type="button"
              onClick={() => onTabChange(t)}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] font-medium capitalize transition-colors",
                active
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t}
              {t === "comments" && commentsCount > 0 && (
                <span
                  className={cn(
                    "rounded px-1.5 py-0 font-mono text-[10px]",
                    active ? "bg-foreground text-background" : "bg-muted text-muted-foreground",
                  )}
                >
                  {commentsCount}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="New thread"
        title="New thread (start a fresh chat in this workspace)"
        onClick={onNewThread}
      >
        <Plus className="size-3.5" />
      </Button>
    </div>
  );
}

function CommentsList({
  items,
  onReuse,
  onRemove,
  onClear,
}: {
  items: DesignCommentRecord[];
  onReuse: (c: DesignCommentRecord) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  if (items.length === 0) {
    return (
      <div className="flex h-full flex-col items-start justify-start px-4 py-6 text-[13px] leading-5 text-muted-foreground">
        <p>No comments yet.</p>
        <p className="mt-2 max-w-xs">
          Open a design, click <strong>Comment</strong> in the preview toolbar,
          point at any element, type a change request. It&apos;ll show up here
          and prefill the chat.
        </p>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground">
        <span>{items.length} comments</span>
        <button
          type="button"
          onClick={() => {
            if (window.confirm(`Clear all ${items.length} comments?`)) onClear();
          }}
          className="hover:text-foreground"
        >
          Clear all
        </button>
      </div>
      <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {items.map((c) => (
          <CommentRow key={c.id} item={c} onReuse={onReuse} onRemove={onRemove} />
        ))}
      </div>
    </div>
  );
}

function CommentRow({
  item,
  onReuse,
  onRemove,
}: {
  item: DesignCommentRecord;
  onReuse: (c: DesignCommentRecord) => void;
  onRemove: (id: string) => void;
}) {
  const ago = relativeTime(item.createdAt);
  return (
    <div className="group rounded-md border border-transparent px-2 py-2 transition-colors hover:border-border/60 hover:bg-muted/30">
      <div className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="rounded bg-muted px-1.5 py-0 font-mono text-[10px] text-foreground">
          {item.tag}
        </span>
        <span className="truncate">{item.filePath}</span>
        <span className="ml-auto whitespace-nowrap">{ago}</span>
      </div>
      {item.text && (
        <div className="mb-1 line-clamp-1 text-[12px] text-muted-foreground/90">
          &ldquo;{item.text}&rdquo;
        </div>
      )}
      <div className="text-[13px] leading-5 text-foreground">{item.comment}</div>
      <div className="mt-1.5 flex gap-2 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          type="button"
          onClick={() => onReuse(item)}
          className="text-[11px] text-primary hover:underline"
        >
          Re-send
        </button>
        <button
          type="button"
          onClick={() => onRemove(item.id)}
          className="text-[11px] text-muted-foreground hover:text-destructive"
        >
          Remove
        </button>
      </div>
    </div>
  );
}

function relativeTime(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d`;
  return new Date(ts).toLocaleDateString();
}
