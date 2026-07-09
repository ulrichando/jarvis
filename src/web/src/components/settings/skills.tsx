"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, Trash2, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Personal-scoped skills — reusable recipes JARVIS can invoke across any
 * chat, the CLI, and the voice agent. Backed by /api/skills →
 * ~/.jarvis/skills/<name>/SKILL.md (Anthropic SKILL.md format: YAML
 * frontmatter name/description/when_to_use + markdown body). Workspace-
 * scoped skills live in the workbench Settings tab.
 */

type PersonalSkill = {
  name: string;
  description: string;
  whenToUse: string;
  bytes: number;
  updatedAt: number;
  layout: "dir" | "flat";
};

function fmtSize(bytes: number): string {
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

const inputCls =
  "w-full rounded-md border border-border bg-background px-2.5 py-1.5 font-mono text-[12px] outline-none focus:border-primary";

export function SkillsSection() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [whenToUse, setWhenToUse] = useState("");
  const [body, setBody] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["skills"],
    queryFn: async () => {
      const r = await fetch("/api/skills");
      if (!r.ok) throw new Error(r.statusText);
      return (await r.json()) as { skills: PersonalSkill[] };
    },
    refetchOnWindowFocus: false,
  });

  const create = useMutation({
    mutationFn: async (skill: {
      name: string;
      description: string;
      whenToUse: string;
      body: string;
    }) => {
      const r = await fetch("/api/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(skill),
      });
      if (!r.ok) {
        const j = (await r.json().catch(() => ({}))) as { error?: string };
        throw new Error(j.error ?? r.statusText);
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Skill saved");
      qc.invalidateQueries({ queryKey: ["skills"] });
      setName("");
      setDescription("");
      setWhenToUse("");
      setBody("");
      setShowForm(false);
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const remove = useMutation({
    mutationFn: async (skillName: string) => {
      const r = await fetch(`/api/skills?name=${encodeURIComponent(skillName)}`, {
        method: "DELETE",
      });
      if (!r.ok) throw new Error(r.statusText);
      return r.json();
    },
    onSuccess: () => {
      toast.success("Skill removed (recoverable from ~/.jarvis/.skills-trash)");
      qc.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (e: Error) => toast.error(`Remove failed: ${e.message}`),
  });

  const skills = data?.skills ?? [];

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <Wrench className="size-4 text-muted-foreground" />
          <h2 className="text-lg font-semibold">Skills</h2>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Reusable recipes JARVIS can invoke — shared with the{" "}
          <code className="font-mono text-[12px]">jarvis</code> CLI and the
          voice agent. Stored as{" "}
          <code className="font-mono text-[12px]">
            ~/.jarvis/skills/&lt;name&gt;/SKILL.md
          </code>{" "}
          (YAML frontmatter + markdown body).
        </p>
      </div>

      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-medium">
          Your skills{isLoading ? "" : ` (${skills.length})`}
        </h3>
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-card/40 px-3 py-1.5 text-[12px] text-foreground hover:border-border hover:bg-card/70"
        >
          <Plus className="size-3.5" />
          New skill
        </button>
      </div>

      {showForm && (
        <div className="space-y-2 rounded-lg border border-border/50 bg-card/30 p-4">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="skill-name (lowercase, hyphens)"
            className={inputCls}
          />
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What the skill does (one line)"
            className={inputCls}
          />
          <input
            value={whenToUse}
            onChange={(e) => setWhenToUse(e.target.value)}
            placeholder="When JARVIS should invoke it (optional)"
            className={inputCls}
          />
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Instructions (markdown body — the recipe JARVIS follows)…"
            rows={8}
            className={`${inputCls} resize-y leading-snug`}
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              disabled={create.isPending}
              onClick={() => {
                if (!name.trim() || !description.trim() || !body.trim()) {
                  toast.error("Name, description, and instructions required");
                  return;
                }
                create.mutate({ name, description, whenToUse, body });
              }}
            >
              {create.isPending ? "Saving…" : "Save skill"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {isLoading ? (
        <p className="text-[13px] text-muted-foreground">Loading…</p>
      ) : skills.length === 0 ? (
        <div className="rounded-lg border border-border/50 bg-card/20 p-6 text-center text-[13px] text-muted-foreground">
          No skills yet — create one above.
        </div>
      ) : (
        <div className="space-y-1.5">
          {skills.map((s) => (
            <div
              key={s.name}
              className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 text-[12px]"
            >
              <span className="shrink-0 font-mono text-foreground">
                /{s.name}
              </span>
              <span
                className="flex-1 truncate text-muted-foreground"
                title={s.whenToUse || s.description}
              >
                {s.description || "(no description)"}
              </span>
              <span className="shrink-0 text-muted-foreground">
                {fmtSize(s.bytes)}
              </span>
              <button
                type="button"
                onClick={() => remove.mutate(s.name)}
                disabled={remove.isPending}
                className="shrink-0 text-muted-foreground hover:text-destructive"
                aria-label={`Remove ${s.name}`}
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
