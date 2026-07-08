"use client";

import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Plus, Wrench, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Section } from "./shared";

// ── Skills ────────────────────────────────────────────────────────────

export type Skill = {
  name: string;
  description: string;
  kind: "prompt" | "shell";
  body: string;
  bytes: number;
  updatedAt: number;
};

export function SkillsSection({ workspaceId }: { workspaceId: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Skill | null>(null);
  const [creating, setCreating] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["ws", workspaceId, "skills"],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}/skills`);
      return (await r.json()) as { skills: Skill[] };
    },
    refetchOnWindowFocus: false,
  });

  const save = useMutation({
    mutationFn: async (s: Pick<Skill, "name" | "description" | "kind" | "body">) => {
      const r = await fetch(`/api/workspace/${workspaceId}/skills`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error ?? r.statusText);
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Skill saved");
      setEditing(null);
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "skills"] });
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const remove = useMutation({
    mutationFn: async (name: string) => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/skills?name=${encodeURIComponent(name)}`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: () => {
      toast.success("Skill removed");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "skills"] });
    },
  });

  if (editing || creating) {
    return (
      <SkillEditor
        initial={editing}
        onCancel={() => {
          setEditing(null);
          setCreating(false);
        }}
        onSave={(s) => save.mutate(s)}
        saving={save.isPending}
      />
    );
  }

  return (
    <Section
      title="Skills"
      icon={<Wrench className="size-3.5" />}
      hint="Reusable prompt templates + shell macros stored at .jarvis/skills/. V1: store + edit in this UI; V2 wires them to slash commands in the composer."
    >
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[12px] text-muted-foreground">
          {data?.skills?.length ?? 0} skill
          {(data?.skills?.length ?? 0) === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex items-center gap-1 rounded-md border border-primary/50 bg-primary/10 px-3 py-1 text-[11.5px] text-primary hover:bg-primary/15"
        >
          <Plus className="size-3.5" />
          New skill
        </button>
      </div>
      {isLoading ? (
        <p className="text-[12px] text-muted-foreground">Loading…</p>
      ) : !data?.skills?.length ? (
        <p className="text-[12px] text-muted-foreground">
          No skills yet — create one.
        </p>
      ) : (
        <div className="space-y-1">
          {data.skills.map((s) => (
            <div
              key={s.name}
              className="flex items-center gap-2 rounded-md border border-border/40 px-3 py-2 text-[12px]"
            >
              <span className="font-mono text-foreground">/{s.name}</span>
              <span className="rounded bg-muted px-1.5 py-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                {s.kind}
              </span>
              <span className="flex-1 truncate text-muted-foreground">
                {s.description || "(no description)"}
              </span>
              <button
                type="button"
                onClick={() => setEditing(s)}
                className="rounded border border-border/60 px-2 py-0.5 text-[11px] hover:bg-accent"
              >
                Edit
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Remove /${s.name}?`)) remove.mutate(s.name);
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
  );
}

export function SkillEditor({
  initial,
  onCancel,
  onSave,
  saving,
}: {
  initial: Skill | null;
  onCancel: () => void;
  onSave: (s: { name: string; description: string; kind: "prompt" | "shell"; body: string }) => void;
  saving: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [kind, setKind] = useState<"prompt" | "shell">(initial?.kind ?? "prompt");
  const [body, setBody] = useState(initial?.body ?? "");

  return (
    <Section
      title={initial ? `Edit /${initial.name}` : "New skill"}
      icon={<Wrench className="size-3.5" />}
    >
      <div className="space-y-3">
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Name
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="optimize-images"
            disabled={!!initial}
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] outline-none focus:border-primary disabled:opacity-60"
          />
          <p className="mt-1 text-[11px] text-muted-foreground">
            Lowercase, kebab-case. Used as the slash-command name.
          </p>
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Description
          </label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Compress all PNG/JPG in public/ via sharp"
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-[12px] outline-none focus:border-primary"
          />
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Kind
          </label>
          <div className="flex gap-2">
            {(["prompt", "shell"] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-[11.5px]",
                  kind === k
                    ? "border-primary/60 bg-primary/15 text-primary"
                    : "border-border/60 hover:bg-accent",
                )}
              >
                {k}
              </button>
            ))}
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            <strong>prompt</strong>: a system-prompt template the model uses
            when invoked. <strong>shell</strong>: a literal command run in the
            sandbox.
          </p>
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
            Body
          </label>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={
              kind === "prompt"
                ? "You are a helpful assistant. Take the user's request and..."
                : 'bunx sharp-cli --input "public/**/*.{png,jpg}" --output "public/" --format webp'
            }
            rows={10}
            className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-[11.5px] leading-snug outline-none focus:border-primary"
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onSave({ name, description, kind, body })}
            disabled={saving}
            className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border px-3 py-1.5 text-[11.5px] hover:bg-accent"
          >
            Cancel
          </button>
        </div>
      </div>
    </Section>
  );
}
