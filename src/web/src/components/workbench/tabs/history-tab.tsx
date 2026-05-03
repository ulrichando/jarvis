"use client";

import { useState } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  GitCommit,
  RotateCcw,
  Loader2,
  GitBranch,
  Check,
  AlertCircle,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

type CommitInfo = {
  sha: string;
  shortSha: string;
  subject: string;
  ts: number;
};

type Props = { workspaceId: string };

function fmtRelative(ts: number): string {
  const delta = Date.now() - ts;
  if (delta < 60_000) return "just now";
  const mins = Math.floor(delta / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(ts).toLocaleDateString();
}

export function HistoryTab({ workspaceId }: Props) {
  const qc = useQueryClient();
  const [restoringSha, setRestoringSha] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["ws", workspaceId, "commits"],
    queryFn: async (): Promise<CommitInfo[]> => {
      const r = await fetch(`/api/workspace/${workspaceId}/commit`);
      if (!r.ok) throw new Error("failed to load history");
      const j = await r.json();
      return j.commits ?? [];
    },
    refetchInterval: 5000,
  });

  const restore = useMutation({
    mutationFn: async (sha: string) => {
      const r = await fetch(
        `/api/workspace/${workspaceId}/commit/restore`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ sha }),
        },
      );
      if (!r.ok) throw new Error("restore failed");
      return r.json();
    },
    onMutate: (sha) => {
      setRestoringSha(sha);
    },
    onSuccess: (_data, sha) => {
      toast.success(`Restored to ${sha.slice(0, 7)}`);
      qc.invalidateQueries({ queryKey: ["ws", workspaceId] });
      qc.invalidateQueries({ queryKey: ["design-tree", workspaceId] });
      setRestoringSha(null);
    },
    onError: (err) => {
      toast.error(`Restore failed: ${err}`);
      setRestoringSha(null);
    },
  });

  const commits = data ?? [];

  return (
    <div className="flex h-full w-full flex-col bg-background">
      <Header workspaceId={workspaceId} commits={commits} />

      <div className="flex-1 min-h-0 overflow-y-auto">
        {isLoading && (
          <div className="flex items-center justify-center py-10 text-[12px] text-muted-foreground">
            <Loader2 className="mr-2 size-3.5 animate-spin" /> Loading…
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 px-4 py-6 text-[12px] text-destructive">
            <AlertCircle className="size-3.5" /> Couldn't load history.
          </div>
        )}

        {!isLoading && commits.length === 0 && !error && (
          <div className="px-4 py-10 text-center text-[12px] text-muted-foreground">
            No commits yet. The first turn that writes files will create one.
          </div>
        )}

        <ol className="relative px-4 py-3">
          {commits.map((c, i) => (
            <li key={c.sha} className="relative pl-7 pb-4 last:pb-1">
              {/* spine */}
              {i < commits.length - 1 && (
                <span
                  aria-hidden
                  className="absolute left-2.5 top-5 bottom-0 w-px bg-border"
                />
              )}
              {/* dot */}
              <span
                aria-hidden
                className={cn(
                  "absolute left-1.5 top-1.5 flex size-3 items-center justify-center rounded-full border bg-background",
                  i === 0
                    ? "border-primary text-primary"
                    : "border-border text-muted-foreground",
                )}
              >
                <GitCommit className="size-2" />
              </span>

              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-medium text-foreground">
                    {c.subject || "(no message)"}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                    <code className="font-mono">{c.shortSha}</code>
                    <span>·</span>
                    <span>{fmtRelative(c.ts)}</span>
                  </div>
                </div>

                {i > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      if (
                        confirm(
                          `Restore workspace to ${c.shortSha}?\n\n"${c.subject}"\n\nThis overwrites every file with the state from that commit. Newer commits stay in history (you can roll forward).`,
                        )
                      ) {
                        restore.mutate(c.sha);
                      }
                    }}
                    disabled={restoringSha !== null}
                    className={cn(
                      "flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] font-medium",
                      "text-muted-foreground hover:text-foreground hover:bg-muted/40",
                      "disabled:opacity-50 disabled:cursor-not-allowed",
                    )}
                  >
                    {restoringSha === c.sha ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <RotateCcw className="size-3" />
                    )}
                    Restore
                  </button>
                )}

                {i === 0 && (
                  <span className="flex shrink-0 items-center gap-1 rounded-md bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary">
                    <Check className="size-3" /> Current
                  </span>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function Header({
  workspaceId,
  commits,
}: {
  workspaceId: string;
  commits: CommitInfo[];
}) {
  const [pushing, setPushing] = useState(false);

  const onPush = async () => {
    const ownerRepo = window.prompt(
      "Push to GitHub — enter <owner>/<repo> (the repo must already exist):",
    );
    if (!ownerRepo) return;
    if (!/^[\w.-]+\/[\w.-]+$/.test(ownerRepo)) {
      toast.error("Format must be <owner>/<repo>");
      return;
    }
    setPushing(true);
    try {
      const r = await fetch(`/api/workspace/${workspaceId}/push`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ownerRepo }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        if (body.error === "missing_token") {
          toast.error(
            "Add a GitHub token in Settings → Integrations first.",
          );
        } else {
          toast.error(`Push failed: ${body.message ?? r.statusText}`);
        }
      } else {
        toast.success(`Pushed to ${ownerRepo}`);
      }
    } catch (err) {
      toast.error(`Push failed: ${err}`);
    } finally {
      setPushing(false);
    }
  };

  return (
    <div className="flex h-10 items-center justify-between border-b border-border/50 px-4">
      <div className="flex items-center gap-2 text-[12px]">
        <span className="font-medium text-foreground">History</span>
        <span className="text-muted-foreground">
          {commits.length === 0
            ? "—"
            : `${commits.length} commit${commits.length === 1 ? "" : "s"}`}
        </span>
      </div>
      <button
        type="button"
        onClick={onPush}
        disabled={pushing || commits.length === 0}
        className={cn(
          "flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-[11px] font-medium",
          "text-muted-foreground hover:text-foreground hover:bg-muted/40",
          "disabled:opacity-50 disabled:cursor-not-allowed",
        )}
      >
        {pushing ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <GitBranch className="size-3" />
        )}
        Push to GitHub
      </button>
    </div>
  );
}
