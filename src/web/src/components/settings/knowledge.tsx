"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BookOpen, Loader2, Trash2, Upload } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";

/**
 * Personal-scoped knowledge base — docs the AI references in EVERY chat
 * (workspace-scoped knowledge lives in the workbench Settings tab).
 * Backed by /api/knowledge → ~/.jarvis/knowledge/. V1: whole text files
 * injected into the system prompt (4K chars/doc), no embeddings.
 */

type KnowledgeDoc = {
  name: string;
  bytes: number;
  updatedAt: number;
  enabled: boolean;
};

const ACCEPT = ".md,.txt,.json,.yaml,.yml,.csv";

function fmtSize(bytes: number): string {
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function KnowledgeSection() {
  const qc = useQueryClient();
  const [dragOver, setDragOver] = useState(false);
  const [newName, setNewName] = useState("");
  const [newContent, setNewContent] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["knowledge"],
    queryFn: async () => {
      const r = await fetch("/api/knowledge");
      if (!r.ok) throw new Error(r.statusText);
      return (await r.json()) as { docs: KnowledgeDoc[] };
    },
    refetchOnWindowFocus: false,
  });

  const upload = useMutation({
    mutationFn: async ({ name, content }: { name: string; content: string }) => {
      const r = await fetch("/api/knowledge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, content }),
      });
      if (!r.ok) {
        const j = (await r.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error ?? r.statusText);
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Document added");
      qc.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (err: Error) => toast.error(`Add failed: ${err.message}`),
  });

  const toggle = useMutation({
    mutationFn: async ({ name, enabled }: { name: string; enabled: boolean }) => {
      const r = await fetch("/api/knowledge", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, enabled }),
      });
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }),
    onError: (e: Error) => toast.error(`Update failed: ${e.message}`),
  });

  const remove = useMutation({
    mutationFn: async (name: string) => {
      const r = await fetch(`/api/knowledge?name=${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: () => {
      toast.success("Document removed");
      qc.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (e: Error) => toast.error(`Remove failed: ${e.message}`),
  });

  const handleFiles = async (files: FileList | null) => {
    if (!files) return;
    for (const f of Array.from(files)) {
      const text = await f.text();
      upload.mutate({ name: f.name, content: text });
    }
  };

  const docs = data?.docs ?? [];

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <BookOpen className="size-4 text-muted-foreground" />
          <h2 className="text-lg font-semibold">Knowledge</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Documents JARVIS can reference in every chat — your CV, brand
          guidelines, recurring project specs, anything you&apos;d want the
          model to remember without re-explaining it each turn. Each enabled
          doc is added to the system prompt (first 4K characters).
        </p>
      </div>

      {/* Upload zone */}
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void handleFiles(e.dataTransfer.files);
        }}
        className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-8 py-12 text-center transition-colors ${
          dragOver
            ? "border-primary/60 bg-primary/5"
            : "border-border/60 bg-card/30 hover:border-border"
        }`}
      >
        <div className="flex size-12 items-center justify-center rounded-full bg-muted">
          {upload.isPending ? (
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          ) : (
            <Upload className="size-5 text-muted-foreground" />
          )}
        </div>
        <div>
          <div className="text-sm font-medium">
            Drop files to add to your knowledge
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            Markdown, text, JSON, YAML, or CSV · 1MB per file
          </div>
        </div>
        <span className="mt-1 rounded-md border border-border/60 bg-card/40 px-4 py-1.5 text-[12px] text-muted-foreground">
          Choose files
        </span>
        <input
          type="file"
          accept={ACCEPT}
          multiple
          className="hidden"
          onChange={(e) => {
            void handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </label>

      {/* Paste fallback */}
      <details className="text-[13px]">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          Or paste text directly
        </summary>
        <div className="mt-2 space-y-2">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="filename.md"
            className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-[12px] outline-none focus:border-primary"
          />
          <textarea
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder="paste content here…"
            rows={6}
            className="w-full resize-y rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-[12px] leading-snug outline-none focus:border-primary"
          />
          <Button
            size="sm"
            disabled={upload.isPending}
            onClick={() => {
              if (!newName.trim() || !newContent.trim()) {
                toast.error("Name and content required");
                return;
              }
              upload.mutate({ name: newName, content: newContent });
              setNewName("");
              setNewContent("");
            }}
          >
            {upload.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </details>

      {/* Document list */}
      <div>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Documents ({docs.length})
        </h3>
        {isLoading ? (
          <p className="text-[13px] text-muted-foreground">Loading…</p>
        ) : docs.length === 0 ? (
          <div className="rounded-lg border border-border/50 bg-card/20 p-6 text-center text-[13px] text-muted-foreground">
            No documents yet — drop a file above.
          </div>
        ) : (
          <div className="space-y-1.5">
            {docs.map((d) => (
              <div
                key={d.name}
                className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 text-[12px]"
              >
                <Switch
                  checked={d.enabled}
                  onCheckedChange={(v) => toggle.mutate({ name: d.name, enabled: v })}
                  aria-label={d.enabled ? "Disable document" : "Enable document"}
                />
                <span
                  className={`flex-1 truncate font-mono ${d.enabled ? "" : "text-muted-foreground line-through"}`}
                >
                  {d.name}
                </span>
                <span className="shrink-0 text-muted-foreground">
                  {fmtSize(d.bytes)}
                </span>
                <button
                  type="button"
                  onClick={() => remove.mutate(d.name)}
                  disabled={remove.isPending}
                  className="shrink-0 text-muted-foreground hover:text-destructive"
                  aria-label={`Remove ${d.name}`}
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
