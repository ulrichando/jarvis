"use client";

import { useRef, useEffect, useState } from "react";
import {
  Cloud,
  Monitor,
  Code2,
  GitBranch,
  Plus,
  CornerDownLeft,
  ChevronDown,
  Circle,
  Loader2,
  Check,
  ExternalLink,
  RefreshCw,
} from "lucide-react";

const TOOLBAR_ICON_BTN =
  "flex size-6 items-center justify-center rounded text-foreground/50 hover:bg-accent/40 hover:text-foreground transition-colors";

type Machine = {
  environment_id: string;
  machine_name: string;
  directory: string;
  branch: string | null;
  git_repo_url: string | null;
  worker_type: string;
  last_seen_at: number;
};

function repoLabel(m: Machine | null): string {
  if (!m) return "repo";
  if (m.git_repo_url) {
    const s = m.git_repo_url.replace(/\.git$/, "").split("/");
    return s.slice(-2).join("/") || s.slice(-1)[0];
  }
  return m.directory.split("/").filter(Boolean).slice(-1)[0] ?? "repo";
}

export function CodeComposer({
  value,
  onChange,
  onSubmit,
  busy = false,
  machines,
  selected,
  onPickMachine,
  onRefreshMachines,
  placeholder = "Describe a task or ask a question",
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  busy?: boolean;
  machines: Machine[] | null;
  selected: Machine | null;
  onPickMachine: (m: Machine) => void;
  onRefreshMachines: () => void;
  placeholder?: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [envOpen, setEnvOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  useEffect(() => {
    if (!envOpen) return;
    const onDown = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setEnvOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [envOpen]);

  const pill =
    "flex items-center gap-1.5 rounded-full border border-border/60 bg-accent/30 px-2.5 py-1 text-[12px] text-foreground/70 hover:bg-accent/50 hover:text-foreground transition-colors";

  return (
    <div className="border border-border/60 rounded-2xl overflow-hidden bg-card">
      {/* pills: environment · repo · branch · + */}
      <div className="relative flex items-center gap-1.5 px-3 py-2 border-b border-border/40" ref={popRef}>
        <button type="button" onClick={() => setEnvOpen((o) => !o)} className={pill}>
          {selected ? <Monitor className="size-3 text-foreground/60" /> : <Cloud className="size-3 text-foreground/60" />}
          {selected?.machine_name ?? "Default"}
          <ChevronDown className="size-3 opacity-50" />
        </button>
        <button type="button" onClick={() => setEnvOpen((o) => !o)} className={pill}>
          <Code2 className="size-3 text-foreground/60" />
          {repoLabel(selected)}
        </button>
        <button type="button" onClick={() => setEnvOpen((o) => !o)} className={pill}>
          <GitBranch className="size-3 text-foreground/60" />
          {selected?.branch ?? "main"}
        </button>
        <button type="button" aria-label="Add" onClick={() => setEnvOpen((o) => !o)} className={`${pill} px-1.5`}>
          <Plus className="size-3" />
        </button>

        {envOpen && (
          <div className="absolute bottom-full left-0 mb-2 w-[320px] rounded-xl border border-border bg-card p-1.5 shadow-xl z-50">
            <div className="flex items-center justify-between px-2 py-1">
              <span className="text-[11px] font-medium text-foreground/60">
                Local <span className="opacity-50">· workers</span>
              </span>
              <button
                type="button"
                aria-label="Refresh machines"
                onClick={onRefreshMachines}
                className="flex size-5 items-center justify-center rounded text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              >
                <RefreshCw className="size-3" />
              </button>
            </div>
            {machines === null ? (
              <div className="flex items-center gap-2 px-2 py-2 text-[12px] text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" /> Loading…
              </div>
            ) : machines.length === 0 ? (
              <div className="px-2 py-1.5 text-[12px] text-muted-foreground">No machines connected.</div>
            ) : (
              machines.map((m) => (
                <button
                  key={m.environment_id}
                  type="button"
                  onClick={() => {
                    onPickMachine(m);
                    setEnvOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent/40"
                >
                  <Monitor className="size-3.5 text-foreground/60" />
                  <span className="flex-1 truncate text-[13px] text-foreground">{m.machine_name}</span>
                  <span className="truncate text-[11px] text-muted-foreground">{repoLabel(m)}</span>
                  {selected?.environment_id === m.environment_id && <Check className="size-3.5 text-primary" />}
                </button>
              ))
            )}
            <div className="mt-1 border-t border-border/40 px-2 pb-1 pt-2 text-[11px] font-medium text-foreground/60">
              Remote Control
            </div>
            <div className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-foreground/80">
              <ExternalLink className="mt-0.5 size-3.5 text-muted-foreground" />
              <div>
                <div className="text-[13px]">Set up Remote Control</div>
                <div className="text-[11px] text-muted-foreground">
                  Run <code className="text-[10.5px]">/remote-control</code> on your machine to code from here.
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* input + send */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/40">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit();
            }
          }}
          placeholder={placeholder}
          rows={1}
          className="flex-1 resize-none bg-transparent text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none"
        />
        <button
          type="button"
          onClick={onSubmit}
          disabled={busy || !value.trim()}
          aria-label="Send"
          className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:pointer-events-none"
        >
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <CornerDownLeft className="size-3.5" />}
        </button>
      </div>

      {/* toolbar */}
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled
            className="rounded px-2 py-1 text-[12px] text-foreground/60 hover:bg-accent/40 hover:text-foreground transition-colors disabled:opacity-50 disabled:pointer-events-none"
          >
            Accept edits
          </button>
          <button type="button" aria-label="Attach" disabled className={`${TOOLBAR_ICON_BTN} disabled:opacity-50 disabled:pointer-events-none`}>
            <Plus className="size-3.5" />
          </button>
          <button type="button" aria-label="Record" disabled className={`${TOOLBAR_ICON_BTN} disabled:opacity-50 disabled:pointer-events-none`}>
            <Circle className="size-3.5" />
          </button>
          <button type="button" aria-label="More options" disabled className={`${TOOLBAR_ICON_BTN} disabled:opacity-50 disabled:pointer-events-none`}>
            <ChevronDown className="size-3.5" />
          </button>
        </div>
        <div className="flex items-center gap-2 text-[11.5px] text-foreground/50">
          <span>Opus 4.8</span>
          <span>Max</span>
          <Loader2 className="size-3 animate-spin opacity-40" />
        </div>
      </div>
    </div>
  );
}
