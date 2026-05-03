"use client";

// Memories section — the durable user-facts store. Mirrors ChatGPT's
// "Saved memories" / Gemini's "Personalization" / Claude's
// "Memories". State.db.memories is the source of truth; voice agent
// reads top-N at every turn so additions propagate live.
//
// Spec: docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type Memory = {
  memory_id: string;
  content: string;
  category: string;
  source: string;
  source_session_id: string | null;
  created_ts: number;
  updated_ts: number;
  last_used_ts: number | null;
  use_count: number;
};

const CATEGORIES = ["identity", "preference", "project", "fact"] as const;
type Category = (typeof CATEGORIES)[number];

export function MemoriesSection() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [draftCat, setDraftCat] = useState<Category>("fact");
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/memories");
      const body = await r.json();
      setMemories(body.memories ?? []);
    } catch (e) {
      toast.error(`Couldn't load memories: ${String(e)}`);
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  // Live updates via SSE — any upsert/remove triggers a re-fetch.
  useEffect(() => {
    const es = new EventSource("/api/events/stream/memory");
    es.onmessage = () => {
      refresh();
    };
    return () => es.close();
  }, [refresh]);

  const onAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const content = draft.trim();
    if (!content || submitting) return;
    setSubmitting(true);
    try {
      const r = await fetch("/api/memories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, category: draftCat }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: "unknown" }));
        toast.error(`Couldn't save: ${err.error ?? r.status}`);
        return;
      }
      setDraft("");
      toast.success("Memory saved");
      // SSE will re-fetch; nothing else to do.
    } catch (e) {
      toast.error(`Network error: ${String(e)}`);
    } finally {
      setSubmitting(false);
    }
  };

  const onForget = async (id: string) => {
    try {
      const r = await fetch(
        `/api/memories?id=${encodeURIComponent(id)}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: "unknown" }));
        toast.error(`Forget failed: ${err.error ?? r.status}`);
        return;
      }
      // SSE will re-fetch.
    } catch (e) {
      toast.error(`Network error: ${String(e)}`);
    }
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-lg font-semibold">Memories</h2>
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  const grouped = CATEGORIES.map((cat) => ({
    cat,
    items: memories.filter((m) => m.category === cat),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Memories</h2>
        <p className="pb-3 text-sm text-muted-foreground">
          Durable facts JARVIS knows about you. These survive chat
          deletions — they&apos;re how JARVIS remembers your preferences
          and projects across sessions, the way ChatGPT and Claude do.
          API keys and credentials are blocked at the server.
        </p>
      </div>

      <form onSubmit={onAdd} className="flex flex-col gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Add a memory… (e.g. 'I prefer terse replies')"
          maxLength={500}
          rows={2}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
        />
        <div className="flex items-center gap-2">
          <Select
            value={draftCat}
            onValueChange={(v) => setDraftCat(v as Category)}
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CATEGORIES.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <button
            type="submit"
            disabled={!draft.trim() || submitting}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save"}
          </button>
          <span className="ml-auto text-xs text-muted-foreground">
            {draft.length}/500
          </span>
        </div>
      </form>

      {memories.length === 0 ? (
        <div className="rounded-md border border-dashed border-border/60 p-6 text-center text-sm text-muted-foreground">
          No memories yet. JARVIS will add some as you talk.
        </div>
      ) : (
        <div className="space-y-5">
          {grouped.map(({ cat, items }) => (
            <section key={cat}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {cat}
              </h3>
              <ul className="divide-y divide-border/40 rounded-md border border-border/60">
                {items.map((m) => (
                  <MemoryRow
                    key={m.memory_id}
                    memory={m}
                    onForget={() => onForget(m.memory_id)}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function MemoryRow({
  memory,
  onForget,
}: {
  memory: Memory;
  onForget: () => void;
}) {
  return (
    <li className="flex items-start gap-3 px-3 py-2">
      <div className="flex-1 min-w-0">
        <div className="text-sm">{memory.content}</div>
        <div className="text-xs text-muted-foreground">
          {memory.source} · used {memory.use_count}× ·{" "}
          {relativeTime(memory.updated_ts)}
        </div>
      </div>
      <button
        type="button"
        onClick={onForget}
        className="shrink-0 rounded px-2 py-1 text-xs text-destructive hover:bg-destructive/10"
        aria-label="Forget this memory"
      >
        Forget
      </button>
    </li>
  );
}

function relativeTime(ts: number): string {
  const diffSec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}
