"use client";

import {
  ArrowLeft,
  ChevronDown,
  Circle,
  LayoutGrid,
  Pin,
  Plus,
  RotateCw,
  SlidersHorizontal,
  Zap,
} from "lucide-react";

const PLACEHOLDER_SESSIONS = [
  { id: "1", icon: Circle, label: "Jarvis session" },
  { id: "2", icon: Zap, label: "Debug with Jarvis" },
];

export function CodeSidebar({ onNewSession }: { onNewSession: () => void }) {
  return (
    <div className="w-[230px] shrink-0 h-full flex flex-col bg-sidebar text-sidebar-foreground">
      {/* Nav */}
      <div>
        <button
          type="button"
          onClick={onNewSession}
          className="flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground transition-colors w-full text-left"
        >
          <Plus className="size-4 shrink-0 text-sidebar-foreground/60" />
          New session
        </button>
        <button
          type="button"
          className="flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground transition-colors w-full text-left"
        >
          <RotateCw className="size-4 shrink-0 text-sidebar-foreground/60" />
          Routines
        </button>
        <button
          type="button"
          className="flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground transition-colors w-full text-left"
        >
          <SlidersHorizontal className="size-4 shrink-0 text-sidebar-foreground/60" />
          Customize
        </button>
        <button
          type="button"
          className="flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-sidebar-foreground/80 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground transition-colors w-full text-left"
        >
          <ChevronDown className="size-4 shrink-0 text-sidebar-foreground/60" />
          More
        </button>
      </div>

      {/* Pinned */}
      <div className="mt-4 px-3">
        <div className="text-[11px] text-sidebar-foreground/45 mb-1">Pinned</div>
        <div className="flex items-center gap-2 py-1 text-[12.5px] text-sidebar-foreground/40">
          <Pin className="size-3.5 shrink-0" />
          <span>Drag to pin</span>
        </div>
      </div>

      {/* Recents */}
      <div className="mt-4 flex-1 overflow-y-auto px-3">
        <div className="text-[11px] text-sidebar-foreground/45 mb-1">Recents</div>
        <div className="space-y-px">
          {PLACEHOLDER_SESSIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className="flex items-center gap-2 w-full rounded-md px-2 py-1.5 text-[12.5px] text-sidebar-foreground/75 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground transition-colors text-left"
            >
              <s.icon className="size-3 shrink-0 text-sidebar-foreground/50" />
              <span className="truncate">{s.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-border/40 px-3 py-2 flex items-center gap-2">
        <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/20 font-mono text-[10px] font-semibold tracking-wider text-primary">
          UA
        </div>
        <span className="flex-1 truncate text-[12.5px] text-sidebar-foreground/80">Ulrich Ando</span>
        <button
          type="button"
          aria-label="Layout"
          className="flex size-6 items-center justify-center rounded text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent/50 transition-colors"
        >
          <LayoutGrid className="size-3.5" />
        </button>
        <button
          type="button"
          aria-label="Back"
          className="flex size-6 items-center justify-center rounded text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent/50 transition-colors"
        >
          <ArrowLeft className="size-3.5" />
        </button>
      </div>
    </div>
  );
}
