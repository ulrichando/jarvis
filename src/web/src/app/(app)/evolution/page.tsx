"use client";

// /evolution — review + approve the changes JARVIS proposes to its own code.
// Refined-minimal, within the app's existing design system. The defining idea:
// make the SAFETY NET visible — every approved deploy is health-checked and
// auto-rolled-back if unhealthy — so approving self-modification feels trusted,
// not reckless. The deploy/restart action is gated behind a two-step confirm.
import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FileCode2,
  GitPullRequest,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Proposal = {
  id: string;
  title: string;
  intent: string;
  files: string[];
  diffSummary: string;
  testsOk: boolean;
  prUrl: string | null;
  createdAt: string | null;
};

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "";
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export default function EvolutionPage() {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deploying, setDeploying] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/evolution", { cache: "no-store" });
      const data = (await res.json()) as { proposals?: Proposal[] };
      setProposals(data.proposals ?? []);
    } catch {
      setProposals([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const approve = useCallback(
    async (id: string) => {
      setConfirming(null);
      setDeploying(id);
      try {
        const res = await fetch(`/api/evolution/${id}/approve`, { method: "POST" });
        const data = (await res.json()) as { ok?: boolean; detail?: string };
        if (res.ok && data.ok) {
          toast.success("Deploying — the watchdog is verifying health and will auto-roll-back if it's unhealthy.");
          setProposals((p) => (p ? p.filter((x) => x.id !== id) : p));
        } else {
          toast.error(data.detail || "Deploy was refused or failed.");
        }
      } catch {
        toast.error("Couldn't reach the server.");
      } finally {
        setDeploying(null);
      }
    },
    [],
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-10">
        {/* Header */}
        <div className="flex items-center gap-2.5">
          <GitPullRequest className="size-5 text-primary" />
          <h1 className="font-serif text-[22px] font-semibold tracking-tight">
            Evolution
          </h1>
        </div>
        <p className="mt-1.5 text-[14px] leading-6 text-muted-foreground">
          Changes JARVIS has proposed to its own source. Review the diff, then
          approve — approving deploys it and restarts him into the new code.
        </p>

        {/* Safety-net reassurance — the defining, trust-building detail */}
        <div className="mt-5 flex items-start gap-3 rounded-xl border border-border/60 bg-card/50 px-4 py-3">
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-emerald-500" />
          <p className="text-[13px] leading-5 text-muted-foreground">
            <span className="font-medium text-foreground">Auto-rollback is on.</span>{" "}
            After a deploy, an external watchdog checks JARVIS comes back healthy.
            If he doesn't, it reverts to the last-good version and restarts him —
            automatically, within a few minutes. Nothing you approve can leave him
            broken.
          </p>
        </div>

        {/* List */}
        <div className="mt-7">
          {proposals === null ? (
            <div className="flex items-center gap-2 py-16 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              <span className="text-[13.5px]">Loading proposals…</span>
            </div>
          ) : proposals.length === 0 ? (
            <EmptyState />
          ) : (
            <ul className="space-y-3">
              <AnimatePresence initial={false}>
                {proposals.map((p, i) => (
                  <motion.li
                    key={p.id}
                    layout
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0, transition: { delay: i * 0.04 } }}
                    exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.15 } }}
                  >
                    <ProposalCard
                      p={p}
                      confirming={confirming === p.id}
                      deploying={deploying === p.id}
                      onAskConfirm={() => setConfirming(p.id)}
                      onCancel={() => setConfirming(null)}
                      onConfirm={() => approve(p.id)}
                    />
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function ProposalCard({
  p,
  confirming,
  deploying,
  onAskConfirm,
  onCancel,
  onConfirm,
}: {
  p: Proposal;
  confirming: boolean;
  deploying: boolean;
  onAskConfirm: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="rounded-2xl border border-border/60 bg-card/60 p-5 transition-colors hover:border-border">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-[15px] font-medium leading-snug text-foreground">
          {p.title}
        </h2>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
            p.testsOk
              ? "bg-emerald-500/10 text-emerald-500"
              : "bg-amber-500/10 text-amber-500",
          )}
        >
          {p.testsOk ? (
            <CheckCircle2 className="size-3" />
          ) : (
            <AlertTriangle className="size-3" />
          )}
          {p.testsOk ? "tests pass" : "check tests"}
        </span>
      </div>

      {p.intent && p.intent !== p.title && (
        <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-[13px] leading-5 text-muted-foreground">
          {p.intent}
        </p>
      )}

      {p.files.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {p.files.slice(0, 6).map((f) => (
            <span
              key={f}
              className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
            >
              <FileCode2 className="size-3" />
              {f.replace(/^src\/voice-agent\//, "")}
            </span>
          ))}
          {p.files.length > 6 && (
            <span className="px-1 py-0.5 text-[11px] text-muted-foreground">
              +{p.files.length - 6} more
            </span>
          )}
        </div>
      )}

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-[12px] text-muted-foreground">
          <span>{timeAgo(p.createdAt)}</span>
          {p.prUrl && (
            <a
              href={p.prUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-foreground/80 hover:text-foreground"
            >
              <ExternalLink className="size-3" />
              View PR
            </a>
          )}
        </div>

        {deploying ? (
          <Button size="sm" disabled className="gap-1.5">
            <Loader2 className="size-3.5 animate-spin" />
            Deploying…
          </Button>
        ) : confirming ? (
          <div className="flex items-center gap-2">
            <span className="hidden text-[11.5px] text-muted-foreground sm:inline">
              Deploy &amp; restart JARVIS?
            </span>
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" onClick={onConfirm} className="gap-1.5">
              <ShieldCheck className="size-3.5" />
              Confirm deploy
            </Button>
          </div>
        ) : (
          <Button size="sm" variant="outline" onClick={onAskConfirm}>
            Approve &amp; deploy
          </Button>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border/60 px-6 py-16 text-center">
      <div className="flex size-11 items-center justify-center rounded-full bg-muted/40">
        <GitPullRequest className="size-5 text-muted-foreground" />
      </div>
      <p className="mt-4 text-[14px] font-medium text-foreground">
        Nothing to review
      </p>
      <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted-foreground">
        JARVIS proposes improvements to his own code during quiet hours. When he
        does, they&apos;ll appear here for your approval — each one tested and
        safe to roll back.
      </p>
    </div>
  );
}
