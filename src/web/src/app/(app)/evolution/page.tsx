"use client";

// /evolution — review the changes JARVIS proposes to its own code, AND watch
// the self-evolution loop's health. Refined-minimal, within the app's existing
// design system. The defining idea: make the SAFETY NET visible — every
// approved deploy is health-checked and auto-rolled-back if unhealthy — so
// approving self-modification feels trusted, not reckless. Deploy/revert are
// gated behind a two-step confirm.
import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Activity as ActivityIcon,
  AlertTriangle,
  Brain,
  CheckCircle2,
  ExternalLink,
  FileCode2,
  GitPullRequest,
  Hammer,
  Loader2,
  Pause,
  Play,
  Radar,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { categorize, CATEGORIES, CATEGORY_TONE, type Category } from "@/lib/evolution/categorize";
import { Sparkline } from "./Sparkline";

// ── Types (mirror GET /api/evolution) ────────────────────────────────────
type Proposal = {
  id: string;
  title: string;
  intent: string;
  files: string[];
  diffSummary: string;
  diff: string;
  diffTruncated: boolean;
  testsOk: boolean;
  prUrl: string | null;
  createdAt: string | null;
  status: string;
  rejectionReason: string;
  testOutput: string;
  coverageGate: { status: string; score: number | null; covered: number; measurable: number };
  priority: string;
  review?: Review | null;
};

type ReviewLens = { verdict: string; findings: string[]; summary: string; model?: string };
type Review = {
  overall: { verdict: string; recommendation: string };
  lenses: Record<string, ReviewLens>;
  model?: string;
  generated_at?: string;
};

const LENS_ORDER = ["correctness", "security", "regression"];
const verdictDot = (v: string) =>
  v === "pass" ? "bg-emerald-500" : v === "block" ? "bg-rose-500" : v === "concern" ? "bg-amber-500" : "bg-muted-foreground/40";
const verdictText = (v: string) =>
  v === "pass" ? "text-emerald-500" : v === "block" ? "text-rose-500" : v === "concern" ? "text-amber-500" : "text-muted-foreground";

type Deployed = {
  id: string;
  title: string;
  intent: string;
  mergeSha: string;
  rollbackSha: string;
  rollbackRef: string;
  createdAt: string | null;
  canRevert: boolean;
  files: string[];
  priority: string;
};

type Activity = {
  id: string;
  status: string;
  kind: string;
  title: string;
  detail: string;
  createdAt: string | null;
  automodId?: string;
};

type Criterion = { id: string; group: string; label: string; description: string };

type Fitness = {
  points: { ts: string; composite: number; passed: boolean }[];
  latest: number | null;
  latestAt: string | null;
  count: number;
  trend: "up" | "down" | "flat" | null;
  perAxis: Record<string, number>;
  weakAxis: { axis: string; score: number } | null;
  error?: string;
};

type Graduation = {
  metCount: number;
  total: number;
  criteria: { id: string; label: string; met: boolean; detail: string }[];
};

type SelfAssessment = {
  summary?: string;
  flaws?: string[];
  improvements?: string[];
  generatedAt?: string;
  [k: string]: unknown;
} | null;

type EvolutionData = {
  proposals: Proposal[];
  failed: Proposal[];
  deployed: Deployed[];
  queued: Activity[];
  paused: boolean;
  autoMode: boolean;
  mode: "auto" | "manual";
  buildModel: string;
  activity: Activity[];
  rollbackEvents: Activity[];
  selfAssessment: SelfAssessment;
  criteria: Criterion[];
  autonomy: { currentLabel: string; targetLabel: string; graduationCriteria: string[] };
  graduation: Graduation;
  fitness: Fitness;
  status: {
    pending: number;
    queued: number;
    failedCount: number;
    deployed: number;
    failed: number;
    builds: { today: number; cap: number; remaining: number };
    building: number;
    buildingIds: string[];
    deployInFlight: boolean;
    rollbacks: number;
  };
};

const BUILD_MODELS = [
  { value: "", label: "Global model" },
  { value: "deepseek-v4-pro", label: "DeepSeek v4 Pro" },
  { value: "claude-opus-4-8", label: "Claude Opus 4.8" },
  { value: "kimi-k2.7-code", label: "Kimi K2.7 Code" },
];

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

const stripPrefix = (f: string) => f.replace(/^src\/voice-agent\//, "");

export default function EvolutionPage() {
  const [data, setData] = useState<EvolutionData | null>(null);
  const [tab, setTab] = useState("review");
  const [catFilter, setCatFilter] = useState<Category | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // "approve:<id>" / "cycle" / ...
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/evolution", { cache: "no-store" });
      setData((await res.json()) as EvolutionData);
    } catch {
      /* keep prior state on a transient failure */
    }
  }, []);

  useEffect(() => {
    void load();
    pollRef.current = setInterval(() => void load(), 6000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load]);

  // Generic POST helper — toasts the detail, reloads, manages the busy key.
  const act = useCallback(
    async (
      key: string,
      url: string,
      body: unknown,
      ok: string,
      onOk?: () => void,
    ) => {
      setBusy(key);
      try {
        const res = await fetch(url, {
          method: "POST",
          ...(body !== undefined
            ? { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }
            : {}),
        });
        const r = (await res.json().catch(() => ({}))) as { ok?: boolean; detail?: string };
        if (res.ok && r.ok !== false) {
          toast.success(r.detail || ok);
          onOk?.();
        } else {
          toast.error(r.detail || "That action was refused or failed.");
        }
      } catch {
        toast.error("Couldn't reach the server.");
      } finally {
        setBusy(null);
        void load();
      }
    },
    [load],
  );

  const approve = (id: string) => {
    setConfirming(null);
    void act(
      `approve:${id}`,
      `/api/evolution/${id}/approve`,
      undefined,
      "Deploying — the watchdog is verifying health and will auto-roll-back if unhealthy.",
      () => setData((d) => (d ? { ...d, proposals: d.proposals.filter((x) => x.id !== id) } : d)),
    );
  };
  const reject = (id: string) =>
    act(`reject:${id}`, `/api/evolution/${id}/reject`, {}, "Proposal rejected; its branch was deleted.");
  const runReview = (id: string) =>
    act(`review:${id}`, `/api/evolution/${id}/review`, undefined, "Review council finished — verdict is on the card.");
  const process = (id: string) =>
    act(`process:${id}`, `/api/evolution/${id}/process`, undefined, "Building — turning this into a reviewable diff.");
  const dismiss = (id: string) =>
    act(`dismiss:${id}`, `/api/evolution/${id}/dismiss`, undefined, "Intent dismissed from the queue.");
  const revert = (id: string) => {
    setConfirming(null);
    void act(`revert:${id}`, `/api/evolution/${id}/revert`, undefined, "Rolling back to the last-good version and restarting.");
  };
  const introspect = () =>
    act("introspect", "/api/evolution/introspect", undefined, "Self-assessment complete.");
  const cycle = () =>
    act("cycle", "/api/evolution/cycle", undefined, "Build cycle started — watch Review / Failed.");
  const setPaused = (paused: boolean) =>
    act("pause", "/api/evolution/pause", { paused }, paused ? "Evolution paused." : "Evolution resumed.");
  const setMode = (mode: "manual" | "auto") =>
    act("mode", "/api/evolution/mode", { mode }, `Switched to ${mode} mode.`);
  const setBuildModel = (model: string) =>
    act("buildModel", "/api/evolution/build-model", { model }, "Build model updated.");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-10">
        {/* Header */}
        <div className="flex items-center gap-2.5">
          <GitPullRequest className="size-5 text-primary" />
          <h1 className="font-serif text-[22px] font-semibold tracking-tight">Evolution</h1>
          {data && (
            <ModePill
              mode={data.mode}
              paused={data.paused}
              busy={busy === "mode" || busy === "pause"}
              onMode={setMode}
              onPause={() => setPaused(!data.paused)}
            />
          )}
        </div>
        <p className="mt-1.5 text-[14px] leading-6 text-muted-foreground">
          Changes JARVIS has proposed to its own source, and the health of the loop that
          produces them. Review a diff, then approve — approving deploys it and restarts
          him into the new code.
        </p>

        {/* Safety-net reassurance — the defining, trust-building detail */}
        <div className="mt-5 flex items-start gap-3 rounded-xl border border-border/60 bg-card/50 px-4 py-3">
          <ShieldCheck className="mt-0.5 size-4 shrink-0 text-emerald-500" />
          <p className="text-[13px] leading-5 text-muted-foreground">
            <span className="font-medium text-foreground">Auto-rollback is on.</span>{" "}
            After a deploy, an external watchdog checks JARVIS comes back healthy. If he
            doesn&apos;t, it reverts to the last-good version and restarts him — automatically,
            within a few minutes. Nothing you approve can leave him broken.
          </p>
        </div>

        {/* Status + controls */}
        {data && (
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <Stat label="pending" value={data.status.pending} />
            <Stat label="queued" value={data.status.queued} />
            {data.status.building > 0 && (
              <Stat label="building" value={data.status.building} live />
            )}
            <Stat label="deployed" value={data.status.deployed} tone="emerald" />
            <Stat label="failed" value={data.status.failed} tone="amber" />
            {data.status.rollbacks > 0 && (
              <Stat label="rollbacks" value={data.status.rollbacks} tone="amber" />
            )}
            <div className="ml-auto flex items-center gap-2">
              <span className="text-[11.5px] text-muted-foreground">
                {data.status.builds.remaining}/{data.status.builds.cap} builds left today
              </span>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                disabled={busy === "introspect"}
                onClick={introspect}
              >
                {busy === "introspect" ? <Loader2 className="size-3.5 animate-spin" /> : <Brain className="size-3.5" />}
                Introspect
              </Button>
              <Button
                size="sm"
                className="gap-1.5"
                disabled={busy === "cycle" || data.paused}
                onClick={cycle}
              >
                {busy === "cycle" ? <Loader2 className="size-3.5 animate-spin" /> : <Hammer className="size-3.5" />}
                Run cycle
              </Button>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="mt-6">
          {data === null ? (
            <div className="flex items-center gap-2 py-16 text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              <span className="text-[13.5px]">Loading evolution state…</span>
            </div>
          ) : (
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList>
                <TabsTrigger value="review">
                  Review{data.proposals.length > 0 ? ` · ${data.proposals.length}` : ""}
                </TabsTrigger>
                <TabsTrigger value="health">Health</TabsTrigger>
                <TabsTrigger value="history">History</TabsTrigger>
              </TabsList>

              {/* REVIEW: pending proposals + queued intents, filterable by category */}
              <TabsContent value="review" className="mt-5 space-y-3">
                {data.proposals.length === 0 && data.queued.length === 0 ? (
                  <EmptyState />
                ) : (
                  (() => {
                    const propCat = (p: Proposal) => categorize(p.files, p.intent);
                    const queuedCat = (q: Activity) => categorize([], q.detail || q.title);
                    const present = CATEGORIES.filter(
                      (c) =>
                        data.proposals.some((p) => propCat(p) === c) ||
                        data.queued.some((q) => queuedCat(q) === c),
                    );
                    const proposals = catFilter
                      ? data.proposals.filter((p) => propCat(p) === catFilter)
                      : data.proposals;
                    const queued = catFilter
                      ? data.queued.filter((q) => queuedCat(q) === catFilter)
                      : data.queued;
                    return (
                      <>
                        {present.length > 1 && (
                          <CategoryFilterBar present={present} active={catFilter} onPick={setCatFilter} />
                        )}
                        <AnimatePresence initial={false}>
                          {proposals.map((p, i) => (
                            <motion.div
                              key={p.id}
                              layout
                              initial={{ opacity: 0, y: 8 }}
                              animate={{ opacity: 1, y: 0, transition: { delay: i * 0.04 } }}
                              exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.15 } }}
                            >
                              <ProposalCard
                                p={p}
                                category={propCat(p)}
                                confirming={confirming === p.id}
                                deploying={busy === `approve:${p.id}`}
                                rejecting={busy === `reject:${p.id}`}
                                reviewing={busy === `review:${p.id}`}
                                onAskConfirm={() => setConfirming(p.id)}
                                onCancel={() => setConfirming(null)}
                                onConfirm={() => approve(p.id)}
                                onReject={() => reject(p.id)}
                                onReview={() => runReview(p.id)}
                              />
                            </motion.div>
                          ))}
                        </AnimatePresence>

                        {queued.length > 0 && (
                          <div className="pt-2">
                            <SectionLabel>Queued intents</SectionLabel>
                            <div className="space-y-2">
                              {queued.map((q) => (
                                <QueuedRow
                                  key={q.id}
                                  q={q}
                                  category={queuedCat(q)}
                                  building={busy === `process:${q.id}`}
                                  dismissing={busy === `dismiss:${q.id}`}
                                  onProcess={() => process(q.id)}
                                  onDismiss={() => dismiss(q.id)}
                                />
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    );
                  })()
                )}
              </TabsContent>

              {/* HEALTH: fitness, criteria, graduation, self-assessment */}
              <TabsContent value="health" className="mt-5 space-y-5">
                <FitnessPanel fitness={data.fitness} />
                <GraduationPanel autonomy={data.autonomy} graduation={data.graduation} />
                <CriteriaGrid criteria={data.criteria} />
                <AssessmentPanel assessment={data.selfAssessment} />
              </TabsContent>

              {/* HISTORY: deployed, failed, activity, rollbacks */}
              <TabsContent value="history" className="mt-5 space-y-5">
                {data.deployed.length > 0 && (
                  <div>
                    <SectionLabel>Deployed</SectionLabel>
                    <div className="space-y-2">
                      {data.deployed.map((d) => (
                        <DeployedRow
                          key={d.id}
                          d={d}
                          confirming={confirming === `revert:${d.id}`}
                          reverting={busy === `revert:${d.id}`}
                          onAskRevert={() => setConfirming(`revert:${d.id}`)}
                          onCancel={() => setConfirming(null)}
                          onRevert={() => revert(d.id)}
                        />
                      ))}
                    </div>
                  </div>
                )}
                {data.failed.length > 0 && (
                  <div>
                    <SectionLabel>Failed</SectionLabel>
                    <div className="space-y-2">
                      {data.failed.map((f) => (
                        <FailedRow key={f.id} f={f} />
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <SectionLabel>Activity</SectionLabel>
                  {data.activity.length === 0 ? (
                    <p className="px-1 text-[13px] text-muted-foreground">No recent activity.</p>
                  ) : (
                    <div className="space-y-1.5">
                      {data.activity.map((a) => (
                        <ActivityRow key={a.id} a={a} />
                      ))}
                    </div>
                  )}
                </div>
                <BuildModelPicker
                  current={data.buildModel}
                  busy={busy === "buildModel"}
                  onChange={setBuildModel}
                />
              </TabsContent>
            </Tabs>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Small pieces ──────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-2 px-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
      {children}
    </p>
  );
}

function CategoryChip({ category }: { category: Category }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 text-[10.5px] font-medium",
        CATEGORY_TONE[category],
      )}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {category}
    </span>
  );
}

function CategoryFilterBar({
  present,
  active,
  onPick,
}: {
  present: Category[];
  active: Category | null;
  onPick: (c: Category | null) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 pb-1">
      <FilterPill label="All" active={active === null} onClick={() => onPick(null)} />
      {present.map((c) => (
        <FilterPill
          key={c}
          label={c}
          tone={CATEGORY_TONE[c]}
          active={active === c}
          onClick={() => onPick(active === c ? null : c)}
        />
      ))}
    </div>
  );
}

function FilterPill({
  label,
  tone,
  active,
  onClick,
}: {
  label: string;
  tone?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "border-primary/40 bg-primary/10 text-foreground"
          : "border-border/60 bg-card/40 text-muted-foreground hover:text-foreground",
      )}
    >
      {tone && <span className={cn("size-1.5 rounded-full bg-current", tone)} />}
      {label}
    </button>
  );
}

function Stat({
  label,
  value,
  tone,
  live,
}: {
  label: string;
  value: number;
  tone?: "emerald" | "amber";
  live?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-card/50 px-2.5 py-1 text-[12px]",
        tone === "emerald" && "text-emerald-500",
        tone === "amber" && "text-amber-500",
      )}
    >
      {live && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      <span className="font-medium tabular-nums text-foreground">{value}</span>
      <span className="text-muted-foreground">{label}</span>
    </span>
  );
}

function ModePill({
  mode,
  paused,
  busy,
  onMode,
  onPause,
}: {
  mode: "auto" | "manual";
  paused: boolean;
  busy: boolean;
  onMode: (m: "manual" | "auto") => void;
  onPause: () => void;
}) {
  return (
    <div className="ml-auto flex items-center gap-1.5">
      <div className="flex items-center rounded-full border border-border/60 bg-card/50 p-0.5 text-[11.5px]">
        {(["manual", "auto"] as const).map((m) => (
          <button
            key={m}
            type="button"
            disabled={busy}
            onClick={() => onMode(m)}
            className={cn(
              "rounded-full px-2 py-0.5 capitalize transition-colors",
              mode === m ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {m}
          </button>
        ))}
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="h-7 gap-1.5 px-2 text-[12px]"
        disabled={busy}
        onClick={onPause}
        title={paused ? "Resume evolution" : "Pause evolution"}
      >
        {paused ? <Play className="size-3.5 text-amber-500" /> : <Pause className="size-3.5" />}
        {paused ? "Paused" : "Pause"}
      </Button>
    </div>
  );
}

function PriorityDot({ priority }: { priority: string }) {
  const p = (priority || "P3").toUpperCase();
  const tone =
    p === "P0" || p === "P1" ? "bg-rose-500" : p === "P2" ? "bg-amber-500" : "bg-muted-foreground/50";
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground" title={`Priority ${p}`}>
      <span className={cn("size-1.5 rounded-full", tone)} />
      {p}
    </span>
  );
}

function FileChips({ files }: { files: string[] }) {
  if (!files?.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {files.slice(0, 6).map((f) => (
        <span
          key={f}
          className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
        >
          <FileCode2 className="size-3" />
          {stripPrefix(f)}
        </span>
      ))}
      {files.length > 6 && (
        <span className="px-1 py-0.5 text-[11px] text-muted-foreground">+{files.length - 6} more</span>
      )}
    </div>
  );
}

function ProposalCard({
  p,
  category,
  confirming,
  deploying,
  rejecting,
  reviewing,
  onAskConfirm,
  onCancel,
  onConfirm,
  onReject,
  onReview,
}: {
  p: Proposal;
  category: Category;
  confirming: boolean;
  deploying: boolean;
  rejecting: boolean;
  reviewing: boolean;
  onAskConfirm: () => void;
  onCancel: () => void;
  onConfirm: () => void;
  onReject: () => void;
  onReview: () => void;
}) {
  const cov = p.coverageGate;
  return (
    <div className="rounded-2xl border border-border/60 bg-card/60 p-5 transition-colors hover:border-border">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-[15px] font-medium leading-snug text-foreground">{p.title}</h2>
        <div className="flex shrink-0 items-center gap-2">
          <CategoryChip category={category} />
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
              p.testsOk ? "bg-emerald-500/10 text-emerald-500" : "bg-amber-500/10 text-amber-500",
            )}
          >
            {p.testsOk ? <CheckCircle2 className="size-3" /> : <AlertTriangle className="size-3" />}
            {p.testsOk ? "tests pass" : "check tests"}
          </span>
        </div>
      </div>

      {p.intent && p.intent !== p.title && (
        <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-[13px] leading-5 text-muted-foreground">
          {p.intent}
        </p>
      )}

      <FileChips files={p.files} />

      {(p.diffSummary || cov.score !== null) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted-foreground">
          {p.diffSummary && <span className="font-mono">{p.diffSummary}</span>}
          {cov.score !== null && (
            <span title={`${cov.covered}/${cov.measurable} changed lines covered`}>
              coverage {(cov.score * 100).toFixed(0)}%
            </span>
          )}
        </div>
      )}

      <ReviewVerdict review={p.review} reviewing={reviewing} onReview={onReview} />

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <PriorityDot priority={p.priority} />
          <span className="text-[12px] text-muted-foreground">{timeAgo(p.createdAt)}</span>
          {p.prUrl && (
            <a
              href={p.prUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[12px] text-foreground/80 hover:text-foreground"
            >
              <ExternalLink className="size-3" />
              PR
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
            <span className="hidden text-[11.5px] text-muted-foreground sm:inline">Deploy &amp; restart JARVIS?</span>
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" onClick={onConfirm} className="gap-1.5">
              <ShieldCheck className="size-3.5" />
              Confirm deploy
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" className="text-muted-foreground" disabled={rejecting} onClick={onReject}>
              {rejecting ? <Loader2 className="size-3.5 animate-spin" /> : "Reject"}
            </Button>
            <Button size="sm" variant="outline" onClick={onAskConfirm}>
              Approve &amp; deploy
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function ReviewVerdict({
  review,
  reviewing,
  onReview,
}: {
  review?: Review | null;
  reviewing: boolean;
  onReview: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (!review) {
    return (
      <button
        type="button"
        onClick={onReview}
        disabled={reviewing}
        className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-background/40 px-2 py-1 text-[11.5px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
      >
        {reviewing ? <Loader2 className="size-3 animate-spin" /> : <Radar className="size-3" />}
        {reviewing ? "Reviewing…" : "Run review council"}
      </button>
    );
  }
  const rec = review.overall.recommendation;
  const recTone =
    rec === "approve"
      ? "bg-emerald-500/10 text-emerald-500"
      : rec === "reject"
        ? "bg-rose-500/10 text-rose-500"
        : rec === "caution"
          ? "bg-amber-500/10 text-amber-500"
          : "bg-muted/60 text-muted-foreground";
  const recLabel =
    rec === "approve"
      ? "looks safe"
      : rec === "reject"
        ? "do not deploy"
        : rec === "caution"
          ? "needs a look"
          : "inconclusive";
  const hasFindings = LENS_ORDER.some((l) => review.lenses[l]?.findings?.length);
  return (
    <div className="mt-3 rounded-lg border border-border/50 bg-background/40 p-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Radar className="size-3.5 text-muted-foreground" />
          <span className="text-[11.5px] font-medium text-foreground">Review council</span>
          <span className={cn("rounded-full px-1.5 py-0.5 text-[10.5px] font-medium", recTone)}>{recLabel}</span>
        </div>
        <div className="flex items-center gap-2.5">
          {LENS_ORDER.map((l) => {
            const lens = review.lenses[l];
            const v = lens?.verdict ?? "skipped";
            return (
              <span
                key={l}
                className="inline-flex items-center gap-1 text-[10.5px] text-muted-foreground"
                title={`${l}: ${v}${lens?.model ? ` (${lens.model})` : ""}`}
              >
                <span className={cn("size-1.5 rounded-full", verdictDot(v))} />
                {l.slice(0, 4)}
              </span>
            );
          })}
          {hasFindings && (
            <button
              type="button"
              onClick={() => setOpen(!open)}
              className="text-[10.5px] text-muted-foreground hover:text-foreground"
            >
              {open ? "hide" : "details"}
            </button>
          )}
        </div>
      </div>
      {open && hasFindings && (
        <div className="mt-2 space-y-1.5 border-t border-border/40 pt-2">
          {LENS_ORDER.map((l) => {
            const lens = review.lenses[l];
            if (!lens?.findings?.length) return null;
            return (
              <div key={l}>
                <p className={cn("text-[10px] font-medium uppercase tracking-wide", verdictText(lens.verdict))}>
                  {l}
                  {lens.model && (
                    <span className="ml-1.5 font-normal normal-case text-muted-foreground/80">
                      · {lens.model.split(":").pop()}
                    </span>
                  )}
                </p>
                <ul className="mt-0.5 space-y-0.5">
                  {lens.findings.map((f, i) => (
                    <li key={i} className="text-[11.5px] leading-4 text-muted-foreground">
                      · {f}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function QueuedRow({
  q,
  category,
  building,
  dismissing,
  onProcess,
  onDismiss,
}: {
  q: Activity;
  category: Category;
  building: boolean;
  dismissing: boolean;
  onProcess: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/40 px-4 py-2.5">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate text-[13px] text-foreground">{q.title}</p>
          <CategoryChip category={category} />
        </div>
        {q.detail && q.detail !== q.title && (
          <p className="truncate text-[12px] text-muted-foreground">{q.detail}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <Button size="sm" variant="ghost" className="h-7 px-2 text-[12px] text-muted-foreground" disabled={dismissing} onClick={onDismiss}>
          {dismissing ? <Loader2 className="size-3.5 animate-spin" /> : "Dismiss"}
        </Button>
        <Button size="sm" variant="outline" className="h-7 gap-1.5 px-2 text-[12px]" disabled={building} onClick={onProcess}>
          {building ? <Loader2 className="size-3.5 animate-spin" /> : <Hammer className="size-3.5" />}
          Build
        </Button>
      </div>
    </div>
  );
}

function DeployedRow({
  d,
  confirming,
  reverting,
  onAskRevert,
  onCancel,
  onRevert,
}: {
  d: Deployed;
  confirming: boolean;
  reverting: boolean;
  onAskRevert: () => void;
  onCancel: () => void;
  onRevert: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-xl border border-border/60 bg-card/40 px-4 py-3">
      <div className="min-w-0">
        <p className="truncate text-[13px] text-foreground">{d.title}</p>
        <p className="mt-0.5 flex items-center gap-2 text-[11.5px] text-muted-foreground">
          <CheckCircle2 className="size-3 text-emerald-500" />
          deployed {timeAgo(d.createdAt)}
          {d.mergeSha && <span className="font-mono">· {d.mergeSha.slice(0, 8)}</span>}
        </p>
      </div>
      {d.canRevert &&
        (reverting ? (
          <Button size="sm" disabled variant="ghost" className="h-7 gap-1.5 px-2 text-[12px]">
            <Loader2 className="size-3.5 animate-spin" />
            Rolling back…
          </Button>
        ) : confirming ? (
          <div className="flex shrink-0 items-center gap-1.5">
            <Button size="sm" variant="ghost" className="h-7 px-2 text-[12px]" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" variant="outline" className="h-7 gap-1.5 px-2 text-[12px] text-amber-600" onClick={onRevert}>
              <RotateCcw className="size-3.5" />
              Confirm
            </Button>
          </div>
        ) : (
          <Button size="sm" variant="ghost" className="h-7 gap-1.5 px-2 text-[12px] text-muted-foreground" onClick={onAskRevert}>
            <RotateCcw className="size-3.5" />
            Roll back
          </Button>
        ))}
    </div>
  );
}

function FailedRow({ f }: { f: Proposal }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-3">
      <div className="flex items-start gap-2">
        <XCircle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
        <div className="min-w-0">
          <p className="truncate text-[13px] text-foreground">{f.title}</p>
          {f.rejectionReason && (
            <p className="mt-0.5 font-mono text-[11.5px] text-amber-600/90">{f.rejectionReason}</p>
          )}
          <p className="mt-0.5 text-[11.5px] text-muted-foreground">{timeAgo(f.createdAt)}</p>
        </div>
      </div>
    </div>
  );
}

function ActivityRow({ a }: { a: Activity }) {
  return (
    <div className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[12.5px]">
      <ActivityIcon className="size-3 shrink-0 text-muted-foreground/70" />
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{a.status}</span>
      <span className="min-w-0 flex-1 truncate text-foreground/90">{a.title}</span>
      {a.detail && <span className="hidden min-w-0 max-w-[40%] truncate text-muted-foreground sm:block">{a.detail}</span>}
      <span className="shrink-0 text-[11px] text-muted-foreground">{timeAgo(a.createdAt)}</span>
    </div>
  );
}

function FitnessPanel({ fitness }: { fitness: Fitness }) {
  const trendTone =
    fitness.trend === "up" ? "text-emerald-500" : fitness.trend === "down" ? "text-amber-500" : "text-muted-foreground";
  const axes = Object.entries(fitness.perAxis ?? {}).sort((a, b) => a[1] - b[1]);
  return (
    <div className="rounded-2xl border border-border/60 bg-card/60 p-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Radar className="size-4 text-primary" />
          <h3 className="text-[14px] font-medium">Fitness</h3>
        </div>
        {fitness.latest !== null && (
          <div className="flex items-baseline gap-2">
            <span className="font-serif text-[22px] font-semibold tabular-nums">{fitness.latest.toFixed(2)}</span>
            {fitness.trend && <span className={cn("text-[12px]", trendTone)}>{fitness.trend}</span>}
          </div>
        )}
      </div>
      {fitness.error ? (
        <p className="mt-3 text-[12.5px] text-muted-foreground">No fitness readings yet ({fitness.error}).</p>
      ) : fitness.count === 0 ? (
        <p className="mt-3 text-[12.5px] text-muted-foreground">No soak-fitness readings recorded yet.</p>
      ) : (
        <>
          <div className="mt-3">
            <Sparkline points={fitness.points} width={520} height={56} className="w-full" />
          </div>
          {axes.length > 0 && (
            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
              {axes.map(([axis, score]) => (
                <div key={axis} className="min-w-0">
                  <div className="flex items-center justify-between gap-2 text-[11.5px]">
                    <span className="truncate text-muted-foreground">{axis.replace(/_/g, " ")}</span>
                    <span className="tabular-nums text-foreground">{score.toFixed(2)}</span>
                  </div>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted/50">
                    <div
                      className={cn("h-full rounded-full", score < 0.6 ? "bg-amber-500" : "bg-emerald-500")}
                      style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
          {fitness.weakAxis && (
            <p className="mt-3 text-[12px] text-muted-foreground">
              Weakest axis:{" "}
              <span className="text-foreground">{fitness.weakAxis.axis.replace(/_/g, " ")}</span> at{" "}
              {fitness.weakAxis.score.toFixed(2)} — the next improvement targets this.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function GraduationPanel({
  autonomy,
  graduation,
}: {
  autonomy: { currentLabel: string; targetLabel: string };
  graduation: Graduation;
}) {
  return (
    <div className="rounded-2xl border border-border/60 bg-card/60 p-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-primary" />
          <h3 className="text-[14px] font-medium">Autonomy</h3>
        </div>
        <span className="text-[12px] tabular-nums text-muted-foreground">
          {graduation.metCount}/{graduation.total} met
        </span>
      </div>
      <p className="mt-1.5 text-[12.5px] text-muted-foreground">
        <span className="text-foreground">{autonomy.currentLabel}</span> → {autonomy.targetLabel}. A human
        approves every deploy until these are consistently met.
      </p>
      <div className="mt-3 space-y-2">
        {graduation.criteria.map((c) => (
          <div key={c.id} className="flex items-start gap-2.5">
            {c.met ? (
              <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" />
            ) : (
              <span className="mt-0.5 size-3.5 shrink-0 rounded-full border border-muted-foreground/40" />
            )}
            <div className="min-w-0">
              <p className="text-[12.5px] text-foreground">{c.label}</p>
              <p className="text-[11.5px] text-muted-foreground">{c.detail}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CriteriaGrid({ criteria }: { criteria: Criterion[] }) {
  const pillars = criteria.filter((c) => c.group === "pillar");
  const system = criteria.filter((c) => c.group === "system");
  return (
    <div className="rounded-2xl border border-border/60 bg-card/60 p-5">
      <h3 className="text-[14px] font-medium">What every change must satisfy</h3>
      <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
        {pillars.map((c) => (
          <div key={c.id} className="rounded-lg border border-border/50 bg-background/40 p-3">
            <p className="text-[12.5px] font-medium text-foreground">{c.label}</p>
            <p className="mt-0.5 text-[11.5px] leading-4 text-muted-foreground">{c.description}</p>
          </div>
        ))}
      </div>
      {system.length > 0 && (
        <div className="mt-3 space-y-1.5 border-t border-border/50 pt-3">
          {system.map((c) => (
            <p key={c.id} className="text-[11.5px] text-muted-foreground">
              <span className="font-medium text-foreground">{c.label}.</span> {c.description}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function AssessmentPanel({ assessment }: { assessment: SelfAssessment }) {
  if (!assessment) {
    return (
      <div className="rounded-2xl border border-dashed border-border/60 bg-card/30 p-5 text-center">
        <Brain className="mx-auto size-5 text-muted-foreground" />
        <p className="mt-2 text-[13px] text-foreground">No self-assessment yet</p>
        <p className="mt-0.5 text-[12px] text-muted-foreground">
          Run <span className="font-medium">Introspect</span> to have JARVIS name its own flaws and the
          improvements worth trying next.
        </p>
      </div>
    );
  }
  const flaws = Array.isArray(assessment.flaws) ? assessment.flaws : [];
  const improvements = Array.isArray(assessment.improvements) ? assessment.improvements : [];
  return (
    <div className="rounded-2xl border border-border/60 bg-card/60 p-5">
      <div className="flex items-center gap-2">
        <Brain className="size-4 text-primary" />
        <h3 className="text-[14px] font-medium">Self-assessment</h3>
      </div>
      {assessment.summary && (
        <p className="mt-2 whitespace-pre-wrap text-[12.5px] leading-5 text-muted-foreground">{assessment.summary}</p>
      )}
      {flaws.length > 0 && (
        <div className="mt-3">
          <SectionLabel>Flaws it sees</SectionLabel>
          <ul className="space-y-1">
            {flaws.map((x, i) => (
              <li key={i} className="flex gap-2 text-[12.5px] text-foreground/90">
                <span className="text-amber-500">·</span>
                {x}
              </li>
            ))}
          </ul>
        </div>
      )}
      {improvements.length > 0 && (
        <div className="mt-3">
          <SectionLabel>Improvements to try</SectionLabel>
          <ul className="space-y-1">
            {improvements.map((x, i) => (
              <li key={i} className="flex gap-2 text-[12.5px] text-foreground/90">
                <span className="text-emerald-500">·</span>
                {x}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function BuildModelPicker({
  current,
  busy,
  onChange,
}: {
  current: string;
  busy: boolean;
  onChange: (model: string) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/40 px-4 py-3">
      <div>
        <p className="text-[12.5px] font-medium text-foreground">Build model</p>
        <p className="text-[11.5px] text-muted-foreground">
          The model autonomous builds run on. Empty inherits the global model.
        </p>
      </div>
      <select
        value={current}
        disabled={busy}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-border/60 bg-background px-2.5 py-1.5 text-[12.5px] text-foreground outline-none focus:border-border disabled:opacity-50"
      >
        {BUILD_MODELS.map((m) => (
          <option key={m.value} value={m.value}>
            {m.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border/60 px-6 py-16 text-center">
      <div className="flex size-11 items-center justify-center rounded-full bg-muted/40">
        <GitPullRequest className="size-5 text-muted-foreground" />
      </div>
      <p className="mt-4 text-[14px] font-medium text-foreground">Nothing to review</p>
      <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted-foreground">
        JARVIS proposes improvements to his own code as he runs. When he does, they&apos;ll appear here
        for your approval — each one tested and safe to roll back. Run a cycle to kick one off now.
      </p>
    </div>
  );
}
