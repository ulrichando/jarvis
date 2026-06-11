"use client";

import { useEffect, useRef, useState } from "react";
import {
  Cloud,
  ChevronDown,
  ChevronRight,
  Share2,
  PanelRight,
  ExternalLink,
  Pencil,
  Palette,
  FileText,
  Link2,
  Settings2,
  Archive,
  Trash2,
} from "lucide-react";

type CodeEvent = {
  cursor: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: number;
};

const MENU = [
  { icon: ExternalLink, label: "Open in", chord: "", sub: true },
  { icon: Pencil, label: "Rename", chord: "R", sub: false },
  { icon: Palette, label: "Color", chord: "", sub: true },
  { icon: FileText, label: "Transcript view", chord: "", sub: true },
  { icon: Link2, label: "Copy link", chord: "C", sub: false },
  { icon: Settings2, label: "Edit environment", chord: "", sub: false },
] as const;

function str(p: Record<string, unknown>, k: string): string | undefined {
  return typeof p[k] === "string" ? (p[k] as string) : undefined;
}

export function CodeSession({
  sessionId,
  repo,
  title,
}: {
  sessionId: string;
  repo?: string | null;
  title?: string;
}) {
  const [events, setEvents] = useState<CodeEvent[]>([]);
  const [waiting, setWaiting] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const cursorRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEvents([]);
    cursorRef.current = 0;
    setWaiting(true);
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/events?since=${cursorRef.current}`);
        if (r.ok) {
          const j = (await r.json()) as { events: CodeEvent[]; cursor: number };
          if (active && j.events?.length) {
            cursorRef.current = j.cursor;
            setEvents((prev) => [...prev, ...j.events]);
            if (j.events.some((e) => e.type !== "user_prompt")) setWaiting(false);
          }
        }
      } catch {
        /* transient */
      }
      if (active) timer = setTimeout(poll, 1500);
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [sessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  return (
    <div className="flex h-full flex-col">
      {/* Breadcrumb header */}
      <div className="flex items-center justify-between px-4 py-2.5">
        <div className="relative flex items-center gap-1.5 text-[13px]" ref={menuRef}>
          <Cloud className="size-3.5 text-blue-400" />
          <span className="text-foreground/75">{repo ?? "repo"}</span>
          <span className="text-muted-foreground/60">/</span>
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            className="flex items-center gap-1 font-medium text-foreground hover:opacity-80"
          >
            {title ?? "New session"}
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>

          {menuOpen && (
            <div className="absolute left-16 top-full z-50 mt-1 w-[230px] rounded-lg border border-border bg-card p-1 shadow-xl">
              {MENU.map((m) => (
                <button key={m.label} type="button" className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50">
                  <m.icon className="size-3.5 text-muted-foreground" />
                  <span className="flex-1">{m.label}</span>
                  {m.sub && <ChevronRight className="size-3.5 text-muted-foreground" />}
                  {m.chord && <span className="text-[11px] text-muted-foreground/60">{m.chord}</span>}
                </button>
              ))}
              <div className="my-1 border-t border-border/50" />
              <button type="button" className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50">
                <Archive className="size-3.5 text-muted-foreground" />
                <span className="flex-1">Archive</span>
                <span className="text-[11px] text-muted-foreground/60">A</span>
              </button>
              <button type="button" className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-red-500 hover:bg-red-500/10">
                <Trash2 className="size-3.5" />
                <span className="flex-1">Delete</span>
                <span className="text-[11px] text-red-500/60">D</span>
              </button>
            </div>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Share" className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-foreground">
            <Share2 className="size-4" />
          </button>
          <button type="button" aria-label="Layout" className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-foreground">
            <PanelRight className="size-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-3xl space-y-3">
          {events.map((e) => {
            if (e.type === "user_prompt") {
              return (
                <div key={e.cursor} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl bg-accent/40 px-3.5 py-1.5 text-[13px] text-foreground">
                    {str(e.payload, "prompt")}
                  </div>
                </div>
              );
            }
            if (e.type === "status" || e.type === "system") {
              return (
                <button key={e.cursor} type="button" className="flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground">
                  {str(e.payload, "status") ?? "Session event"}
                  <ChevronRight className="size-3.5" />
                </button>
              );
            }
            const text = str(e.payload, "text") ?? str(e.payload, "content") ?? str(e.payload, "message");
            return (
              <div key={e.cursor} className="flex gap-2.5">
                <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-500" />
                <div className="min-w-0 whitespace-pre-wrap break-words text-[13px] text-foreground/90">
                  {text ?? <span className="text-muted-foreground/70">{e.type}</span>}
                </div>
              </div>
            );
          })}

          {waiting && (
            <div className="flex items-center gap-2 pt-1 text-orange-500">
              <span className="inline-block animate-pulse text-[18px] leading-none">✳</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
