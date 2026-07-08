"use client";

import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Archive } from "lucide-react";
import { toast } from "sonner";
import { type GitStatus, Section, relativeTime } from "./shared";

// ── Backups (git-backed) ──────────────────────────────────────────────

export type CommitInfo = {
  sha: string;
  shortSha: string;
  subject: string;
  ts: number;
};

export async function fetchCommits(id: string): Promise<{ commits: CommitInfo[] }> {
  const r = await fetch(`/api/workspace/${id}/commit`);
  if (!r.ok) return { commits: [] };
  return r.json();
}

export async function postRestore(id: string, sha: string) {
  const r = await fetch(`/api/workspace/${id}/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "restore", sha }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? r.statusText);
  }
  return r.json();
}

export async function postCommit(id: string, message: string) {
  const r = await fetch(`/api/workspace/${id}/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? r.statusText);
  }
  return r.json();
}

export function BackupsSection({
  git,
  workspaceId,
  onChanged,
}: {
  git: GitStatus | null;
  workspaceId: string;
  onChanged: () => void;
}) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState("");
  const { data } = useQuery({
    queryKey: ["ws", workspaceId, "commits"],
    queryFn: () => fetchCommits(workspaceId),
    refetchOnWindowFocus: false,
  });
  const commit = useMutation({
    mutationFn: () => postCommit(workspaceId, msg.trim() || "manual snapshot"),
    onSuccess: (j: { commit: { shortSha: string } | null }) => {
      if (j.commit) toast.success(`Snapshot ${j.commit.shortSha} created`);
      else toast.message("Nothing to snapshot — working tree is clean");
      setMsg("");
      qc.invalidateQueries({ queryKey: ["ws", workspaceId, "commits"] });
      onChanged();
    },
    onError: (err: Error) => toast.error(`Snapshot failed: ${err.message}`),
  });
  const restore = useMutation({
    mutationFn: (sha: string) => postRestore(workspaceId, sha),
    onSuccess: (_data, sha) => {
      toast.success(`Restored to ${sha.slice(0, 7)}`);
      qc.invalidateQueries({ queryKey: ["ws", workspaceId] });
    },
    onError: (err: Error) => toast.error(`Restore failed: ${err.message}`),
  });

  const commits = data?.commits ?? [];

  return (
    <>
      <Section
        title="Snapshot now"
        icon={<Archive className="size-3.5" />}
        hint="Each workspace is a git repo. The AI auto-snapshots after every successful turn; this is for manual checkpoints."
      >
        <div className="flex items-center gap-2">
          <input
            value={msg}
            onChange={(e) => setMsg(e.target.value)}
            placeholder="What's this snapshot for?"
            className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-[12px] outline-none focus:border-primary"
          />
          <button
            type="button"
            onClick={() => commit.mutate()}
            disabled={commit.isPending || (git?.dirtyCount ?? 0) === 0}
            className="rounded-md border border-primary/50 bg-primary/10 px-3 py-1.5 text-[11.5px] text-primary hover:bg-primary/15 disabled:opacity-50"
            title={
              (git?.dirtyCount ?? 0) === 0
                ? "Nothing to snapshot — working tree clean"
                : "Create a manual snapshot"
            }
          >
            {commit.isPending ? "Saving…" : "Snapshot"}
          </button>
        </div>
        {git && (
          <div className="mt-2 text-[11.5px] text-muted-foreground">
            {git.branch && <>Branch <span className="font-mono">{git.branch}</span> · </>}
            {git.dirtyCount === 0
              ? "Working tree clean"
              : `${git.dirtyCount} pending change${git.dirtyCount === 1 ? "" : "s"}`}
          </div>
        )}
      </Section>

      <Section
        title="Snapshot history"
        hint="Most recent first. Click Restore to reset the workspace to a prior snapshot — this discards uncommitted changes; snapshot first if you want to keep them."
      >
        {commits.length === 0 ? (
          <p className="text-[12px] text-muted-foreground">
            No snapshots yet. The AI creates one automatically after each
            successful turn.
          </p>
        ) : (
          <div className="space-y-1">
            {commits.map((c) => (
              <div
                key={c.sha}
                className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 text-[12px]"
              >
                <span className="font-mono text-[11px] text-muted-foreground">
                  {c.shortSha}
                </span>
                <span className="flex-1 truncate">{c.subject}</span>
                <span className="whitespace-nowrap text-[11px] text-muted-foreground">
                  {relativeTime(c.ts)}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (
                      confirm(
                        `Restore to "${c.subject}" (${c.shortSha})? Uncommitted changes will be lost.`,
                      )
                    )
                      restore.mutate(c.sha);
                  }}
                  disabled={restore.isPending}
                  className="rounded border border-border/60 px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-50"
                >
                  Restore
                </button>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}
