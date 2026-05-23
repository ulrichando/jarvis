"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  CheckSquare,
  Loader2,
  MessagesSquare,
  MessageSquare,
  Plus,
  Search,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConversations, useDeleteConversation } from "@/hooks/use-conversations";
import { formatRelativeTime, cn } from "@/lib/utils";
import { MODELS_META } from "@/lib/ai/models-meta";
import { ProviderDot } from "@/components/layout/provider-dot";

// Voice-session listing removed 2026-05-22 along with the rest of the
// hub subsystem — voice transcripts no longer persist anywhere, so
// there is nothing to list. The page is now a typed-chat-only view
// (Drizzle-backed conversations).

type TypedItem = {
  kind: "typed";
  id: string;
  title: string;
  model: string;
  updatedAtMs: number;
  href: string;
};

function toMs(v: string | number): number {
  return typeof v === "number" ? v : new Date(v).getTime();
}

export default function ChatsPage() {
  const { data: typed = [], isLoading: typedLoading } = useConversations();

  const [filter, setFilter] = useState("");
  const [selectMode, setSelectMode] = useState(false);
  // Selected ids — typed chats keyed as `t:<id>`.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkPending, setBulkPending] = useState(false);

  const del = useDeleteConversation();

  // Build the unfiltered base list once. Filtering applies after so we
  // keep "0 of 12 match 'foo'" semantics correct.
  const allTyped = useMemo<TypedItem[]>(
    () =>
      [...typed]
        .map((c) => ({
          kind: "typed" as const,
          id: c.id,
          title: c.title,
          model: c.model,
          updatedAtMs: toMs(c.updatedAt),
          href: `/chat/${c.id}`,
        }))
        .sort((a, b) => b.updatedAtMs - a.updatedAtMs),
    [typed],
  );

  const q = filter.trim().toLowerCase();
  const typedItems = useMemo(
    () =>
      q
        ? allTyped.filter(
            (i) =>
              i.title.toLowerCase().includes(q) ||
              (MODELS_META[i.model]?.label ?? i.model).toLowerCase().includes(q),
          )
        : allTyped,
    [allTyped, q],
  );

  const total = typedItems.length;
  const allTotal = allTyped.length;
  const isLoading = typedLoading;

  const visibleKeys = useMemo(
    () => typedItems.map((i) => `t:${i.id}`),
    [typedItems],
  );

  const allVisibleSelected =
    visibleKeys.length > 0 && visibleKeys.every((k) => selected.has(k));

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const toggleAllVisible = () => {
    setSelected((prev) => {
      if (allVisibleSelected) {
        const next = new Set(prev);
        for (const k of visibleKeys) next.delete(k);
        return next;
      }
      const next = new Set(prev);
      for (const k of visibleKeys) next.add(k);
      return next;
    });
  };

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
      for (const key of selected) {
        if (key.startsWith("t:")) {
          await del.mutateAsync(key.slice(2));
        }
      }
      exitSelectMode();
    } finally {
      setBulkPending(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 px-5">
        <div className="flex items-center gap-2">
          <MessagesSquare className="size-4 text-primary" />
          <h1 className="text-sm font-semibold tracking-tight">All chats</h1>
          {allTotal > 0 && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {q ? `${total}/${allTotal}` : allTotal}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {selectMode ? (
            <>
              <span className="text-[12px] text-muted-foreground">
                {selected.size} selected
              </span>
              <Button
                size="sm"
                variant="outline"
                className="rounded-md"
                onClick={toggleAllVisible}
                disabled={visibleKeys.length === 0}
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
                className="rounded-md"
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
              <Button
                size="sm"
                variant="ghost"
                className="rounded-md"
                onClick={exitSelectMode}
              >
                <X className="size-3.5" />
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                className="rounded-md"
                onClick={() => setSelectMode(true)}
                disabled={allTotal === 0}
              >
                <CheckSquare className="size-3.5" />
                Select
              </Button>
              <Button
                render={<Link href="/chat" />}
                nativeButton={false}
                size="sm"
                variant="outline"
                className="rounded-md"
              >
                <Plus className="size-3.5" />
                New chat
              </Button>
            </>
          )}
        </div>
      </header>

      <div className="border-b border-border/60 px-5 py-2">
        <div className="mx-auto flex max-w-3xl items-center gap-2 rounded-md border border-border/60 bg-card/40 px-2.5 py-1.5">
          <Search className="size-3.5 text-muted-foreground/70" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by title or model…"
            className="flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/60"
          />
          {filter && (
            <button
              type="button"
              onClick={() => setFilter("")}
              className="rounded p-0.5 text-muted-foreground/60 hover:bg-muted hover:text-foreground"
              aria-label="Clear filter"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-5 py-6 space-y-8">
          {isLoading && allTotal === 0 ? (
            <p className="text-sm text-muted-foreground">loading…</p>
          ) : allTotal === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border/60 py-16 text-center">
              <MessagesSquare className="size-6 text-muted-foreground/60" />
              <p className="mt-3 text-sm text-muted-foreground">No chats yet.</p>
              <Button
                render={<Link href="/chat" />}
                nativeButton={false}
                size="sm"
                className="mt-4 rounded-md"
              >
                <Plus className="size-3.5" />
                Start a chat
              </Button>
            </div>
          ) : total === 0 ? (
            <p className="px-3 py-2 text-xs italic text-muted-foreground/70">
              No chats match &quot;{filter}&quot;.
            </p>
          ) : (
            <Section
              icon={<MessageSquare className="size-3.5 text-muted-foreground" />}
              label="Chat"
              count={typedItems.length}
            >
              {typedItems.length === 0 ? (
                <EmptyHint label={q ? "No matches." : "No typed chats yet."} />
              ) : (
                <ul className="space-y-1">
                  {typedItems.map((item) => (
                    <li key={item.id}>
                      <TypedRow
                        item={item}
                        selectMode={selectMode}
                        selected={selected.has(`t:${item.id}`)}
                        onToggle={() => toggle(`t:${item.id}`)}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({
  icon,
  label,
  count,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-2 px-1">
        {icon}
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground/80">
          {label}
        </h2>
        <span className="font-mono text-[10px] text-muted-foreground/60">
          {count}
        </span>
      </div>
      {children}
    </section>
  );
}

function EmptyHint({ label }: { label: string }) {
  return (
    <p className="px-3 py-2 text-xs italic text-muted-foreground/70">{label}</p>
  );
}

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <span
      role="checkbox"
      aria-checked={checked}
      className={cn(
        "shrink-0 rounded border transition-colors",
        checked
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-background group-hover:border-foreground/50",
      )}
    >
      {checked ? (
        <CheckSquare className="size-4 text-primary-foreground" />
      ) : (
        <Square className="size-4 text-transparent" />
      )}
    </span>
  );
}

function TypedRow({
  item,
  selectMode,
  selected,
  onToggle,
}: {
  item: TypedItem;
  selectMode: boolean;
  selected: boolean;
  onToggle: () => void;
}) {
  const meta = MODELS_META[item.model];
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

  const Inner = (
    <>
      <MessageSquare className="size-3.5 shrink-0 text-muted-foreground group-hover:text-primary" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm">{item.title}</div>
        {meta && (
          <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70">
            <ProviderDot provider={meta.provider} />
            {meta.label}
          </div>
        )}
      </div>
      <span className="shrink-0 font-mono text-[11px] uppercase tracking-wider text-muted-foreground/60">
        {formatRelativeTime(item.updatedAtMs)}
      </span>
    </>
  );

  if (selectMode) {
    return (
      <button
        type="button"
        onClick={onToggle}
        className={cn(
          "group flex w-full items-center gap-3 rounded-md border px-3 py-2.5 text-left transition-colors",
          selected
            ? "border-primary/50 bg-primary/5"
            : "border-transparent hover:border-border/80 hover:bg-card/60",
        )}
      >
        <Checkbox checked={selected} />
        {Inner}
      </button>
    );
  }

  return (
    <div className="group flex items-center gap-3 rounded-md border border-transparent px-3 py-2.5 transition-colors hover:border-border/80 hover:bg-card/60">
      <Link href={item.href} className="flex flex-1 min-w-0 items-center gap-3">
        {Inner}
      </Link>
      <DeleteButton
        confirming={confirming}
        pending={del.isPending}
        onClick={handleDelete}
        label={`Delete chat "${item.title}"`}
      />
    </div>
  );
}

function DeleteButton({
  confirming,
  pending,
  onClick,
  label,
}: {
  confirming: boolean;
  pending: boolean;
  onClick: (e: React.MouseEvent) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={confirming ? "Click again to confirm" : "Delete"}
      onClick={onClick}
      disabled={pending}
      className={cn(
        "shrink-0 rounded-md p-1.5 transition-colors",
        "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
        confirming
          ? "bg-destructive/15 text-destructive opacity-100"
          : "text-muted-foreground/60 hover:bg-destructive/10 hover:text-destructive",
        pending && "opacity-100",
      )}
    >
      {pending ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <Trash2 className="size-3.5" />
      )}
    </button>
  );
}
