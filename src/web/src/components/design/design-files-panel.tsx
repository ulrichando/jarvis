"use client";

import {
  ChevronUp,
  Clipboard,
  File as FileIcon,
  FileCode,
  FileText,
  Folder,
  MoreHorizontal,
  Palette,
  Pencil,
  RefreshCw,
  Upload,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { apiTree, type TreeEntry } from "@/lib/workspace/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  GROUP_LABEL,
  classify,
  fileKindLabel,
  groupEntries,
  type FileGroupKey,
} from "./file-classify";

const SECTION_ORDER: FileGroupKey[] = [
  "folders",
  "pages",
  "stylesheets",
  "scripts",
  "other",
];

export function DesignFilesPanel({
  workspaceId,
  selectedPath,
  onSelectFile,
  onUpClick,
  refetchKey,
}: {
  workspaceId: string;
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
  onUpClick?: () => void;
  refetchKey?: number;
}) {
  const { data: entries = [], isLoading, refetch } = useQuery({
    queryKey: ["design-tree", workspaceId, refetchKey ?? 0],
    queryFn: () => apiTree(workspaceId, ""),
  });

  const groups = useMemo(() => groupEntries(entries), [entries]);
  const total = entries.length;

  return (
    <div className="flex h-full flex-col bg-muted/10">
      {/* Center toolbar */}
      <div className="flex items-center gap-2 border-b border-border/50 px-3 h-11 shrink-0">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Up"
          onClick={onUpClick}
          disabled={!onUpClick}
        >
          <ChevronUp className="size-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Refresh"
          onClick={() => refetch()}
        >
          <RefreshCw className="size-3.5" />
        </Button>
        <span className="text-[13px] text-muted-foreground/80">project</span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" className="rounded-md">
            <Pencil className="size-3.5" />
            New sketch
          </Button>
          <Button variant="ghost" size="sm" className="rounded-md">
            <Clipboard className="size-3.5" />
            Paste
          </Button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {isLoading && total === 0 ? (
          <div className="px-6 py-8 text-[13px] text-muted-foreground">
            loading…
          </div>
        ) : total === 0 ? (
          <EmptyDesign />
        ) : (
          <div className="space-y-5 px-3 py-4">
            {SECTION_ORDER.map((key) => {
              const items = groups[key];
              if (items.length === 0) return null;
              return (
                <FileSection
                  key={key}
                  label={GROUP_LABEL[key]}
                  entries={items}
                  selectedPath={selectedPath}
                  onSelectFile={onSelectFile}
                />
              );
            })}
          </div>
        )}
      </div>

      {/* Drop zone */}
      <DropZone />
    </div>
  );
}

function FileSection({
  label,
  entries,
  selectedPath,
  onSelectFile,
}: {
  label: string;
  entries: TreeEntry[];
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
}) {
  return (
    <div>
      <div className="px-3 pb-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground/70">
        {label}
      </div>
      <div className="space-y-px">
        {entries.map((e) => (
          <FileRow
            key={e.path}
            entry={e}
            selected={selectedPath === e.path}
            onClick={() => onSelectFile(e)}
          />
        ))}
      </div>
    </div>
  );
}

function FileRow({
  entry,
  selected,
  onClick,
}: {
  entry: TreeEntry;
  selected: boolean;
  onClick: () => void;
}) {
  // We don't have per-file mtime in the tree response yet; placeholder.
  const updated = "—";
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors",
        selected
          ? "bg-primary/10 text-foreground"
          : "hover:bg-muted/40 text-foreground/90",
      )}
    >
      <span
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-md",
          tintFor(entry),
        )}
      >
        <FileIconFor entry={entry} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium">{entry.name}</div>
        <div className="truncate text-[11px] text-muted-foreground">
          {fileKindLabel(entry)}
        </div>
      </div>
      <div className="hidden shrink-0 text-[11px] text-muted-foreground/70 md:block">
        {updated}
      </div>
      <span
        role="button"
        tabIndex={-1}
        aria-label="More"
        className="ml-1 hidden text-muted-foreground/70 transition-colors hover:text-foreground group-hover:block"
        onClick={(e) => e.stopPropagation()}
      >
        <MoreHorizontal className="size-3.5" />
      </span>
    </button>
  );
}

function FileIconFor({ entry }: { entry: TreeEntry }) {
  if (entry.type === "dir") return <Folder className="size-3.5" />;
  const k = classify(entry);
  if (k === "pages") return <FileText className="size-3.5" />;
  if (k === "stylesheets") return <Palette className="size-3.5" />;
  if (k === "scripts") return <FileCode className="size-3.5" />;
  return <FileIcon className="size-3.5" />;
}

function tintFor(entry: TreeEntry): string {
  const k = classify(entry);
  if (k === "folders") return "bg-amber-500/15 text-amber-400";
  if (k === "pages") return "bg-orange-500/15 text-orange-400";
  if (k === "stylesheets") return "bg-sky-500/15 text-sky-400";
  if (k === "scripts") return "bg-violet-500/15 text-violet-400";
  return "bg-muted text-muted-foreground";
}

function DropZone() {
  return (
    <div className="border-t border-border/50 bg-background/40 px-5 py-4">
      <div className="flex items-center gap-2 text-[13px] font-medium text-foreground/80">
        <Upload className="size-3.5 text-muted-foreground" />
        DROP FILES HERE
      </div>
      <p className="mt-1 text-[12px] leading-5 text-muted-foreground">
        Images, docs, references, Figma links, or folders — Jarvis will use
        them as context.
      </p>
    </div>
  );
}

function EmptyDesign() {
  return (
    <div className="flex flex-col items-center justify-center px-8 py-16 text-center text-muted-foreground">
      <div className="flex size-12 items-center justify-center rounded-xl border border-dashed border-border/60">
        <FileText className="size-5 text-muted-foreground/70" />
      </div>
      <p className="mt-4 max-w-xs text-[13px] leading-5">
        No design files yet. Ask Jarvis in the chat to scaffold your first
        sketch, or drop files below.
      </p>
    </div>
  );
}

