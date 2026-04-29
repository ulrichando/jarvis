"use client";

import {
  ChevronUp,
  File as FileIcon,
  FileCode,
  FileText,
  Folder,
  Loader2,
  Palette,
  RefreshCw,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
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
  format,
  onStarter,
  onToggleBrand,
  brandActive,
}: {
  workspaceId: string;
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
  onUpClick?: () => void;
  refetchKey?: number;
  format?: Format;
  onStarter?: (prompt: string) => void;
  /** Toggle the brand-editor panel from inside this toolbar. The header
   *  no longer carries a Brand button — Present is the only header action. */
  onToggleBrand?: () => void;
  brandActive?: boolean;
}) {
  const qc = useQueryClient();
  const { data: entries = [], isLoading, refetch } = useQuery({
    queryKey: ["design-tree", workspaceId, refetchKey ?? 0],
    queryFn: () => apiTree(workspaceId, ""),
  });

  const del = useMutation({
    mutationFn: (path: string) => apiDeleteEntry(workspaceId, path),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["design-tree", workspaceId] });
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
          {onToggleBrand && (
            <Button
              variant={brandActive ? "secondary" : "ghost"}
              size="sm"
              className="rounded-md"
              aria-pressed={brandActive}
              onClick={onToggleBrand}
              title="Brand — colors, fonts, voice that apply to every generation"
            >
              <Sparkles className="size-3.5" />
              Brand
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
          <EmptyDesign format={format} onStarter={onStarter} />
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
                />
              );
            })}
          </div>
        )}
      </div>

      {/* Drop zone */}
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
}: {
  label: string;
  entries: TreeEntry[];
  selectedPath: string | null;
  onSelectFile: (entry: TreeEntry) => void;
  onDelete: (path: string) => void;
  deletingPath: string | null;
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
            onDelete={() => onDelete(e.path)}
            deleting={deletingPath === e.path}
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
  onDelete,
  deleting,
}: {
  entry: TreeEntry;
  selected: boolean;
  onClick: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  // We don't have per-file mtime in the tree response yet; placeholder.
  const updated = "—";

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
      className={cn(
        "group flex w-full cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-left transition-colors",
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

function EmptyDesign({
  format,
  onStarter,
}: {
  format?: Format;
  onStarter?: (prompt: string) => void;
}) {
  // When format is given (rare — chips are removed by default), show only
  // that format's starters. Otherwise mix one starter per format so the
  // user sees the full range of what's possible.
  const items: { format: Format; title: string; prompt: string }[] = format
    ? STARTERS[format].map((s) => ({ format, ...s }))
    : (Object.keys(STARTERS) as Format[]).map((f) => ({
        format: f,
        ...STARTERS[f][0],
      }));

  return (
    <div className="flex flex-col px-6 py-10">
      <div className="flex flex-col items-start gap-2">
        <div className="flex size-10 items-center justify-center rounded-xl border border-dashed border-border/60">
          <FileText className="size-4 text-muted-foreground/70" />
        </div>
        <p className="max-w-md text-[13px] leading-5 text-muted-foreground">
          No design files yet. Describe what you want in the chat — slides, a
          prototype, a landing page, a one-pager, an infographic — Jarvis figures
          out the format. Or pick a starter below to prefill the composer.
        </p>
      </div>

      {onStarter && (
        <div className="mt-5 grid gap-2">
          {items.map((s) => (
            <button
              key={`${s.format}:${s.title}`}
              type="button"
              onClick={() => onStarter(s.prompt)}
              className={cn(
                "group flex flex-col items-start gap-1.5 rounded-lg border border-border/60 bg-background/60 px-3 py-2.5 text-left",
                "transition-colors hover:border-foreground/30 hover:bg-background",
              )}
            >
              <div className="flex w-full items-center gap-2">
                <span className="text-[13px] font-semibold text-foreground">
                  {s.title}
                </span>
                <span className="ml-auto rounded bg-muted/70 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                  {FORMAT_LABEL[s.format]}
                </span>
              </div>
              <span className="line-clamp-2 text-[12px] leading-4 text-muted-foreground">
                {s.prompt}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

