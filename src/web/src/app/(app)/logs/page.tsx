"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

type LogIndexEntry = {
  name: string;
  label: string;
  path: string;
  size: number;
  mtime: number;
  present: boolean;
};

type Line = { line: string; ts: number; id: number };

// Soft cap on lines kept in DOM. Beyond this the oldest are dropped
// to keep the page responsive — log files can be megabytes and a
// naive append would lock up the browser within a minute.
const MAX_LINES = 2000;

// Heuristic colour-coding by line content. Cheap regex per line, no
// build-time tokeniser needed. Matches what `bin/jarvis-logs` does
// in colour mode via tput.
function classify(line: string): "error" | "warn" | "info" | "muted" | "plain" {
  if (/\b(error|fail(ed|ure)?|exception|panic|fatal|stack)\b/i.test(line)) return "error";
  if (/\b(warn(ing)?|deprecated|blocked)\b/i.test(line)) return "warn";
  if (/\b(ready|listening|started|active|spawn|✓)\b/i.test(line)) return "info";
  if (/^\[/.test(line) || /\bGET |POST |PATCH |PUT |DELETE /.test(line)) return "muted";
  return "plain";
}

const TONE_CLASS: Record<ReturnType<typeof classify>, string> = {
  error: "text-red-400",
  warn: "text-yellow-300",
  info: "text-emerald-400",
  muted: "text-muted-foreground",
  plain: "text-foreground",
};

export default function LogsPage() {
  const [files, setFiles] = useState<LogIndexEntry[]>([]);
  const [active, setActive] = useState<string>("jarvis-web.log");
  const [filter, setFilter] = useState<string>("");
  const [paused, setPaused] = useState<boolean>(false);
  const [follow, setFollow] = useState<boolean>(true);
  const [lines, setLines] = useState<Line[]>([]);
  const idRef = useRef(0);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  // Load file index — refresh every 5s so newly-created log files show up.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch("/api/logs");
        if (!r.ok) return;
        const j = await r.json();
        if (!cancelled) setFiles(j.logs ?? []);
      } catch {
        /* ignore — list refresh is best-effort */
      }
    };
    load();
    const t = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  // Live tail via SSE. Resets when active file changes.
  useEffect(() => {
    setLines([]);
    if (paused) return;
    const es = new EventSource(`/api/logs/stream?file=${encodeURIComponent(active)}&tail=400`);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as { line: string; ts: number };
        if (data.line === undefined) return;
        setLines((prev) => {
          const next = prev.concat({ line: data.line, ts: data.ts, id: ++idRef.current });
          if (next.length > MAX_LINES) next.splice(0, next.length - MAX_LINES);
          return next;
        });
      } catch {
        /* ignore malformed events */
      }
    };
    es.onerror = () => {
      // Browser will auto-reconnect by default; nothing to do.
    };
    return () => es.close();
  }, [active, paused]);

  // Auto-scroll to bottom on new lines when follow is on. Doing this
  // in a layout effect AFTER render so the scroll target reflects the
  // appended content. Skipped when the user has scrolled up — hovering
  // over the scroller pauses follow until they scroll back to bottom.
  useEffect(() => {
    if (!follow) return;
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lines, follow]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return lines;
    let pattern: RegExp;
    try {
      pattern = new RegExp(filter, "i");
    } catch {
      return lines;
    }
    return lines.filter((l) => pattern.test(l.line));
  }, [lines, filter]);

  const errorCount = useMemo(
    () => lines.filter((l) => classify(l.line) === "error").length,
    [lines],
  );
  const warnCount = useMemo(
    () => lines.filter((l) => classify(l.line) === "warn").length,
    [lines],
  );

  return (
    <div className="flex h-full flex-col bg-background">
      {/* Toolbar */}
      <div className="flex items-center gap-3 border-b border-border/50 px-4 py-2">
        <select
          value={active}
          onChange={(e) => setActive(e.target.value)}
          className="rounded-md border border-border bg-card px-2 py-1 text-[12px]"
        >
          {files.map((f) => (
            <option key={f.name} value={f.name} disabled={!f.present}>
              {f.label}
              {f.present ? ` · ${formatBytes(f.size)}` : " · (no file)"}
            </option>
          ))}
        </select>

        <input
          type="text"
          placeholder="filter (regex, case-insensitive)…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="flex-1 max-w-md rounded-md border border-border bg-card px-2 py-1 text-[12px] font-mono"
        />

        <button
          type="button"
          onClick={() => setPaused((v) => !v)}
          className={cn(
            "rounded-md border px-2 py-1 text-[11px] font-medium",
            paused
              ? "border-yellow-300/50 bg-yellow-300/10 text-yellow-300"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          {paused ? "Resume" : "Pause"}
        </button>

        <button
          type="button"
          onClick={() => setFollow((v) => !v)}
          className={cn(
            "rounded-md border px-2 py-1 text-[11px] font-medium",
            follow
              ? "border-emerald-400/50 bg-emerald-400/10 text-emerald-400"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          {follow ? "Following" : "Follow"}
        </button>

        <button
          type="button"
          onClick={() => setLines([])}
          className="rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          Clear
        </button>

        <span className="ml-auto text-[11px] text-muted-foreground">
          {filtered.length}/{lines.length} lines
          {errorCount > 0 && (
            <span className="ml-3 text-red-400">{errorCount} errors</span>
          )}
          {warnCount > 0 && (
            <span className="ml-3 text-yellow-300">{warnCount} warns</span>
          )}
        </span>
      </div>

      {/* Stream */}
      <div
        ref={scrollerRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 16;
          if (!atBottom && follow) setFollow(false);
        }}
        className="flex-1 overflow-y-auto bg-[#0a0a0a] p-3 font-mono text-[12px] leading-snug"
      >
        {lines.length === 0 ? (
          <div className="text-muted-foreground">
            Waiting for log lines… Click anywhere outside the input then press{" "}
            <kbd className="rounded border border-border bg-card px-1.5 py-0.5 text-[10px]">
              Pause
            </kbd>{" "}
            to stop the stream.
          </div>
        ) : (
          filtered.map((l) => (
            <div key={l.id} className={cn("whitespace-pre-wrap break-all", TONE_CLASS[classify(l.line)])}>
              {l.line || " "}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}
