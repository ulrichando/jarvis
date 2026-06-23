"use client";
import { useEffect, useState } from "react";
import { Loader2, Check } from "lucide-react";
import { Markdown } from "@/components/markdown/markdown";
import { PermissionCard } from "./permission-card";
import { type ChatMsg, type Part, formatStepTime, formatElapsed } from "@/lib/computer-use/timeline";

const EXAMPLES = [
  "Take a screenshot and tell me what's open",
  "Open Firefox and go to news.ycombinator.com",
  "Open the file manager and list my home folder",
];

export function ActivityTimeline({
  thread, running, runStart, ready, onApprove, onRunExample,
}: {
  thread: ChatMsg[];
  running: boolean;
  runStart: number | null;
  ready: boolean;
  onApprove: (reqId: string, decision: "once" | "session" | "deny") => void;
  onRunExample: (ex: string) => void;
}) {
  const stepCount = thread.reduce((n, m) => n + m.parts.filter((p) => p.kind === "action").length, 0);
  const lastAssistant = [...thread].reverse().find((m) => m.role === "assistant");
  const showTrailingWork = running && lastAssistant && !lastAssistant.parts.some((p) => p.kind === "done");

  return (
    <aside className="flex w-[404px] shrink-0 flex-col border-l border-border/40 bg-card/30">
      <div className="flex h-[46px] shrink-0 items-center gap-2 border-b border-border/40 px-4">
        <span className="text-[13px] font-semibold">Activity</span>
        {running && (
          <span role="status" className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
            <span className="size-1.5 animate-pulse rounded-full bg-primary motion-reduce:animate-none" /> Working
          </span>
        )}
        {stepCount > 0 && (
          <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">{stepCount} steps · <ElapsedCounter key={runStart ?? 0} runStart={runStart} running={running} /></span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {thread.length === 0 ? (
          <div className="pt-4">
            <p className="text-center text-xs text-muted-foreground">
              Tell Jarvis what to do on the desktop. It watches the screen and works step by step — take control any time for logins or captchas.
            </p>
            <div className="mt-5 space-y-1.5">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => onRunExample(ex)} disabled={!ready}
                  className="block w-full rounded-lg border border-border/50 bg-card/40 px-3 py-2 text-left text-xs text-foreground/90 transition-colors hover:border-primary/40 hover:bg-card disabled:opacity-40">
                  {ex}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div>
            {thread.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="mb-4 rounded-xl border border-border/40 bg-card p-3">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-primary">Task</div>
                  <div className="text-[13.5px] leading-snug">{m.parts[0]?.text}</div>
                </div>
              ) : (
                <div key={i}>{m.parts.map((p, j) => <Entry key={j} part={p} onApprove={onApprove} />)}</div>
              ),
            )}
            {showTrailingWork && (
              <div className="flex items-center gap-2.5 pb-2 text-[12px] text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin text-primary motion-reduce:animate-none" /> Working…
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

// Owns its own 500ms tick so only this label re-renders during a run — keeps the
// page (and the live noVNC canvas in DesktopStage) off the twice-a-second re-render.
// Parent keys it on runStart, so each new run remounts it with a fresh 0:00.
function ElapsedCounter({ runStart, running }: { runStart: number | null; running: boolean }) {
  const [elapsedMs, setElapsedMs] = useState(() => (runStart == null ? 0 : Date.now() - runStart));
  useEffect(() => {
    if (!running || runStart == null) return;
    const id = setInterval(() => setElapsedMs(Date.now() - runStart), 500);
    return () => clearInterval(id);
  }, [running, runStart]);
  return <>{formatElapsed(elapsedMs)}</>;
}

function Entry({ part, onApprove }: { part: Part; onApprove: (reqId: string, decision: "once" | "session" | "deny") => void }) {
  if (part.kind === "text") return <div className="mb-4 border-l-2 border-border/40 pl-3 text-[12.5px] leading-relaxed text-muted-foreground"><Markdown content={part.text} /></div>;
  if (part.kind === "permission") return <div className="mb-4"><PermissionCard part={part} onApprove={onApprove} /></div>;
  if (part.kind === "done") return <div className="mb-4 flex items-center gap-2 text-[12px] text-emerald-500"><Check className="size-3.5" /> {part.text}</div>;
  if (part.kind === "blocked") return <div className="mb-4 rounded-md bg-destructive/10 px-2.5 py-1.5 text-[12px] text-destructive">⛔ {part.text}</div>;
  if (part.kind === "error") return <div className="mb-4 rounded-lg bg-destructive/10 px-3 py-2 text-[13px] text-destructive">{part.text}</div>;
  return (
    <div className="mb-4 flex gap-3">
      <div className="flex flex-col items-center">
        <div className="grid size-[22px] place-items-center rounded-full border border-emerald-500/40 bg-emerald-500/15 text-emerald-500"><Check className="size-3" /></div>
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] leading-snug">{part.text}</div>
        {part.ts ? <div className="mt-0.5 text-[10.5px] tabular-nums text-muted-foreground/70">{formatStepTime(part.ts)}</div> : null}
      </div>
      {part.thumb ? (
        // eslint-disable-next-line @next/next/no-img-element -- base64 dataURL; next/image can't optimize it, and fixed h-10 w-16 means no CLS
        <img src={part.thumb} alt="Desktop at this step" className="mt-0.5 h-10 w-16 shrink-0 rounded-md border border-border/40 object-cover" />
      ) : null}
    </div>
  );
}
