"use client";

import {
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FileCode,
  FileText,
  Folder,
  Loader2,
  Palette,
  Lightbulb,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiDeleteEntry, apiTree, type TreeEntry } from "@/lib/workspace/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { type Format, FORMAT_LABEL } from "@/lib/design/format";
import { STARTERS } from "@/lib/design/starters";
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
  "components",
  "stylesheets",
  "scripts",
  "references",
  "other",
];

export function DesignFilesPanel({
  workspaceId,
  selectedPath,
  onSelectFile,
  refetchKey,
  format,
  onStarter,
  onToggleBrand,
  brandActive,
  onRefine,
  onFileDeleted,
  onWorkspaceCleared,
}: {
  workspaceId: string;
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
  refetchKey?: number;
  format?: Format;
  onStarter?: (prompt: string) => void;
  /** Toggle the brand-editor panel from inside this toolbar. The header
   *  no longer carries a Brand button — Present is the only header action. */
  onToggleBrand?: () => void;
  brandActive?: boolean;
  /** Open the structured "Refine the brief" form. When set, the empty state
   *  shows it as a primary CTA above the starter cards. */
  onRefine?: () => void;
  /** Fired after a successful single-file or single-folder delete so the
   *  parent can clear the selection / close tabs pointing at the now-gone
   *  path. Pass the deleted entry's path. */
  onFileDeleted?: (path: string) => void;
  /** Fired after a workspace-clear succeeds so the parent can drop the
   *  preview entirely (every file is gone, not just one). */
  onWorkspaceCleared?: () => void;
}) {
  const qc = useQueryClient();
  const { data: entries = [], isLoading, refetch } = useQuery({
    queryKey: ["design-tree", workspaceId, "", refetchKey ?? 0],
    queryFn: () => apiTree(workspaceId, ""),
    // Belt-and-suspenders: invalidations from the runner SHOULD pull new
    // files into the panel within ms, but if a notification drops (rAF
    // scheduling, React batching mid-stream, etc.) the panel can sit
    // stale. 2s polling guarantees the user sees files within at most
    // one tick — cheap for a local API, no perceptible cost.
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });

  const del = useMutation({
    mutationFn: (path: string) => apiDeleteEntry(workspaceId, path),
    onSuccess: (_, path) => {
      qc.invalidateQueries({ queryKey: ["design-tree", workspaceId] });
      qc.removeQueries({
        queryKey: ["design-file", workspaceId, path],
        exact: false,
      });
      onFileDeleted?.(path);
    },
  });

  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const toggleFolder = (path: string) =>
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  // Auto-expand newly-appearing folders during generation. Without this,
  // the model writes `components/Button.jsx` and the user only sees a
  // collapsed `components/` row — feels like nothing's actually happening
  // inside. Tracks which dir paths we've already seen and expands the new
  // ones. We never auto-collapse, so a folder the user closes manually
  // stays closed as long as no NEW dirs land.
  const seenDirsRef = useRef<Set<string> | null>(null);
  useEffect(() => {
    seenDirsRef.current = null;
  }, [workspaceId]);
  useEffect(() => {
    const currentDirs = new Set<string>();
    for (const e of entries) if (e.type === "dir") currentDirs.add(e.path);
    if (seenDirsRef.current === null) {
      seenDirsRef.current = currentDirs;
      return;
    }
    const newlyAppeared: string[] = [];
    for (const p of currentDirs) {
      if (!seenDirsRef.current.has(p)) newlyAppeared.push(p);
    }
    seenDirsRef.current = currentDirs;
    if (newlyAppeared.length === 0) return;
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      for (const p of newlyAppeared) next.add(p);
      return next;
    });
  }, [entries]);

  const clearWs = useMutation({
    mutationFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/clear`, {
        method: "POST",
      });
      if (!r.ok && r.status !== 207) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error ?? `clear failed (${r.status})`);
      }
      return (await r.json()) as { ok: boolean; cleared: number };
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["design-tree", workspaceId] });
      qc.removeQueries({ queryKey: ["design-file", workspaceId], exact: false });
      onWorkspaceCleared?.();
    },
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
          aria-label="Refresh"
          onClick={() => refetch()}
        >
          <RefreshCw className="size-3.5" />
        </Button>
        <span className="text-[13px] font-medium text-muted-foreground/90">
          {total > 0 ? `${total} file${total === 1 ? "" : "s"}` : "Files"}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {total > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="rounded-md text-muted-foreground hover:text-destructive"
              onClick={() => {
                if (clearWs.isPending) return;
                const ok = window.confirm(
                  `Clear all ${total} item${total === 1 ? "" : "s"} in this workspace? Brand settings will be kept.`,
                );
                if (ok) clearWs.mutate();
              }}
              disabled={clearWs.isPending}
              title="Clear workspace — wipe all files (brand settings preserved)"
            >
              {clearWs.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Trash2 className="size-3.5" />
              )}
              Clear
            </Button>
          )}
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
                  onDelete={(path) => del.mutate(path)}
                  deletingPath={del.isPending ? (del.variables ?? null) : null}
                  workspaceId={workspaceId}
                  expanded={expandedFolders}
                  onToggleFolder={toggleFolder}
                />
              );
            })}
          </div>
        )}
      </div>

      {/* Drop zone */}
      <TipBanner />
      <DropZone
        workspaceId={workspaceId}
        onUploaded={() =>
          qc.invalidateQueries({ queryKey: ["design-tree", workspaceId] })
        }
      />
    </div>
  );
}

function FileSection({
  label,
  entries,
  selectedPath,
  onSelectFile,
  onDelete,
  deletingPath,
  workspaceId,
  expanded,
  onToggleFolder,
}: {
  label: string;
  entries: TreeEntry[];
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
  onDelete: (path: string) => void;
  deletingPath: string | null;
  workspaceId: string;
  expanded: Set<string>;
  onToggleFolder: (path: string) => void;
}) {
  return (
    <div>
      <div className="px-3 pb-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground/70">
        {label}
      </div>
      <div className="space-y-px">
        {entries.map((e) => {
          const isFolder = e.type === "dir";
          const isExpanded = isFolder && expanded.has(e.path);
          return (
            <FileEntry
              key={e.path}
              entry={e}
              selectedPath={selectedPath}
              onSelectFile={onSelectFile}
              onDelete={onDelete}
              deletingPath={deletingPath}
              workspaceId={workspaceId}
              expanded={expanded}
              onToggleFolder={onToggleFolder}
              isFolder={isFolder}
              isExpanded={isExpanded}
              indent={0}
            />
          );
        })}
      </div>
    </div>
  );
}

// A single tree node: renders the row, then if this is an expanded folder,
// recursively renders its contents (which may themselves include nested
// expanded folders). Indentation grows with depth.
function FileEntry({
  entry,
  selectedPath,
  onSelectFile,
  onDelete,
  deletingPath,
  workspaceId,
  expanded,
  onToggleFolder,
  isFolder,
  isExpanded,
  indent,
}: {
  entry: TreeEntry;
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
  onDelete: (path: string) => void;
  deletingPath: string | null;
  workspaceId: string;
  expanded: Set<string>;
  onToggleFolder: (path: string) => void;
  isFolder: boolean;
  isExpanded: boolean;
  indent: number;
}) {
  return (
    <>
      <FileRow
        entry={entry}
        selected={selectedPath === entry.path}
        onClick={() =>
          isFolder ? onToggleFolder(entry.path) : onSelectFile(entry)
        }
        onDelete={() => onDelete(entry.path)}
        deleting={deletingPath === entry.path}
        expandable={isFolder}
        expanded={isExpanded}
        indent={indent}
      />
      {isExpanded && (
        <FolderContents
          workspaceId={workspaceId}
          path={entry.path}
          indent={indent + 1}
          selectedPath={selectedPath}
          onSelectFile={onSelectFile}
          onDelete={onDelete}
          deletingPath={deletingPath}
          expanded={expanded}
          onToggleFolder={onToggleFolder}
        />
      )}
    </>
  );
}

function FolderContents({
  workspaceId,
  path,
  indent,
  selectedPath,
  onSelectFile,
  onDelete,
  deletingPath,
  expanded,
  onToggleFolder,
}: {
  workspaceId: string;
  path: string;
  indent: number;
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
  onDelete: (path: string) => void;
  deletingPath: string | null;
  expanded: Set<string>;
  onToggleFolder: (path: string) => void;
}) {
  const { data: entries = [], isLoading } = useQuery({
    queryKey: ["design-tree", workspaceId, path],
    queryFn: () => apiTree(workspaceId, path),
    // Same polling as the root tree — when the model writes files into
    // a subfolder during generation, this query needs to refetch too or
    // the user only sees the empty folder while files are landing.
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });
  if (isLoading) {
    return (
      <div
        className="text-[11px] text-muted-foreground/70 py-1"
        style={{ paddingLeft: `${indent * 16 + 12}px` }}
      >
        loading…
      </div>
    );
  }
  if (entries.length === 0) {
    return (
      <div
        className="text-[11px] italic text-muted-foreground/60 py-1"
        style={{ paddingLeft: `${indent * 16 + 12}px` }}
      >
        empty folder
      </div>
    );
  }
  return (
    <>
      {entries.map((e) => {
        const isFolder = e.type === "dir";
        const isExpanded = isFolder && expanded.has(e.path);
        return (
          <FileEntry
            key={e.path}
            entry={e}
            selectedPath={selectedPath}
            onSelectFile={onSelectFile}
            onDelete={onDelete}
            deletingPath={deletingPath}
            workspaceId={workspaceId}
            expanded={expanded}
            onToggleFolder={onToggleFolder}
            isFolder={isFolder}
            isExpanded={isExpanded}
            indent={indent}
          />
        );
      })}
    </>
  );
}

function FileRow({
  entry,
  selected,
  onClick,
  onDelete,
  deleting,
  expandable = false,
  expanded = false,
  indent = 0,
}: {
  entry: TreeEntry;
  selected: boolean;
  onClick: () => void;
  onDelete: () => void;
  deleting: boolean;
  /** When true, render a chevron at the row's start that flips with expanded. */
  expandable?: boolean;
  expanded?: boolean;
  /** Tree depth — every level adds 16px of left padding. */
  indent?: number;
}) {
  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (deleting) return;
    if (typeof window !== "undefined") {
      const ok = window.confirm(`Delete ${entry.name}? This can't be undone.`);
      if (!ok) return;
    }
    onDelete();
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={indent > 0 ? { paddingLeft: `${indent * 16 + 12}px` } : undefined}
      className={cn(
        "group flex w-full cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-left transition-colors",
        selected
          ? "bg-primary/10 text-foreground"
          : "hover:bg-muted/40 text-foreground/90",
      )}
    >
      {expandable ? (
        <span className="flex size-3.5 shrink-0 items-center justify-center text-muted-foreground/70">
          {expanded ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </span>
      ) : (
        <span className="size-3.5 shrink-0" aria-hidden />
      )}
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
      <button
        type="button"
        aria-label={`Delete ${entry.name}`}
        title="Delete"
        onClick={handleDelete}
        disabled={deleting}
        className={cn(
          "ml-1 rounded-md p-1 text-muted-foreground/60 transition-colors",
          "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
          "hover:bg-destructive/10 hover:text-destructive",
          deleting && "opacity-100",
        )}
      >
        {deleting ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Trash2 className="size-3.5" />
        )}
      </button>
    </div>
  );
}

function FileIconFor({ entry }: { entry: TreeEntry }) {
  if (entry.type === "dir") return <Folder className="size-3.5" />;
  const k = classify(entry);
  if (k === "pages") return <FileText className="size-3.5" />;
  if (k === "components") return <FileCode className="size-3.5" />;
  if (k === "stylesheets") return <Palette className="size-3.5" />;
  if (k === "scripts") return <FileCode className="size-3.5" />;
  if (k === "references") return <FileIcon className="size-3.5" />;
  return <FileIcon className="size-3.5" />;
}

function tintFor(entry: TreeEntry): string {
  const k = classify(entry);
  if (k === "folders") return "bg-amber-500/15 text-amber-400";
  if (k === "pages") return "bg-orange-500/15 text-orange-400";
  if (k === "components") return "bg-emerald-500/15 text-emerald-400";
  if (k === "stylesheets") return "bg-sky-500/15 text-sky-400";
  if (k === "scripts") return "bg-violet-500/15 text-violet-400";
  if (k === "references") return "bg-rose-500/15 text-rose-400";
  return "bg-muted text-muted-foreground";
}

// Rotating tips shown above the dropzone. Refresh-cycles through the list so
// repeat users see different ones without a "next tip" affordance to manage.
const TIPS = [
  "Drop an image into the file panel — Jarvis can use it as a visual reference.",
  "Click 'Comment' in the preview toolbar to leave a targeted edit on any element.",
  "Hit 'Tweaks' to live-tune the accent, density, and toggles your design declared.",
  "Save a brand and every future generation in this workspace stays on-brand.",
  "Press Cmd/Ctrl+Enter in the comment popover to send.",
  "Press the format chip in starter prompts to skip the typing — review before sending.",
  "The Present menu has Fullscreen — hand the iframe over for a real demo.",
  "Drag the divider between chat and the file panel to give yourself more room.",
];

function TipBanner() {
  // SSR renders the first tip deterministically; the client re-rolls on mount
  // for variety. Doing the random pick in useState's lazy init causes a
  // hydration mismatch (server picks a different index than the client), so
  // the random pick is gated to a useEffect that only runs after hydration.
  const [tip, setTip] = useState(TIPS[0]);
  useEffect(() => {
    setTip(TIPS[Math.floor(Math.random() * TIPS.length)]);
  }, []);
  return (
    <div className="flex items-start gap-2 border-t border-border/50 bg-orange-500/5 px-5 py-3 text-[12px] leading-5 text-foreground/85">
      <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-orange-400" />
      <div>
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-orange-400">
          Tip
        </span>
        <p className="mt-0.5 text-muted-foreground">{tip}</p>
      </div>
    </div>
  );
}

function DropZone({
  workspaceId,
  onUploaded,
}: {
  workspaceId: string;
  onUploaded: () => void;
}) {
  const [active, setActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUploaded, setLastUploaded] = useState<string[] | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const upload = async (files: FileList | File[]) => {
    setBusy(true);
    setError(null);
    const arr = Array.from(files);
    const uploadedNames: string[] = [];
    try {
      for (const f of arr) {
        if (f.size === 0) continue;
        if (f.size > 10 * 1024 * 1024) {
          setError(`${f.name} > 10MB`);
          continue;
        }
        const dataUrl = await readAsDataUrl(f);
        const base64 = dataUrl.split(",")[1] ?? "";
        const safeName = f.name.replace(/[/\\]/g, "_");
        const target = looksLikeReference(f) ? `references/${safeName}` : safeName;
        const r = await fetch(`/api/workspace/${workspaceId}/upload`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: target, base64 }),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          setError(j?.error ?? `upload failed (${r.status})`);
          continue;
        }
        uploadedNames.push(target);
      }
      if (uploadedNames.length > 0) {
        setLastUploaded(uploadedNames);
        onUploaded();
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        if (!active) setActive(true);
      }}
      onDragLeave={(e) => {
        // Only deactivate when leaving the dropzone itself, not its children.
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setActive(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setActive(false);
        if (e.dataTransfer.files.length > 0) void upload(e.dataTransfer.files);
      }}
      className={cn(
        "border-t bg-background/40 px-5 py-4 transition-colors",
        active
          ? "border-primary/50 bg-primary/5"
          : "border-border/50",
      )}
    >
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={busy}
        className="flex items-center gap-2 text-[13px] font-medium text-foreground/80 hover:text-foreground"
      >
        {busy ? (
          <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
        ) : (
          <Upload className="size-3.5 text-muted-foreground" />
        )}
        DROP FILES HERE
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files && e.target.files.length > 0) {
            void upload(e.target.files);
            e.target.value = "";
          }
        }}
      />
      <p className="mt-1 text-[12px] leading-5 text-muted-foreground">
        Images, docs, references — Jarvis can use them as context. Click to
        browse, or drop them here.
      </p>
      {error && (
        <p className="mt-1 text-[11px] leading-4 text-red-500">{error}</p>
      )}
      {lastUploaded && lastUploaded.length > 0 && !error && (
        <p className="mt-1 text-[11px] leading-4 text-emerald-500">
          Uploaded {lastUploaded.length === 1 ? lastUploaded[0] : `${lastUploaded.length} files`}
        </p>
      )}
    </div>
  );
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function looksLikeReference(f: File): boolean {
  // Images and PDFs are most useful as visual references — bucket them under
  // references/ so they don't pollute the top-level project tree.
  if (f.type.startsWith("image/")) return true;
  if (f.type === "application/pdf") return true;
  if (/\.(png|jpe?g|gif|webp|svg|pdf|sketch|fig)$/i.test(f.name)) return true;
  return false;
}

function EmptyDesign() {
  return (
    <div className="flex flex-col items-start px-6 py-10">
      <div className="flex size-10 items-center justify-center rounded-xl border border-dashed border-border/60">
        <FileText className="size-4 text-muted-foreground/70" />
      </div>
      <p className="mt-3 max-w-md text-[13px] leading-5 text-muted-foreground">
        No files yet. Describe what you want in the chat — Jarvis will ask a few
        questions if the brief is sparse, then generate.
      </p>
    </div>
  );
}

