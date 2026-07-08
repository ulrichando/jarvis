"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { type WorkspaceMeta, patchWorkspace, Section } from "./shared";

// ── Dev command override ───────────────────────────────────────────────

export function DevCommandSection({
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

  useEffect(() => {
    if (!dirty) setDraft(ws?.devCommand ?? "");
  }, [ws?.devCommand, dirty]);

  const save = useMutation({
    mutationFn: () => patchWorkspace(workspaceId, { devCommand: draft }),
    onSuccess: () => {
      toast.success("Dev command saved");
      setDirty(false);
      onSaved();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  return (
    <Section
      title="Dev command override"
      hint="Replaces `bun run dev` when set. Must bind 0.0.0.0:5173 — that's the only port exposed to the host. Leave blank to use the project's default."
    >
      <div className="flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setDirty(true);
          }}
          placeholder='e.g. "next dev -p 5173 -H 0.0.0.0"'
          className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 font-mono text-[12px] outline-none focus:border-primary"
        />
        {dirty && (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(ws?.devCommand ?? "");
                setDirty(false);
              }}
              className="rounded-md border border-border px-2 py-1.5 text-[11.5px] hover:bg-accent"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="rounded-md border border-primary/50 bg-primary/10 px-2 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            >
              {save.isPending ? "Saving…" : "Save"}
            </button>
          </>
        )}
      </div>
    </Section>
  );
}
