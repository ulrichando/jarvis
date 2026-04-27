"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useQuery } from "convex/react";
import { api } from "@convex/_generated/api";
import { MessagesSquare, MessageSquare, Mic, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConversations } from "@/hooks/use-conversations";
import { formatRelativeTime } from "@/lib/utils";
import { MODELS_META } from "@/lib/ai/models-meta";
import { ProviderDot } from "@/components/layout/provider-dot";

// Unified shape for the merged conversation list. `kind` discriminates
// rendering and routing; everything else is precomputed so the render
// stays a flat map.
type ChatItem =
  | {
      kind: "typed";
      key: string;
      title: string;
      model: string;
      updatedAtMs: number;
      href: string;
    }
  | {
      kind: "voice";
      key: string;
      title: string; // last-message preview, used as the row label
      turnCount: number;
      updatedAtMs: number;
      href: string;
    };

// Convex returns numbers; useConversations returns ISO strings. Normalise
// both into a unix-ms number so a single sort key works.
function toMs(v: string | number): number {
  return typeof v === "number" ? v : new Date(v).getTime();
}

export default function ChatsPage() {
  const { data: typed = [], isLoading: typedLoading } = useConversations();

  // Live subscription — re-renders the moment the voice agent appends a
  // new turn (which the agent does after every conversation_item_added
  // event in jarvis_agent.py).
  const voiceSessions = useQuery(api.sessions.list, { limit: 200 });

  const items: ChatItem[] = useMemo(() => {
    const merged: ChatItem[] = [];
    for (const c of typed) {
      merged.push({
        kind: "typed",
        key: `t:${c.id}`,
        title: c.title,
        model: c.model,
        updatedAtMs: toMs(c.updatedAt),
        href: `/chat/${c.id}`,
      });
    }
    for (const s of voiceSessions ?? []) {
      // Skip empty sessions — sometimes a job starts but never lands a
      // turn (mic noise filtered by the directed-at-me check). Showing
      // a "0 turns" empty card is just clutter.
      if (s.turnCount === 0) continue;
      merged.push({
        kind: "voice",
        key: `v:${s.sessionId}`,
        title: s.preview || "(voice conversation)",
        turnCount: s.turnCount,
        updatedAtMs: s.lastTs,
        href: `/chat/voice/${s.sessionId}`,
      });
    }
    merged.sort((a, b) => b.updatedAtMs - a.updatedAtMs);
    return merged;
  }, [typed, voiceSessions]);

  const voiceLoading = voiceSessions === undefined;
  const isLoading = typedLoading || voiceLoading;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 px-5">
        <div className="flex items-center gap-2">
          <MessagesSquare className="size-4 text-primary" />
          <h1 className="text-sm font-semibold tracking-tight">All chats</h1>
          {items.length > 0 && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {items.length}
            </span>
          )}
        </div>
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
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-5 py-6">
          {isLoading && items.length === 0 ? (
            <p className="text-sm text-muted-foreground">loading…</p>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border/60 py-16 text-center">
              <MessagesSquare className="size-6 text-muted-foreground/60" />
              <p className="mt-3 text-sm text-muted-foreground">
                No chats yet.
              </p>
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
          ) : (
            <ul className="space-y-1">
              {items.map((item) => (
                <li key={item.key}>
                  <ChatRow item={item} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function ChatRow({ item }: { item: ChatItem }) {
  if (item.kind === "voice") {
    return (
      <Link
        href={item.href}
        className="group flex items-center gap-3 rounded-md border border-transparent px-3 py-2.5 transition-colors hover:border-border/80 hover:bg-card/60"
      >
        <Mic className="size-3.5 shrink-0 text-muted-foreground group-hover:text-primary" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm">{item.title}</div>
          <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70">
            voice · {item.turnCount} turn{item.turnCount === 1 ? "" : "s"}
          </div>
        </div>
        <span className="shrink-0 font-mono text-[11px] uppercase tracking-wider text-muted-foreground/60">
          {formatRelativeTime(item.updatedAtMs)}
        </span>
      </Link>
    );
  }

  const meta = MODELS_META[item.model];
  return (
    <Link
      href={item.href}
      className="group flex items-center gap-3 rounded-md border border-transparent px-3 py-2.5 transition-colors hover:border-border/80 hover:bg-card/60"
    >
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
    </Link>
  );
}
