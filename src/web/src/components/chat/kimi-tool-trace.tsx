"use client";
import { Search, FileText, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToolTraceEntry = {
  id: string;
  toolName: string;
  // Concise summary string (e.g., the search query, or the URL fetched)
  summary: string;
  status: "pending" | "ok" | "error";
  // Optional 1-line preview of the result (e.g., "5 results" or "200 OK 12kb")
  resultSummary?: string;
};

export function KimiToolTrace({ entries }: { entries: ToolTraceEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <div className="mb-3 space-y-1.5">
      {entries.map((e) => (
        <div
          key={e.id}
          className="flex items-center gap-2 rounded-md border border-border/40 bg-muted/20 px-3 py-1.5 text-[12px]"
        >
          {e.status === "error" ? (
            <AlertCircle className="size-3.5 shrink-0 text-destructive/80" />
          ) : e.toolName === "webSearch" ? (
            <Search
              className={cn(
                "size-3.5 shrink-0",
                e.status === "pending" ? "text-primary animate-pulse" : "text-primary",
              )}
            />
          ) : (
            <FileText className="size-3.5 shrink-0 text-muted-foreground" />
          )}
          <span className="flex-1 truncate">
            <span className="font-medium text-foreground/90">{e.toolName}</span>
            <span className="text-muted-foreground"> · {e.summary}</span>
          </span>
          {e.resultSummary && (
            <span className="shrink-0 text-[11px] text-muted-foreground/80">
              {e.resultSummary}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
