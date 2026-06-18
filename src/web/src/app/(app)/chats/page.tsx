"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  Check,
  CheckSquare,
  ChevronDown,
  Loader2,
  Plus,
  Search,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useConversations, useDeleteConversation } from "@/hooks/use-conversations";
import { cn } from "@/lib/utils";

// Voice-session listing removed 2026-05-22 along with the rest of the
// hub subsystem — voice transcripts no longer persist anywhere, so
// there is nothing to list. The page is now a typed-chat-only view
// (Drizzle-backed conversations).

type ChatItem = {
  id: string;
  title: string;
  updatedAtMs: number;
  href: string;
  projectId: string | null;
  projectName: string | null;
};

function toMs(v: string | number): number {
  return typeof v === "number" ? v : new Date(v).getTime();
}

// Verbose, Claude.ai-style relative timestamps: "20 hours ago",
// "yesterday", "2 days ago", then absolute "Jun 6" (+ year if not this
// year) past a week. Deliberately NOT the shared `formatRelativeTime`
// (lib/utils) — that one is the compact "20h ago" form the sidebar and
// search rely on; this page wants the spelled-out form from the design.
function formatChatTimestamp(ms: number): string {
  const now = Date.now();
  const diff = now - ms;
  if (diff < 0) return "just now";
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  const day = Math.floor(hr / 24);
  if (day === 1) return "yesterday";
  if (day < 7) return `${day} days ago`;
  const d = new Date(ms);
  const sameYear = d.getFullYear() === new Date(now).getFullYear();
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

export default function ChatsPage() {
  const { data: conversations = [], isLoading } = useConversations();

  const [filter, setFilter] = useState("");
  const [projectFilter, setProjectFilter] = useState<string>("all"); // "all" | projectId
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkPending, setBulkPending] = useState(false);

  const del = useDeleteConversation();

  // Unfiltered base list (already sorted newest-first by the API, but
  // re-sort defensively so optimistic cache updates can't reorder it).
  const allItems = useMemo<ChatItem[]>(
    () =>
      [...conversations]
        .map((c) => ({
          id: c.id,
          title: c.title,
          updatedAtMs: toMs(c.updatedAt),
          href: `/chat/${c.id}`,
          projectId: c.projectId ?? null,
          projectName: c.projectName ?? null,
        }))
        .sort((a, b) => b.updatedAtMs - a.updatedAtMs),
    [conversations],
  );

  // Projects that actually have chats in the list — the dimension
  // "Filter by" offers (matches the picture). Hidden when there are none.
  const projects = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of allItems) {
      if (c.projectId && c.projectName) m.set(c.projectId, c.projectName);
    }
    return [...m.entries()]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [allItems]);

  // Always show "Filter by All" when there are chats (matches the
  // reference) — the project list inside only appears when projects exist.
  const showFilter = allItems.length > 0;
  const projectFilterActive =
    projectFilter !== "all" && projects.some((p) => p.id === projectFilter);

  const q = filter.trim().toLowerCase();
  const items = useMemo(() => {
    let list = allItems;
    if (projectFilterActive) {
      list = list.filter((i) => i.projectId === projectFilter);
    }
    if (q) {
      list = list.filter(
        (i) =>
          i.title.toLowerCase().includes(q) ||
          (i.projectName?.toLowerCase().includes(q) ?? false),
      );
    }
    return list;
  }, [allItems, projectFilter, projectFilterActive, q]);

  const total = items.length;
  const allTotal = allItems.length;
  const filterActive = q !== "" || projectFilterActive;
  const activeProjectName = projectFilterActive
    ? projects.find((p) => p.id === projectFilter)?.name ?? "All"
    : "All";

  const visibleIds = useMemo(() => items.map((i) => i.id), [items]);
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleAllVisible = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) for (const id of visibleIds) next.delete(id);
      else for (const id of visibleIds) next.add(id);
      return next;
    });

  const exitSelectMode = () => {
    setSelectMode(false);
    setSelected(new Set());
  };

  const deleteSelected = async () => {
    if (selected.size === 0) return;
    const ok = window.confirm(
      `Delete ${selected.size} chat${selected.size === 1 ? "" : "s"}? This can't be undone.`,
    );
    if (!ok) return;
    setBulkPending(true);
    try {
      for (const id of selected) await del.mutateAsync(id);
      exitSelectMode();
    } finally {
      setBulkPending(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      {/* Pinned header + search. Same max-w column as the list so the
          title, search bar and rows all share one left/right edge. */}
      <div className="shrink-0">
        <div className="mx-auto w-full max-w-4xl px-6 pt-8 pb-4">
          <div className="flex items-center justify-between gap-4">
            <h1 className="text-3xl font-semibold tracking-tight">Chats</h1>

            <div className="flex items-center gap-1">
              {selectMode ? (
                <>
                  <span className="mr-1 text-[13px] text-muted-foreground">
                    {selected.size} selected
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={toggleAllVisible}
                    disabled={visibleIds.length === 0}
                  >
                    {allVisibleSelected ? (
                      <CheckSquare className="size-3.5" />
                    ) : (
                      <Square className="size-3.5" />
                    )}
                    {allVisibleSelected ? "Clear" : "Select all"}
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={deleteSelected}
                    disabled={selected.size === 0 || bulkPending}
                  >
                    {bulkPending ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="size-3.5" />
                    )}
                    Delete{selected.size > 0 ? ` ${selected.size}` : ""}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={exitSelectMode}>
                    <X className="size-3.5" />
                    Cancel
                  </Button>
                </>
              ) : (
                <>
                  {showFilter && (
                    <FilterDropdown
                      projects={projects}
                      value={projectFilter}
                      activeName={activeProjectName}
                      onChange={setProjectFilter}
                    />
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-9 gap-1.5 px-3.5"
                    onClick={() => setSelectMode(true)}
                    disabled={allTotal === 0}
                  >
                    <CheckSquare className="size-3.5" />
                    Select chats
                  </Button>
                  <Button
                    render={<Link href="/chat" />}
                    nativeButton={false}
                    size="sm"
                    variant="outline"
                    className="h-9 gap-1.5 px-3.5"
                  >
                    <Plus className="size-3.5" />
                    New chat
                  </Button>
                </>
              )}
            </div>
          </div>

          <div className="relative mt-5">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/70" />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search chats..."
              className="h-12 w-full rounded-xl border border-border/70 bg-card/30 pl-10 pr-10 text-[15px] outline-none transition-colors placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/40"
            />
            {filter && (
              <button
                type="button"
                onClick={() => setFilter("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground/60 transition-colors hover:bg-muted hover:text-foreground"
                aria-label="Clear search"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Scrollable list */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-6 pb-12">
          {isLoading && allTotal === 0 ? (
            <p className="px-2 py-6 text-sm text-muted-foreground">loading…</p>
          ) : allTotal === 0 ? (
            <div className="mt-6 flex flex-col items-center justify-center rounded-xl border border-dashed border-border/60 py-16 text-center">
              <p className="text-sm text-muted-foreground">No chats yet.</p>
              <Button
                render={<Link href="/chat" />}
                nativeButton={false}
                size="sm"
                className="mt-4"
              >
                <Plus className="size-3.5" />
                Start a chat
              </Button>
            </div>
          ) : total === 0 ? (
            <p className="px-2 py-6 text-sm text-muted-foreground">
              No chats match {q ? <>&quot;{filter}&quot;</> : <>that filter</>}.
            </p>
          ) : (
            <>
              {filterActive && (
                <p className="px-1 pt-1 pb-2 text-xs text-muted-foreground/70">
                  {total} of {allTotal}
                </p>
              )}
              <ul>
                {items.map((item) => (
                  <li key={item.id}>
                    <ChatRow
                      item={item}
                      selectMode={selectMode}
                      selected={selected.has(item.id)}
                      onToggle={() => toggle(item.id)}
                    />
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function FilterDropdown({
  projects,
  value,
  activeName,
  onChange,
}: {
  projects: { id: string; name: string }[];
  value: string;
  activeName: string;
  onChange: (v: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="secondary"
            size="sm"
            className="h-9 gap-1.5 rounded-lg px-3.5 font-normal"
          />
        }
      >
        <span className="text-muted-foreground">Filter by</span>
        <span className="font-medium text-foreground">{activeName}</span>
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="bottom"
        sideOffset={6}
        className="min-w-52 p-1"
      >
        <DropdownMenuItem
          onClick={() => onChange("all")}
          className="gap-2 py-1.5 text-[13px]"
        >
          <Check
            className={value === "all" ? "size-3.5 text-primary" : "size-3.5 opacity-0"}
          />
          All chats
        </DropdownMenuItem>
        {projects.length > 0 && <DropdownMenuSeparator />}
        {projects.map((p) => (
          <DropdownMenuItem
            key={p.id}
            onClick={() => onChange(p.id)}
            className="gap-2 py-1.5 text-[13px]"
          >
            <Check
              className={value === p.id ? "size-3.5 text-primary" : "size-3.5 opacity-0"}
            />
            <span className="truncate">{p.name}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ProjectTag({ name }: { name: string }) {
  return (
    <span className="hidden max-w-40 shrink-0 truncate rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] text-muted-foreground/90 sm:inline-block">
      {name}
    </span>
  );
}

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <span
      role="checkbox"
      aria-checked={checked}
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded border transition-colors",
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-background group-hover:border-foreground/50",
      )}
    >
      {checked && <Check className="size-3" />}
    </span>
  );
}

function ChatRow({
  item,
  selectMode,
  selected,
  onToggle,
}: {
  item: ChatItem;
  selectMode: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const del = useDeleteConversation();
  const [confirming, setConfirming] = useState(false);

  const handleDelete = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirming) {
      setConfirming(true);
      setTimeout(() => setConfirming(false), 3000);
      return;
    }
    del.mutate(item.id);
  };

  if (selectMode) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          "group flex w-full items-center gap-3 border-b border-border/40 px-3 py-3.5 text-left transition-colors",
          selected ? "bg-primary/5" : "hover:bg-muted/30",
        )}
      >
        <Checkbox checked={selected} />
        <span className="min-w-0 flex-1 truncate text-[15px]">{item.title}</span>
        {item.projectName && <ProjectTag name={item.projectName} />}
        <span className="shrink-0 text-[13px] text-muted-foreground">
          {formatChatTimestamp(item.updatedAtMs)}
        </span>
      </button>
    );
  }

  return (
    <div className="group relative flex items-center border-b border-border/40 transition-colors hover:bg-muted/30">
      <Link
        href={item.href}
        className="flex min-w-0 flex-1 items-center gap-3 px-3 py-3.5"
      >
        <span className="min-w-0 flex-1 truncate text-[15px]">{item.title}</span>
        {item.projectName && <ProjectTag name={item.projectName} />}
        {/* Date fades out on hover so the delete button can take its
            place at the right edge — no layout shift either way. */}
        <span className="shrink-0 text-[13px] text-muted-foreground transition-opacity group-hover:opacity-0">
          {formatChatTimestamp(item.updatedAtMs)}
        </span>
      </Link>
      <button
        type="button"
        aria-label={`Delete chat "${item.title}"`}
        title={confirming ? "Click again to confirm" : "Delete"}
        onClick={handleDelete}
        disabled={del.isPending}
        className={cn(
          "absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1.5 opacity-0 transition-all",
          "group-hover:opacity-100 focus-visible:opacity-100",
          confirming
            ? "bg-destructive/15 text-destructive opacity-100"
            : "text-muted-foreground/60 hover:bg-destructive/10 hover:text-destructive",
          del.isPending && "opacity-100",
        )}
      >
        {del.isPending ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Trash2 className="size-3.5" />
        )}
      </button>
    </div>
  );
}
