"use client";

import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { BookOpen, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Section, formatBytes, relativeTime } from "./shared";

// ── Knowledge ─────────────────────────────────────────────────────────

export type KnowledgeDoc = {
  name: string;
  bytes: number;
  updatedAt: number;
  enabled: boolean;
};

export function KnowledgeSection({ workspaceId }: { workspaceId: string }) {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");
  const [newContent, setNewContent] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "knowledge"],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/knowledge`);
      return (await r.json()) as { docs: KnowledgeDoc[] };
    },
    refetchOnWindowFocus: false,
  });

  const upload = useMutation({
    mutationFn: async ({ name, content }: { name: string; content: string }) => {
      const r = await fetch(`/api/workspace/${workspaceId}/knowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, content }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error ?? r.statusText);
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Document added");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "knowledge"] });
    },
    onError: (err: Error) => toast.error(`Add failed: ${err.message}`),
  });

  const toggle = useMutation({
    mutationFn: async ({
      name,
      enabled,
    }: {
      name: string;
      enabled: boolean;
    }) => {
      const r = await fetch(`/api/workspace/${workspaceId}/knowledge`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, enabled }),
      });
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "knowledge"] });
      toast.success(vars.enabled ? "Document enabled" : "Document disabled");
    },
    onError: (e: Error) => toast.error(`Update failed: ${e.message}`),
  });

  const remove = useMutation({
    mutationFn: async (name: string) => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/knowledge?name=${encodeURIComponent(name)}`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: () => {
      toast.success("Document removed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "knowledge"] });
    },
  });

  const handleFiles = async (files: FileList | null) => {
    if (!files) return;
    for (const f of Array.from(files)) {
      const text = await f.text();
      upload.mutate({ name: f.name, content: text });
    }
  };

  return (
    <>
      <Section
        title="Knowledge"
        icon={<BookOpen className="size-3.5" />}
        hint="Reference docs the AI reads on every chat turn in this workspace. Each enabled doc is appended to the system prompt (truncated to 4K chars). For brand guidelines, API contracts, project conventions, etc."
      >
        <label className="flex flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-border/60 bg-card/30 px-6 py-8 text-center cursor-pointer hover:border-border">
          <BookOpen className="size-5 text-muted-foreground" />
          <div className="text-[12.5px] font-medium">
            Drop a .md / .txt / .json file
          </div>
          <div className="text-[11px] text-muted-foreground">
            or click to browse · max 1MB per file
          </div>
          <input
            type="file"
            accept=".md,.txt,.json,.yaml,.yml,.csv"
            multiple
            className="hidden"
            onChange={(e) => handleFiles(e.target.files)}
          />
        </label>
        <details className="mt-3 text-[12px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            Or paste text directly
          </summary>
          <div className="mt-2 space-y-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="filename.md"
              className="w-full rounded-md border border-border bg-background px-2 py-1 font-mono text-[11.5px] outline-none focus:border-primary"
            />
            <textarea
              value={newContent}
              onChange={(e) => setNewContent(e.target.value)}
              placeholder="paste content here…"
              rows={6}
              className="w-full resize-y rounded-md border border-border bg-background px-2 py-1 font-mono text-[11.5px] leading-snug outline-none focus:border-primary"
            />
            <button
              type="button"
              onClick={() => {
                if (!newName.trim() || !newContent.trim()) {
                  toast.error("Name and content required");
                  return;
                }
                upload.mutate({ name: newName, content: newContent });
                setNewName("");
                setNewContent("");
              }}
              disabled={upload.isPending}
              className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            >
              {upload.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </details>
      </Section>

      <Section title={`Documents (${data?.docs?.length ?? 0})`}>
        {isLoading ? (
          <p className="text-[12px] text-muted-foreground">Loading…</p>
        ) : !data?.docs?.length ? (
          <p className="text-[12px] text-muted-foreground">
            No documents yet — upload above.
          </p>
        ) : (
          <div className="space-y-1">
            {data.docs.map((d) => (
              <div
                key={d.name}
                className="flex items-center gap-2 rounded-md border border-border/40 px-3 py-2 text-[12px]"
              >
                <input
                  type="checkbox"
                  checked={d.enabled}
                  onChange={(e) =>
                    toggle.mutate({ name: d.name, enabled: e.target.checked })
                  }
                  className="shrink-0 cursor-pointer accent-primary"
                  title={d.enabled ? "Disable in retrieval" : "Enable in retrieval"}
                />
                <span
                  className={cn(
                    "flex-1 truncate font-mono",
                    !d.enabled && "text-muted-foreground/70 line-through",
                  )}
                >
                  {d.name}
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {formatBytes(d.bytes)} · {relativeTime(d.updatedAt)}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (confirm(`Remove ${d.name}?`)) remove.mutate(d.name);
                  }}
                  className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                  aria-label="Remove"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
