"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { Search as SearchIcon, MessageSquare } from "lucide-react";
import { Input } from "@/components/ui/input";
import { useConversations } from "@/hooks/use-conversations";
import { formatRelativeTime } from "@/lib/utils";

export default function SearchPage() {
  const [q, setQ] = useState("");
  const { data: conversations = [], isLoading } = useConversations();

  const results = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return conversations;
    return conversations.filter((c) => c.title.toLowerCase().includes(needle));
  }, [conversations, q]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border/60 p-4">
        <div className="relative mx-auto max-w-2xl">
          <SearchIcon className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search chats by title…"
            className="h-10 pl-9"
          />
        </div>
      </div>
      <div className="mx-auto w-full max-w-2xl flex-1 overflow-y-auto px-4 py-4">
        {isLoading && conversations.length === 0 ? (
          <p className="text-sm text-muted-foreground">loading…</p>
        ) : results.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {q ? `No matches for "${q}".` : "No chats yet."}
          </p>
        ) : (
          <ul className="space-y-1">
            {results.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/chat/${c.id}`}
                  className="flex items-center justify-between gap-3 rounded-md border border-transparent px-3 py-2 transition-colors hover:border-border/80 hover:bg-card/60"
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate text-sm">{c.title}</span>
                  </div>
                  <span className="shrink-0 font-mono text-[11px] uppercase tracking-wider text-muted-foreground/60">
                    {formatRelativeTime(c.updatedAt)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
