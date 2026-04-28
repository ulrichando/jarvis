"use client";

import { useQuery } from "convex/react";
import { api } from "@convex/_generated/api";
import { ArrowUp, Mic } from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams } from "next/navigation";
import type { UIMessage } from "ai";
import { toast } from "sonner";
import { Thread } from "@/components/chat/thread";
import { Button } from "@/components/ui/button";
import { formatRelativeTime } from "@/lib/utils";

// Voice transcript view, two-way:
//
//  - Subscribes to Convex `turns:bySession` for live updates from the
//    voice agent.
//  - Composer at the bottom POSTs to the voice-client's /user-input
//    endpoint, which publishes a data packet that the voice-agent
//    treats as a synthetic user turn (full LLM → TTS round trip).
//    Both sides of the round trip land in conversations.db, mirror to
//    Convex, and re-render this page within ~50 ms.
//
//  Distinct from /chat/[id] (typed chat in Drizzle). This route is
//  scoped to a single voice session and edits go through the voice
//  pipeline so JARVIS responds out loud.

const VOICE_CLIENT_URL =
  process.env.NEXT_PUBLIC_VOICE_CLIENT_URL ?? "http://127.0.0.1:8767";

type ConvexTurn = {
  _id: string;
  ts: number;
  role: "user" | "assistant";
  text: string;
};

function turnsToUIMessages(turns: ConvexTurn[]): UIMessage[] {
  return turns.map(
    (t) =>
      ({
        id: t._id,
        role: t.role,
        parts: [{ type: "text", text: t.text }],
      } as UIMessage),
  );
}

export default function VoiceSessionPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params?.sessionId ?? "";

  const turns = useQuery(api.turns.bySession, { sessionId });

  const messages = useMemo(
    () => (turns ? turnsToUIMessages(turns) : []),
    [turns],
  );

  const lastTs = turns && turns.length > 0 ? turns[turns.length - 1].ts : null;

  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const r = await fetch(`${VOICE_CLIENT_URL}/user-input`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.error ?? `HTTP ${r.status}`);
      }
      setInput("");
      // Don't append optimistically — the voice-agent's
      // conversation_item_added handler will mirror the user turn into
      // Convex within ~100 ms and the live subscription will render it
      // for free. Optimistic local appends would race that and risk
      // duplicates.
    } catch (e) {
      toast.error(
        `Couldn't reach voice agent: ${
          e instanceof Error ? e.message : "unknown"
        }`,
      );
    } finally {
      setSending(false);
      taRef.current?.focus();
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send();
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border/60 px-5">
        <Mic className="size-4 text-primary" />
        <h1 className="text-sm font-semibold tracking-tight">
          Voice conversation
        </h1>
        {turns && (
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {turns.length} turn{turns.length === 1 ? "" : "s"}
            {lastTs && (
              <>
                {" · "}
                last {formatRelativeTime(lastTs)}
              </>
            )}
          </span>
        )}
      </header>

      <div className="flex-1 overflow-y-auto">
        {turns === undefined ? (
          <p className="px-5 py-8 text-sm text-muted-foreground">loading…</p>
        ) : turns.length === 0 ? (
          <p className="px-5 py-8 text-sm text-muted-foreground">
            No turns in this session.
          </p>
        ) : (
          <Thread messages={messages} isStreaming={false} />
        )}
      </div>

      {/* Composer — types into the live voice session via /user-input.
          JARVIS responds via voice (TTS) AND the response appears in
          this transcript live. */}
      <form
        onSubmit={onSubmit}
        className="shrink-0 border-t border-border/60 bg-background px-4 py-3"
      >
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-border/60 bg-card/40 px-3 py-2 focus-within:border-primary/50">
          <textarea
            ref={taRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            placeholder="Type a follow-up — JARVIS answers out loud"
            rows={1}
            disabled={sending}
            className="min-h-6 max-h-40 flex-1 resize-none bg-transparent text-[14.5px] leading-6 outline-none placeholder:text-muted-foreground"
          />
          <Button
            type="submit"
            size="icon-sm"
            disabled={!input.trim() || sending}
            className="rounded-full"
            aria-label="Send"
          >
            <ArrowUp className="size-4" />
          </Button>
        </div>
        <p className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] uppercase tracking-wider text-muted-foreground/60">
          Enter sends · Shift+Enter newline · JARVIS replies via voice
        </p>
      </form>
    </div>
  );
}
