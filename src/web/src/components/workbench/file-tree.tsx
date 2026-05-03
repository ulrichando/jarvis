"use client";

import { useEffect, useState } from "react";
import {
  ChevronRight,
  File as FileIcon,
  Folder,
  FolderOpen,
  Plus,
  Trash2,
} from "lucide-react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import {
  apiTree,
  apiCreateEntry,
  apiDeleteEntry,
  type TreeEntry,
} from "@/lib/workspace/client";
import { useEditedFiles } from "@/stores/edited-files";

type Props = {
  workspaceId: string;
  activePath: string | null;
  onOpen: (path: string) => void;
};

export function FileTree({ workspaceId, activePath, onOpen }: Props) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border/50">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Files
        </span>
        <NewEntryButton workspaceId={workspaceId} parentPath="" />
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        <DirNode
          workspaceId={workspaceId}
          path=""
          depth={0}
          activePath={activePath}
          onOpen={onOpen}
          forceOpen
        />
      </div>
    </div>
  );
}

function DirNode({
  workspaceId,
  path,
  depth,
  activePath,
  onOpen,
  forceOpen,
}: {
  workspaceId: string;
  path: string;
  depth: number;
  activePath: string | null;
  onOpen: (path: string) => void;
  forceOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!forceOpen);
  const { data: entries = [] } = useQuery({
    queryKey: ["ws", workspaceId, "tree", path],
    queryFn: () => apiTree(workspaceId, path),
    enabled: open,
  });

  if (!open && !forceOpen) return null;

  return (
    <div>
      {entries.map((e) => (
        <EntryRow
          key={e.path}
          workspaceId={workspaceId}
          entry={e}
          depth={depth}
          activePath={activePath}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

function EntryRow({
  workspaceId,
  entry,
  depth,
  activePath,
  onOpen,
}: {
  workspaceId: string;
  entry: TreeEntry;
  depth: number;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();

  const del = useMutation({
    mutationFn: () => apiDeleteEntry(workspaceId, entry.path),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "tree"] });
    },
  });

  const isFile = entry.type === "file";
  const active = activePath === entry.path;
  // "Recently edited by JARVIS" indicator. The chat layer pushes paths
  // into the store on each successful file action; we render a small
  // cyan dot next to the filename for ~60s. We re-render once a second
  // so the indicator fades away on its own without needing the store
  // to push expiration events.
  const wasRecentlyEdited = useEditedFiles((s) => s.wasRecentlyEdited);
  const [, force] = useState(0);
  useEffect(() => {
    if (!isFile) return;
    if (!wasRecentlyEdited(workspaceId, entry.path)) return;
    const t = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [isFile, workspaceId, entry.path, wasRecentlyEdited]);
  const recentlyEdited =
    isFile && wasRecentlyEdited(workspaceId, entry.path);

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 px-2 py-0.5 text-[13px] cursor-pointer hover:bg-accent/50",
          active && "bg-accent text-accent-foreground",
        )}
        style={{ paddingLeft: 8 + depth * 12 }}
        onClick={() => {
          if (isFile) onOpen(entry.path);
          else setExpanded((v) => !v);
        }}
      >
        {isFile ? (
          <>
            <span className="w-3.5" />
            <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
          </>
        ) : (
          <>
            <ChevronRight
              className={cn(
                "size-3.5 shrink-0 text-muted-foreground transition-transform",
                expanded && "rotate-90",
              )}
            />
            {expanded ? (
              <FolderOpen className="size-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <Folder className="size-3.5 shrink-0 text-muted-foreground" />
            )}
          </>
        )}
        <span className="truncate flex-1">{entry.name}</span>
        {recentlyEdited && (
          <span
            title="Recently edited by JARVIS"
            className="size-1.5 rounded-full bg-cyan-400 shadow-[0_0_4px_rgba(34,211,238,0.7)] animate-pulse shrink-0"
            aria-label="recently edited"
          />
        )}
        <button
          onClick={(ev) => {
            ev.stopPropagation();
            if (confirm(`Delete ${entry.path}?`)) del.mutate();
          }}
          className="opacity-0 group-hover:opacity-60 hover:opacity-100 transition-opacity"
          aria-label="delete"
        >
          <Trash2 className="size-3" />
        </button>
      </div>
      {!isFile && expanded && (
        <DirNode
          workspaceId={workspaceId}
          path={entry.path}
          depth={depth + 1}
          activePath={activePath}
          onOpen={onOpen}
          forceOpen
        />
      )}
    </div>
  );
}

function NewEntryButton({
  workspaceId,
  parentPath,
}: {
  workspaceId: string;
  parentPath: string;
}) {
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: ({ path, type }: { path: string; type: "file" | "dir" }) =>
      apiCreateEntry(workspaceId, path, type),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "tree"] });
    },
  });

  const onClick = () => {
    const name = prompt(
      "New file or folder (end with / for folder):",
      "untitled.ts",
    );
    if (!name) return;
    const isDir = name.endsWith("/");
    const clean = name.replace(/\/+$/, "");
    const fullPath = parentPath ? `${parentPath}/${clean}` : clean;
    create.mutate({ path: fullPath, type: isDir ? "dir" : "file" });
  };

  return (
    <button
      onClick={onClick}
      className="text-muted-foreground hover:text-foreground"
      aria-label="new file"
    >
      <Plus className="size-3.5" />
    </button>
  );
}
