"use client";

import Link from "next/link";
import { MessagesSquare, MessageSquare, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConversations } from "@/hooks/use-conversations";
import { formatRelativeTime } from "@/lib/utils";
import { MODELS_META } from "@/lib/ai/models-meta";
import { ProviderDot } from "@/components/layout/provider-dot";

export default function ChatsPage() {
  const { data: conversations = [], isLoading } = useConversations();

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 px-5">
        <div className="flex items-center gap-2">
          <MessagesSquare className="size-4 text-primary" />
          <h1 className="text-sm font-semibold tracking-tight">All chats</h1>
          {conversations.length > 0 && (
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {conversations.length}
            </span>
          )}
        </div>
        <Button
          render={<Link href="/chat" />}
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
          {isLoading && conversations.length === 0 ? (
            <p className="text-sm text-muted-foreground">loading…</p>
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border/60 py-16 text-center">
              <MessagesSquare className="size-6 text-muted-foreground/60" />
              <p className="mt-3 text-sm text-muted-foreground">
                No chats yet.
              </p>
              <Button
                render={<Link href="/chat" />}
                size="sm"
                className="mt-4 rounded-md"
              >
                <Plus className="size-3.5" />
                Start a chat
              </Button>
            </div>
          ) : (
            <ul className="space-y-1">
              {conversations.map((c) => {
                const meta = MODELS_META[c.model];
                return (
                  <li key={c.id}>
                    <Link
                      href={`/chat/${c.id}`}
                      className="group flex items-center gap-3 rounded-md border border-transparent px-3 py-2.5 transition-colors hover:border-border/80 hover:bg-card/60"
                    >
                      <MessageSquare className="size-3.5 shrink-0 text-muted-foreground group-hover:text-primary" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm">{c.title}</div>
                        {meta && (
                          <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70">
                            <ProviderDot provider={meta.provider} />
                            {meta.label}
                          </div>
                        )}
                      </div>
                      <span className="shrink-0 font-mono text-[11px] uppercase tracking-wider text-muted-foreground/60">
                        {formatRelativeTime(c.updatedAt)}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
