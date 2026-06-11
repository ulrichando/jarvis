"use client";

import { useEffect, useState } from "react";
import { X, Loader2, ExternalLink, CircleDot, Check, Plug } from "lucide-react";

type GithubIssue = {
  number: number;
  title: string;
  body: string;
  repo: string;
  url: string;
  updated_at: string;
};

function GithubMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden className={className} fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="w-[440px] max-w-[92vw] rounded-2xl border border-border bg-card p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div className="text-[15px] font-semibold text-foreground">{title}</div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-muted-foreground hover:text-foreground">
            <X className="size-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ConnectorsModal({ onClose }: { onClose: () => void }) {
  const [status, setStatus] = useState<{ connected: boolean; login?: string } | null>(null);
  const [token, setToken] = useState("");
  const [showInput, setShowInput] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetch("/api/connectors/github");
      if (r.ok) setStatus((await r.json()) as { connected: boolean; login?: string });
    } catch {
      /* ignore */
    }
  };
  useEffect(() => {
    load();
  }, []);

  const connect = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/connectors/github", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const j = (await r.json()) as { ok: boolean; error?: string };
      if (j.ok) {
        setToken("");
        setShowInput(false);
        await load();
      } else {
        setErr(j.error ?? "Failed to connect");
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    await fetch("/api/connectors/github", { method: "DELETE" }).catch(() => {});
    await load();
  };

  return (
    <ModalShell title="Connectors" onClose={onClose}>
      <div className="flex items-center gap-3 rounded-xl border border-border/60 p-3">
        <GithubMark className="size-6 text-foreground" />
        <div className="flex-1">
          <div className="text-[13px] font-medium text-foreground">GitHub</div>
          <div className="text-[12px] text-muted-foreground">Import issues from your repositories</div>
        </div>
        {status?.connected ? (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1 text-[12px] text-emerald-500">
              <Check className="size-3.5" /> @{status.login}
            </span>
            <button type="button" onClick={disconnect} className="rounded-md border border-border/60 px-2 py-1 text-[12px] text-foreground/70 hover:bg-accent/40">
              Disconnect
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => setShowInput((s) => !s)} className="rounded-md bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground hover:bg-primary/90">
            Connect
          </button>
        )}
      </div>

      {showInput && !status?.connected && (
        <div className="mt-3 space-y-2">
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && token.trim()) connect(); }}
            placeholder="ghp_… or github_pat_…"
            autoFocus
            className="w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40"
          />
          {err && <div className="text-[12px] text-red-500">{err}</div>}
          <div className="flex items-center justify-between">
            <a href="https://github.com/settings/tokens/new?scopes=repo,read:user&description=Jarvis%20Code" target="_blank" rel="noreferrer" className="flex items-center gap-1 text-[11.5px] text-blue-400 hover:underline">
              Create a token <ExternalLink className="size-3" />
            </a>
            <button type="button" onClick={connect} disabled={busy || !token.trim()} className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-[12px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40">
              {busy && <Loader2 className="size-3.5 animate-spin" />} Connect
            </button>
          </div>
          <div className="text-[11px] text-muted-foreground/70">
            Needs <code className="text-[10.5px]">repo</code> + <code className="text-[10.5px]">read:user</code>. The token is stored on this machine only and never sent to the browser.
          </div>
        </div>
      )}

      <div className="mt-3 flex items-center gap-2 rounded-lg bg-accent/15 px-3 py-2 text-[11.5px] text-muted-foreground/70">
        <Plug className="size-3.5" /> More connectors coming soon.
      </div>
    </ModalShell>
  );
}

export function ImportIssueModal({ onClose, onPick }: { onClose: () => void; onPick: (text: string) => void }) {
  const [issues, setIssues] = useState<GithubIssue[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/github/issues")
      .then(async (r) => {
        const j = (await r.json()) as { ok: boolean; issues?: GithubIssue[]; error?: string };
        if (j.ok && j.issues) setIssues(j.issues);
        else setErr(j.error ?? "Failed to load issues");
      })
      .catch((e) => setErr(String(e)));
  }, []);

  const pick = (i: GithubIssue) => {
    const text = `${i.title}\n\n${i.body}`.trim() + `\n\n(${i.repo}#${i.number} — ${i.url})`;
    onPick(text);
    onClose();
  };

  return (
    <ModalShell title="Import GitHub issue" onClose={onClose}>
      {err ? (
        <div className="py-6 text-center text-[13px] text-muted-foreground">
          <CircleDot className="mx-auto mb-2 size-5 text-muted-foreground/50" />
          {err.toLowerCase().includes("not connected") ? "Connect GitHub in Connectors first." : err}
        </div>
      ) : issues === null ? (
        <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading issues…
        </div>
      ) : issues.length === 0 ? (
        <div className="py-8 text-center text-[13px] text-muted-foreground">No open issues found.</div>
      ) : (
        <div className="max-h-[360px] space-y-1 overflow-y-auto">
          {issues.map((i) => (
            <button key={i.url} type="button" onClick={() => pick(i)} className="flex w-full items-start gap-2.5 rounded-lg p-2.5 text-left hover:bg-accent/40">
              <CircleDot className="mt-0.5 size-4 shrink-0 text-emerald-500" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-foreground">{i.title}</div>
                <div className="truncate text-[11.5px] text-muted-foreground">{i.repo} #{i.number}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </ModalShell>
  );
}
