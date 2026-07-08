"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { type WorkspaceMeta, patchWorkspace, Section } from "./shared";

// ── Custom instructions (.cursorrules / CLAUDE.md analog) ───────────────

export function CustomInstructionsSection({
  ws,
  workspaceId,
  onSaved,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);

  // Sync draft when server-side value changes (and we haven't started editing).
  useEffect(() => {
    if (!dirty) setDraft(ws?.customInstructions ?? "");
  }, [ws?.customInstructions, dirty]);

  const save = useMutation({
    mutationFn: () =>
      patchWorkspace(workspaceId, { customInstructions: draft }),
    onSuccess: () => {
      toast.success("Custom instructions saved");
      setDirty(false);
      onSaved();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const len = draft.length;
  const max = 8192;

  return (
    <Section
      title="Custom instructions"
      hint="Workspace-scoped rules for the AI. Appended to every chat turn's system prompt — same role as Cursor's .cursorrules or Claude Code's CLAUDE.md. Keep it tight; 8K char limit."
    >
      <textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setDirty(true);
        }}
        placeholder={`e.g.\n- Always use server components by default\n- Never use any package whose version is < 1.0\n- Match the existing test style in this repo`}
        rows={8}
        maxLength={max}
        className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-[12px] leading-snug outline-none focus:border-primary"
      />
      <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span className={cn(len > max * 0.9 && "text-amber-500")}>
          {len.toLocaleString()} / {max.toLocaleString()} chars
        </span>
        {dirty && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setDraft(ws?.customInstructions ?? "");
                setDirty(false);
              }}
              className="rounded-md border border-border px-2 py-1 text-[11px] hover:bg-accent"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="rounded-md border border-primary/50 bg-primary/10 px-2 py-1 text-[11px] text-primary hover:bg-primary/15 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        )}
      </div>
    </Section>
  );
}
