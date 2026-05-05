"use client";
import { Network, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function KimiSwarmProgress({
  total,
  completed,
  current,
  done,
}: {
  total: number;
  completed: number;
  current?: string;
  // True once the aggregator started streaming (replace card with text)
  done: boolean;
}) {
  if (done && completed >= total) return null;
  if (total === 0) return null;
  const pct = Math.round((completed / total) * 100);
  return (
    <div className="mb-3 rounded-lg border border-border/40 bg-muted/20 px-3 py-2.5">
      <div className="flex items-center gap-2 text-[12px]">
        {completed >= total ? (
          <CheckCircle2 className="size-3.5 shrink-0 text-primary" />
        ) : (
          <Network className="size-3.5 shrink-0 text-primary animate-pulse" />
        )}
        <span className="flex-1 font-medium text-foreground/90">
          {completed >= total
            ? "Synthesizing…"
            : `Coordinating ${total} agents`}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {completed}/{total}
        </span>
      </div>
      <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted/40">
        <div
          className={cn("h-full bg-primary transition-all duration-300")}
          style={{ width: `${pct}%` }}
        />
      </div>
      {current && (
        <div className="mt-1.5 truncate text-[11px] text-muted-foreground">
          Latest: {current}
        </div>
      )}
    </div>
  );
}
