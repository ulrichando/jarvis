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
  Mic,
  Loader2,
  Check,
  ExternalLink,
  RefreshCw,
  Search,
  Paperclip,
  CircleDot,
  SquareSlash,
  Blocks,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ConnectorsModal, ImportIssueModal } from "./code-connectors";

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

type Popover = null | "env" | "repo" | "plus" | "model" | "effort";

const MODELS: { name: string; n: string; legacy?: boolean; ctx?: string }[] = [
  { name: "Fable 5", n: "1" },
  { name: "Fable 5", ctx: "1M context", n: "2" },
  { name: "Opus 4.8", n: "3" },
  { name: "Opus 4.8", ctx: "1M context", n: "4" },
  { name: "Sonnet 4.6", n: "5" },
  { name: "Haiku 4.5", n: "6" },
  { name: "Opus 4.7", n: "7", legacy: true },
  { name: "Opus 4.7", ctx: "1M context", n: "8", legacy: true },
  { name: "Opus 4.6", n: "9", legacy: true },
];

const PLUS_ITEMS: { icon: LucideIcon; label: string; chord: string; sub?: boolean }[] = [
  { icon: Paperclip, label: "Add files or photos", chord: "Ctrl+U" },
  { icon: CircleDot, label: "Import GitHub issue", chord: "" },
  { icon: SquareSlash, label: "Slash commands", chord: "" },
  { icon: Blocks, label: "Connectors", chord: "", sub: true },
];

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
  showPills = true,
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
  showPills?: boolean;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<Popover>(null);
  const [model, setModel] = useState("Opus 4.8");
  const [repoQuery, setRepoQuery] = useState("");
  const [modal, setModal] = useState<null | "connectors" | "import">(null);
  const [ghRepos, setGhRepos] = useState<{ full_name: string }[] | null>(null);
  const [repoOverride, setRepoOverride] = useState<string | null>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(null);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  // Pull the user's GitHub repos (when GitHub is connected) for the repo picker.
  useEffect(() => {
    fetch("/api/github/repos")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { ok?: boolean; repos?: { full_name: string }[] } | null) => {
        if (d?.ok && d.repos) setGhRepos(d.repos);
      })
      .catch(() => {});
  }, []);

  const pill =
    "flex items-center gap-1.5 rounded-full border border-border/60 bg-accent/30 px-2.5 py-1 text-[12px] text-foreground/70 hover:bg-accent/50 hover:text-foreground transition-colors";
  const toggle = (p: Popover) => setOpen((cur) => (cur === p ? null : p));
  const repos = (machines ?? []).filter((m) =>
    repoLabel(m).toLowerCase().includes(repoQuery.toLowerCase()),
  );
  const ghFiltered = (ghRepos ?? []).filter((r) =>
    r.full_name.toLowerCase().includes(repoQuery.toLowerCase()),
  );

  return (
    <div className="border border-border/60 rounded-2xl overflow-visible bg-card" ref={rootRef}>
      {/* pills (welcome view only) */}
      {showPills && (
        <div className="relative flex items-center gap-1.5 px-3 py-2 border-b border-border/40">
          {/* environment */}
          <button type="button" onClick={() => toggle("env")} className={pill}>
            {selected ? <Monitor className="size-3 text-foreground/60" /> : <Cloud className="size-3 text-foreground/60" />}
            {selected?.machine_name ?? "Default"}
            <ChevronDown className="size-3 opacity-50" />
          </button>
          {open === "env" && (
            <div className="absolute bottom-full left-0 mb-2 w-[320px] rounded-xl border border-border bg-card p-1.5 shadow-xl z-50">
              <div className="flex items-center justify-between px-2 py-1">
                <span className="text-[11px] font-medium text-foreground/60">Local <span className="opacity-50">· workers</span></span>
                <button type="button" aria-label="Refresh machines" onClick={onRefreshMachines} className="flex size-5 items-center justify-center rounded text-muted-foreground hover:bg-accent/50 hover:text-foreground">
                  <RefreshCw className="size-3" />
                </button>
              </div>
              {machines === null ? (
                <div className="flex items-center gap-2 px-2 py-2 text-[12px] text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Loading…</div>
              ) : machines.length === 0 ? (
                <div className="px-2 py-1.5 text-[12px] text-muted-foreground">No machines connected.</div>
              ) : (
                machines.map((m) => (
                  <button key={m.environment_id} type="button" onClick={() => { onPickMachine(m); setOpen(null); }} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent/40">
                    <Monitor className="size-3.5 text-foreground/60" />
                    <span className="flex-1 truncate text-[13px] text-foreground">{m.machine_name}</span>
                    <span className="truncate text-[11px] text-muted-foreground">{repoLabel(m)}</span>
                    {selected?.environment_id === m.environment_id && <Check className="size-3.5 text-primary" />}
                  </button>
                ))
              )}
              <div className="mt-1 border-t border-border/40 px-2 pb-1 pt-2 text-[11px] font-medium text-foreground/60">Remote Control</div>
              <div className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-foreground/80">
                <ExternalLink className="mt-0.5 size-3.5 text-muted-foreground" />
                <div>
                  <div className="text-[13px]">Set up Remote Control</div>
                  <div className="text-[11px] text-muted-foreground">Run <code className="text-[10.5px]">/remote-control</code> on your machine to code from here.</div>
                </div>
              </div>
            </div>
          )}

          {/* repo */}
          <button type="button" onClick={() => toggle("repo")} className={pill}>
            <Code2 className="size-3 text-foreground/60" />
            {repoOverride ?? repoLabel(selected)}
          </button>
          {open === "repo" && (
            <div className="absolute bottom-full left-20 mb-2 w-[320px] rounded-xl border border-border bg-card p-1.5 shadow-xl z-50">
              <div className="max-h-[300px] overflow-y-auto">
                {ghRepos !== null && ghFiltered.length > 0 && (
                  <>
                    <div className="px-2 pb-1 pt-1 text-[11px] font-medium text-muted-foreground/70">GitHub</div>
                    {ghFiltered.slice(0, 60).map((r) => (
                      <button key={r.full_name} type="button" onClick={() => { setRepoOverride(r.full_name); setOpen(null); }} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-accent/40">
                        <span className="flex-1 truncate text-foreground/90">{r.full_name}</span>
                        {repoOverride === r.full_name && <Check className="size-3.5 text-primary" />}
                      </button>
                    ))}
                  </>
                )}
                {repos.length > 0 && (
                  <>
                    <div className="px-2 pb-1 pt-1.5 text-[11px] font-medium text-muted-foreground/70">Connected machines</div>
                    {repos.map((m) => (
                      <button key={m.environment_id} type="button" onClick={() => { onPickMachine(m); setRepoOverride(null); setOpen(null); }} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-accent/40">
                        <span className="flex-1 truncate text-foreground/90">{repoLabel(m)}</span>
                        {!repoOverride && selected?.environment_id === m.environment_id && <Check className="size-3.5 text-primary" />}
                      </button>
                    ))}
                  </>
                )}
                {ghRepos === null && repos.length === 0 && (
                  <div className="px-2 py-1.5 text-[12px] text-muted-foreground">No repos — connect GitHub (＋ → Connectors) or a machine.</div>
                )}
                {ghRepos !== null && ghFiltered.length === 0 && repos.length === 0 && (
                  <div className="px-2 py-1.5 text-[12px] text-muted-foreground">No matching repos.</div>
                )}
              </div>
              <div className="mt-1 flex items-center gap-1.5 rounded-lg border border-border/50 bg-accent/20 px-2 py-1.5">
                <Search className="size-3.5 text-muted-foreground" />
                <input value={repoQuery} onChange={(e) => setRepoQuery(e.target.value)} placeholder="Search repos…" className="flex-1 bg-transparent text-[12.5px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none" />
              </div>
            </div>
          )}

          {/* branch */}
          <button type="button" className={pill}>
            <GitBranch className="size-3 text-foreground/60" />
            {selected?.branch ?? "main"}
          </button>

          {/* + menu */}
          <button type="button" aria-label="Add" onClick={() => toggle("plus")} className={`${pill} px-1.5`}>
            <Plus className="size-3" />
          </button>
          {open === "plus" && (
            <div className="absolute bottom-full left-32 mb-2 w-[240px] rounded-xl border border-border bg-card p-1 shadow-xl z-50">
              {PLUS_ITEMS.map((it) => (
                <button
                  key={it.label}
                  type="button"
                  onClick={() => {
                    if (it.label === "Connectors") setModal("connectors");
                    else if (it.label === "Import GitHub issue") setModal("import");
                    setOpen(null);
                  }}
                  className="flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50"
                >
                  <it.icon className="size-3.5 text-muted-foreground" />
                  <span className="flex-1">{it.label}</span>
                  {it.sub && <ChevronDown className="size-3.5 -rotate-90 text-muted-foreground" />}
                  {it.chord && <span className="text-[11px] text-muted-foreground/60">{it.chord}</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* input + send */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/40">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(); } }}
          placeholder={placeholder}
          rows={1}
          className="flex-1 resize-none bg-transparent text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none"
        />
        <button type="button" onClick={onSubmit} disabled={busy || !value.trim()} aria-label="Send" className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:pointer-events-none">
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <CornerDownLeft className="size-3.5" />}
        </button>
      </div>

      {/* toolbar */}
      <div className="relative flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-1">
          <button type="button" disabled className="rounded px-2 py-1 text-[12px] text-foreground/60 disabled:opacity-50 disabled:pointer-events-none">Accept edits</button>
          <button type="button" aria-label="Attach" className={TOOLBAR_ICON_BTN}><Plus className="size-3.5" /></button>
          <button type="button" aria-label="Record" className={TOOLBAR_ICON_BTN}><Mic className="size-3.5" /></button>
          <button type="button" aria-label="More" className={TOOLBAR_ICON_BTN}><ChevronDown className="size-3.5" /></button>
        </div>
        <div className="flex items-center gap-2 text-[11.5px] text-foreground/55">
          <button type="button" onClick={() => toggle("model")} className="rounded px-1.5 py-0.5 hover:bg-accent/40 hover:text-foreground">{model}</button>
          <button type="button" onClick={() => toggle("effort")} className="rounded px-1.5 py-0.5 hover:bg-accent/40 hover:text-foreground">Max</button>
          <Loader2 className="size-3 animate-spin opacity-40" />
        </div>

        {open === "model" && (
          <div className="absolute bottom-full right-2 mb-2 w-[260px] rounded-xl border border-border bg-card p-1 shadow-xl z-50">
            <div className="flex items-center justify-between px-2.5 py-1.5 text-[11px] text-muted-foreground/60">
              <span>Models</span>
              <span className="font-mono">Ctrl ⇧ I</span>
            </div>
            {MODELS.map((m) => {
              const label = m.ctx ? `${m.name} (${m.ctx})` : m.name;
              const active = label === model;
              return (
                <button key={m.n} type="button" onClick={() => { setModel(label); setOpen(null); }} className="flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-left text-[13px] hover:bg-accent/50">
                  <Check className={`size-3.5 ${active ? "text-primary" : "opacity-0"}`} />
                  <span className="flex-1 text-foreground/90">{m.name}{m.ctx && <span className="text-muted-foreground"> ({m.ctx})</span>}{m.legacy && <span className="text-muted-foreground/60"> Legacy</span>}</span>
                  <span className="text-[11px] text-muted-foreground/50">{m.n}</span>
                </button>
              );
            })}
          </div>
        )}

        {open === "effort" && (
          <div className="absolute bottom-full right-2 mb-2 w-[240px] rounded-xl border border-border bg-card p-3 shadow-xl z-50">
            <div className="mb-2 text-[12px] text-foreground/70">Effort <span className="font-medium text-foreground">Max</span></div>
            <div className="mb-1 flex justify-between text-[11px] text-muted-foreground/60"><span>Faster</span><span>Smarter</span></div>
            <input type="range" min={0} max={100} defaultValue={100} className="w-full accent-primary" />
          </div>
        )}
      </div>

      {modal === "connectors" && <ConnectorsModal onClose={() => setModal(null)} />}
      {modal === "import" && <ImportIssueModal onClose={() => setModal(null)} onPick={(t) => onChange(t)} />}
    </div>
  );
}
