"use client";

import Link from "next/link";
import {
  ChevronDown,
  Briefcase,
  Plus,
  Zap,
  Search,
  PanelLeft,
  ArrowDownUp,
  Send,
} from "lucide-react";

type SessionSummary = {
  session_id: string;
  title: string;
  status: "needs_input" | "working" | "done";
};

const NAV_BTN =
  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-sidebar-foreground/85 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground transition-colors w-full text-left";

const DOT: Record<SessionSummary["status"], string> = {
  needs_input: "bg-amber-500",
  working: "bg-blue-500",
  done: "border border-sidebar-foreground/40",
};

export function CodeSidebar({
  onNewSession,
  sessions = [],
  activeSessionId,
  onSelectSession,
}: {
  onNewSession: () => void;
  sessions?: SessionSummary[];
  activeSessionId?: string | null;
  onSelectSession?: (id: string) => void;
}) {
  return (
    <aside className="w-[260px] shrink-0 h-full flex flex-col bg-sidebar text-sidebar-foreground border-r border-border/40">
      {/* Branding header */}
      <div className="flex items-center justify-between px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <Link href="/chat" className="whitespace-nowrap font-serif text-[14px] font-bold leading-none text-sidebar-foreground">
            Jarvis&nbsp;Code
          </Link>
          <span className="whitespace-nowrap rounded border border-border/60 px-1.5 py-0.5 text-[9.5px] leading-none text-sidebar-foreground/55">
            Research preview
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <button type="button" aria-label="Toggle sidebar" className="flex size-6 items-center justify-center rounded text-sidebar-foreground/50 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground">
            <PanelLeft className="size-4" />
          </button>
          <button type="button" aria-label="Search" className="flex size-6 items-center justify-center rounded text-sidebar-foreground/50 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground">
            <Search className="size-4" />
          </button>
        </div>
      </div>

      {/* Nav */}
      <div className="px-2 pt-1">
        <button type="button" onClick={onNewSession} className={`${NAV_BTN} bg-sidebar-accent/60 text-sidebar-foreground`}>
          <Plus className="size-4 shrink-0 text-sidebar-foreground/70" />
          New session
        </button>
        <button type="button" className={NAV_BTN}>
          <Zap className="size-4 shrink-0 text-sidebar-foreground/60" />
          Routines
        </button>
        <button type="button" className={NAV_BTN}>
          <Send className="size-4 shrink-0 text-sidebar-foreground/60" />
          <span>Dispatch</span>
          <span className="ml-1 rounded border border-border/60 px-1 py-0.5 text-[9px] leading-none text-sidebar-foreground/50">
            Beta
          </span>
        </button>
        <button type="button" className={NAV_BTN}>
          <Briefcase className="size-4 shrink-0 text-sidebar-foreground/60" />
          Customize
        </button>
        <button type="button" className={NAV_BTN}>
          <ChevronDown className="size-4 shrink-0 text-sidebar-foreground/60" />
          More
        </button>
      </div>

      {/* Recents (sessions with status dots) */}
      <div className="mt-4 flex-1 overflow-y-auto px-2">
        <div className="mb-1 flex items-center justify-between px-2.5">
          <span className="text-[11px] font-medium text-sidebar-foreground/45">Recents</span>
          <button type="button" aria-label="Sort" className="flex size-5 items-center justify-center rounded text-sidebar-foreground/40 hover:text-sidebar-foreground">
            <ArrowDownUp className="size-3" />
          </button>
        </div>
        <div className="space-y-px">
          {sessions.length === 0 ? (
            <div className="px-2.5 py-1 text-[12.5px] text-sidebar-foreground/35">No sessions yet</div>
          ) : (
            sessions.map((s) => (
              <button
                key={s.session_id}
                type="button"
                onClick={() => onSelectSession?.(s.session_id)}
                className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[12.5px] transition-colors ${
                  activeSessionId === s.session_id
                    ? "bg-sidebar-accent/60 text-sidebar-foreground"
                    : "text-sidebar-foreground/75 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                }`}
              >
                <span className={`size-1.5 shrink-0 rounded-full ${DOT[s.status]}`} />
                <span className="truncate">{s.title}</span>
              </button>
            ))
          )}
        </div>
      </div>

      {/* User footer */}
      <footer aria-label="User" className="flex items-center gap-2 border-t border-border/40 px-3 py-2.5">
        <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/20 font-mono text-[9px] font-semibold tracking-wider text-primary">
          UA
        </div>
        <div className="flex-1 truncate text-[12.5px]">
          <span className="text-sidebar-foreground/85">Ulrich</span>
          <span className="text-sidebar-foreground/45"> · Max</span>
        </div>
        <ChevronDown className="size-3.5 text-sidebar-foreground/45" />
      </footer>
    </aside>
  );
}
