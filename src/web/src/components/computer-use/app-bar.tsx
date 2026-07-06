"use client";
import { Monitor, ShieldCheck, Hand, Square, MoreVertical, Plug, Unplug, RotateCcw, RotateCw, ChevronDown } from "lucide-react";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { SessionInfo } from "@/lib/computer-use/timeline";

export type ConnStatus = "connected" | "connecting" | "disconnected" | "offline";

const SESSION_DOT: Record<string, string> = {
  running: "bg-emerald-500",
  waiting_approval: "bg-amber-500",
  error: "bg-destructive",
};

export function CuAppBar({
  connStatus, sessionId, supervised, takeover, connected, running, hasThread,
  sessions, onLoadSessions, onPickSession,
  onToggleMode, onToggleTakeover, onToggleConnected, onNewChat, onStop, onRefresh,
}: {
  connStatus: ConnStatus; sessionId: string; supervised: boolean; takeover: boolean;
  connected: boolean; running: boolean; hasThread: boolean;
  sessions: SessionInfo[]; onLoadSessions: () => void; onPickSession: (s: SessionInfo) => void;
  onToggleMode: () => void; onToggleTakeover: () => void; onToggleConnected: () => void;
  onNewChat: () => void; onStop: () => void; onRefresh: () => void;
}) {
  const dot = connStatus === "connected" ? "bg-emerald-500" : connStatus === "connecting" ? "bg-amber-500" : "bg-muted-foreground";
  return (
    <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-border/40 bg-card/30 px-4">
      <div className="flex items-center gap-2">
        <span className="grid size-[26px] place-items-center rounded-lg bg-primary/10 text-primary"><Monitor className="size-4" /></span>
        <span className="text-[14.5px] font-medium tracking-tight">Computer Use</span>
      </div>
      <span role="status" aria-live="polite" aria-label={`Desktop ${connStatus}`}
        className="inline-flex items-center gap-1.5 rounded-full border border-border/40 bg-card px-2.5 py-0.5 text-[11.5px] text-muted-foreground">
        <span className={`size-1.5 rounded-full ${dot}`} />{connStatus[0].toUpperCase() + connStatus.slice(1)}
      </span>
      {sessionId && (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<button title="Sessions" aria-label="Sessions" onClick={onLoadSessions}
              className="hidden items-center gap-1 font-mono text-[10.5px] text-muted-foreground/80 transition-colors hover:text-foreground sm:inline-flex" />}>
            session · {sessionId.slice(0, 4)}…{sessionId.slice(-3)}<ChevronDown className="size-3" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="max-w-80 min-w-64">
            {sessions.length === 0 && <DropdownMenuItem disabled>No sessions yet</DropdownMenuItem>}
            {sessions.map((s) => (
              <DropdownMenuItem key={s.id} onClick={() => { if (s.id !== sessionId) onPickSession(s); }}>
                <span className={`size-1.5 shrink-0 rounded-full ${SESSION_DOT[s.status] ?? "bg-muted-foreground/60"}`} />
                <span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">{s.id.slice(0, 4)}…{s.id.slice(-3)}</span>
                <span className="truncate text-[12px]">{s.task || "(no task yet)"}</span>
                {s.id === sessionId && <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">current</span>}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      <div className="flex-1" />

      <div role="radiogroup" aria-label="Approval mode" className="flex gap-0.5 rounded-lg border border-border/40 bg-card/40 p-0.5">
        <button role="radio" aria-checked={supervised} aria-label="Supervised" onClick={() => { if (!supervised) onToggleMode(); }}
          className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${supervised ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground"}`}>
          <ShieldCheck className="size-3.5" />Supervised
        </button>
        <button role="radio" aria-checked={!supervised} aria-label="Auto" onClick={() => { if (supervised) onToggleMode(); }}
          className={`rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${!supervised ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground"}`}>
          Auto
        </button>
      </div>

      {connected && (
        <button onClick={onToggleTakeover} title={takeover ? "Give control back to Jarvis" : "Take control of the desktop"}
          className={`inline-flex h-[30px] items-center gap-1.5 rounded-lg border px-3 text-[12px] transition-colors ${takeover ? "border-primary/60 bg-primary/10 text-primary" : "border-border/40 bg-card hover:border-border"}`}>
          <Hand className="size-3.5" />{takeover ? "Give control" : "Take control"}
        </button>
      )}

      {running && (
        <button onClick={onStop} title="Stop the agent" className="inline-flex h-[30px] items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-3 text-[12px] text-destructive transition-colors hover:border-destructive/60">
          <Square className="size-3.5" />Stop
        </button>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger render={<button title="More" className="grid size-[30px] place-items-center rounded-lg border border-border/40 bg-card text-muted-foreground transition-colors hover:border-border" />}>
          <MoreVertical className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-44">
          <DropdownMenuItem onClick={onToggleConnected}>{connected ? <><Unplug className="size-4" /> Disconnect</> : <><Plug className="size-4" /> Connect</>}</DropdownMenuItem>
          {hasThread && <DropdownMenuItem onClick={onNewChat}><RotateCcw className="size-4" /> New session</DropdownMenuItem>}
          <DropdownMenuItem onClick={onRefresh}><RotateCw className="size-4" /> Refresh</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
