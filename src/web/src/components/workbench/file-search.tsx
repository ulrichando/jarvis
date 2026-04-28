"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Search, Loader2, FileText } from "lucide-react";

type Hit = {
  path: string;
  line: number;
  text: string;
};

async function runSearch(workspaceId: string, query: string): Promise<Hit[]> {
  // ripgrep is preinstalled in the sandbox image. We pipe through head
  // so a runaway search can't dump megabytes back to the browser.
  const cmd = `rg --json --max-count 50 --max-columns 500 ${JSON.stringify(query)} . 2>/dev/null | head -c 200000`;
  const r = await fetch(`/api/workspace/${workspaceId}/exec`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command: cmd }),
  });
  const j = await r.json();
  const stdout: string = j?.stdout ?? "";
  const hits: Hit[] = [];
  for (const line of stdout.split("\n")) {
    if (!line.trim()) continue;
    try {
      const ev = JSON.parse(line);
      if (ev.type !== "match") continue;
      const data = ev.data;
      const path = data?.path?.text?.replace(/^\.\//, "") ?? "";
      const lineNum = data?.line_number ?? 0;
      const text = (data?.lines?.text ?? "").replace(/\n$/, "");
      if (path && lineNum) hits.push({ path, line: lineNum, text });
    } catch {}
  }
  return hits;
}

type Props = {
  workspaceId: string;
  onOpen: (path: string) => void;
};

export function FileSearch({ workspaceId, onOpen }: Props) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);

  const search = useMutation({
    mutationFn: (q: string) => runSearch(workspaceId, q),
    onSuccess: (results) => setHits(results),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    search.mutate(q);
  };

  return (
    <div className="flex h-full flex-col">
      <form onSubmit={submit} className="px-3 py-2 border-b border-border/50">
        <div className="flex items-center gap-2 rounded-md border border-border/50 bg-muted/30 px-2.5 h-7">
          {search.isPending ? (
            <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
          ) : (
            <Search className="size-3.5 text-muted-foreground" />
          )}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search files…"
            className="flex-1 bg-transparent text-[12px] outline-none placeholder:text-muted-foreground"
          />
        </div>
      </form>
      <div className="flex-1 overflow-y-auto">
        {search.isPending && hits.length === 0 ? (
          <div className="px-3 py-2 text-xs text-muted-foreground">searching…</div>
        ) : hits.length === 0 ? (
          <div className="px-3 py-2 text-xs text-muted-foreground italic">
            {query ? "No matches." : "Type to search across all files in this workspace."}
          </div>
        ) : (
          <HitsList hits={hits} onOpen={onOpen} />
        )}
      </div>
    </div>
  );
}

function HitsList({ hits, onOpen }: { hits: Hit[]; onOpen: (path: string) => void }) {
  // Group by path for compactness.
  const byPath = new Map<string, Hit[]>();
  for (const h of hits) {
    const arr = byPath.get(h.path) ?? [];
    arr.push(h);
    byPath.set(h.path, arr);
  }
  return (
    <div className="text-[12px]">
      {[...byPath.entries()].map(([path, group]) => (
        <div key={path} className="border-b border-border/30">
          <button
            onClick={() => onOpen(path)}
            className="flex w-full items-center gap-1.5 px-3 py-1.5 hover:bg-accent/50"
          >
            <FileText className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="font-mono truncate">{path}</span>
            <span className="ml-auto text-[10px] text-muted-foreground">{group.length}</span>
          </button>
          {group.slice(0, 5).map((h, i) => (
            <button
              key={i}
              onClick={() => onOpen(path)}
              className="flex w-full items-start gap-2 px-3 py-1 hover:bg-accent/30 font-mono text-[11px]"
            >
              <span className="shrink-0 text-muted-foreground">:{h.line}</span>
              <span className="truncate text-left text-muted-foreground/90">{h.text}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
