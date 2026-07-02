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
  Copy,
  ExternalLink,
  FileCode2,
  Flag,
  GitPullRequest,
  Hammer,
  Loader2,
  Radar,
  RotateCcw,
  ShieldCheck,
  TrendingUp,
  Wallet,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { cn, formatRelativeTime } from "@/lib/utils";
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
  priority?: string;
};

type Criterion = { id: string; group: string; label: string; description: string };

type AxisMeta = { score: number; std: number; flat: boolean };
type Fitness = {
  points: { ts: string; composite: number; passed: boolean }[];
  latest: number | null;
  latestAt: string | null;
  count: number;
  trend: "up" | "down" | "flat" | null;
  perAxis: Record<string, number>;
  perAxisMeta: Record<string, AxisMeta>;
  weakAxis: { axis: string; score: number } | null;
  error?: string;
};

type Cost = { spentToday: number; dailyUsd: number; remaining: number };
type LoopStatus = {
  mode: "auto" | "manual";
  paused: boolean;
  state: string; // paused|manual|deploying|building|waiting|budget|cooldown|ready
  reason: string;
  idleS: number | null;
  cooldownLeftS: number;
  budgetSpent: number;
  budgetCap: number;
  lastTickAgeS: number | null;
};
type FailureDigest = {
  total: number;
  byClass: { label: string; count: number }[];
  repeatedPaths: { path: string; count: number }[];
};

type Graduation = {
  metCount: number;
  total: number;
  criteria: { id: string; label: string; met: boolean; detail: string }[];
};

type SelfAssessment = {
  summary?: string;
  flaws?: unknown[]; // strings OR {area, detail}
  improvements?: unknown[]; // strings OR {title, rationale, target_axis}
  generatedAt?: string;
  [k: string]: unknown;
} | null;

// Flaws/improvements come back as either strings or structured objects
// ({area, detail} / {title, rationale}); render them safely either way.
function assessmentText(x: unknown): { head: string; body: string } {
  if (typeof x === "string") return { head: "", body: x };
  if (x && typeof x === "object") {
    const o = x as Record<string, unknown>;
    const head = String(o.area ?? o.title ?? o.axis ?? "");
    const body = String(o.detail ?? o.rationale ?? o.description ?? o.text ?? "");
    if (head || body) return { head, body: body || head };
    return { head: "", body: JSON.stringify(o) };
  }
  return { head: "", body: String(x ?? "") };
}

type EvolutionData = {
  proposals: Proposal[];
  failed: Proposal[];
  failureDigest: FailureDigest;
  needsHuman: Activity[];
  cost: Cost;
  loopStatus: LoopStatus;
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
  reviewAll?: { running: boolean; total: number; done: number } | null;
  status: {
    pending: number;
    queued: number;
    needsHuman: number;
    failedCount: number;
    deployed: number;
    failed: number;
    builds: { today: number; cap: number; remaining: number };
    cost: Cost;
    building: number;
    buildingDetail: InFlightBuild[];
    deployInFlight: boolean;
    rollbacks: number;
  };
};

type InFlightBuild = { id: string; intent: string; kind: string; elapsedSec: number };

const BUILD_MODELS = [
  { value: "", label: "Global model" },
  { value: "deepseek-v4-pro", label: "DeepSeek v4 Pro" },
  { value: "claude-opus-4-8", label: "Claude Opus 4.8" },
  { value: "kimi-k2.7-code", label: "Kimi K2.7 Code" },
];

// Null-safe adapter over the shared formatRelativeTime (lib/utils) — was a 6th
// hand-rolled copy of the same min/hour/day math; now delegates.
function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "" : formatRelativeTime(t);
}

const stripPrefix = (f: string) => f.replace(/^src\/voice-agent\//, "");

export default function EvolutionPage() {
  const [data, setData] = useState<EvolutionData | null>(null);
  const [tab, setTab] = useState("review");
  const [catFilter, setCatFilter] = useState<Category | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // "approve:<id>" / "cycle" / ...
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const didDefaultRef = useRef(false);
  // Switch tabs AND reset the category filter — each view starts unfiltered.
  const goTab = useCallback((t: string) => {
    setTab(t);
    setCatFilter(null);
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/evolution", { cache: "no-store" });
      if (res.status === 401) {
        // Web session expired (e.g. the 8h absolute cap) — re-auth instead of
        // rendering the {error:"auth required"} body as data (which crashed on
        // data.status). A full nav lets proxy.ts gate us to the login page.
        window.location.href = "/login";
        return;
      }
      if (!res.ok) return; // transient error — keep prior state, never crash
      const json = (await res.json()) as Partial<EvolutionData>;
      // Only accept a well-formed payload (guards against a partial/error body).
      if (json && typeof json.status === "object") setData(json as EvolutionData);
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

  // Smart default: on first load, open the tab that actually has something for
  // the user — proposals to decide, else a live build, else the queue — instead
  // of always landing on a Review tab that may be empty.
  useEffect(() => {
    if (!data || didDefaultRef.current) return;
    didDefaultRef.current = true;
    if (data.status.pending > 0) setTab("review");
    else if (data.status.needsHuman > 0) setTab("needs");
    else if (data.status.building > 0) setTab("building");
    else if (data.queued.length > 0) setTab("queue");
  }, [data]);

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
  // Kick off the batch in the background (detached server-side) and return fast;
  // the 6s poll above + the .review-all-status.json the run writes make each
  // proposal's verdict + the progress count update INCREMENTALLY.
  const reviewAll = async () => {
    setBusy("review-all");
    try {
      const res = await fetch("/api/evolution/review-all", { method: "POST" });
      const r = (await res.json().catch(() => ({}))) as { ok?: boolean; detail?: string };
      if (res.ok && r.ok !== false) toast.success("Reviewing all pending — verdicts update as each finishes.");
      else toast.error(r.detail || "Couldn't start the batch review.");
    } catch {
      toast.error("Couldn't reach the server.");
    } finally {
      setBusy(null);
      void load();
      setTimeout(() => void load(), 1500); // pick up the status file once the run wrote it
    }
  };
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
  // ONE control for the loop's run state. "Off" is a hard pause; "Manual" and
  // "Auto" are the build mode (unpausing first if needed). Replaces the old
  // pause button + resume button + manual/auto toggle — three controls for what
  // is really one three-state choice.
  const setLoopMode = async (m: "off" | "manual" | "auto") => {
    if (m === "off") {
      void setPaused(true);
      return;
    }
    if (data?.paused) await setPaused(false);
    void setMode(m);
  };
  const setBuildModel = (model: string) =>
    act("buildModel", "/api/evolution/build-model", { model }, "Build model updated.");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-10">
        {/* Header — title only. The run-state control lives in the status card
            below, next to the state it changes (one control, one place). */}
        <div className="flex items-center gap-x-3">
          <GitPullRequest className="size-5 text-primary" />
          <div className="min-w-0">
            <h1 className="font-serif text-[22px] font-semibold leading-none tracking-tight">Evolution</h1>
            <p className="mt-1 text-[12px] uppercase tracking-wider text-muted-foreground">
              Self-modification console
            </p>
          </div>
        </div>

        {/* System status — one card: run state + the single Off/Manual/Auto
            control (top), health outcomes (bottom). */}
        {data?.loopStatus && (
          <SystemStatusCard
            data={data}
            busy={busy === "mode" || busy === "pause"}
            onSetMode={setLoopMode}
            onHealth={() => goTab("health")}
          />
        )}

        {/* Stage tabs — the work stages double as navigation. Light tab bar
            (not a second metrics row): small count badges, active underline. */}
        {data && <StageTabs data={data} tab={tab} onTab={goTab} />}

        {/* Actions — the three things you DO to the loop. */}
        {data && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" className="gap-1.5" disabled={busy === "cycle" || data.paused} onClick={cycle}>
              {busy === "cycle" ? <Loader2 className="size-3.5 animate-spin" /> : <Hammer className="size-3.5" />}
              Run cycle
            </Button>
            <Button size="sm" variant="outline" className="gap-1.5" disabled={busy === "introspect"} onClick={introspect}>
              {busy === "introspect" ? <Loader2 className="size-3.5 animate-spin" /> : <Brain className="size-3.5" />}
              Introspect
            </Button>
            <BuildModelPicker current={data.buildModel} busy={busy === "buildModel"} onChange={setBuildModel} />
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
            <Tabs value={tab} onValueChange={goTab}>

              {/* REVIEW: pending PROPOSALS only — the decisions awaiting you.
                  Queued intents live in their own tab so a destructive
                  "Approve & deploy" is never adjacent to a "Build" (mixing them
                  is dangerous — flagged by the user). */}
              <TabsContent value="review" className="mt-5 space-y-3">
                {data.proposals.length === 0 ? (
                  <EmptyState />
                ) : (
                  (() => {
                    const propCat = (p: Proposal) => categorize(p.files, p.intent);
                    const counts = Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<Category, number>;
                    data.proposals.forEach((p) => {
                      counts[propCat(p)] += 1;
                    });
                    const proposals = catFilter
                      ? data.proposals.filter((p) => propCat(p) === catFilter)
                      : data.proposals;
                    return (
                      <>
                        <CategoryFilterBar counts={counts} active={catFilter} onPick={setCatFilter} />
                        <div className="flex justify-end">
                          {(() => {
                            const ra = data.reviewAll;
                            const running = !!ra?.running || busy === "review-all";
                            return (
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={running}
                                onClick={reviewAll}
                                title="Re-run the 3-lens review council on every pending proposal in parallel — verdicts update as each finishes"
                              >
                                {running ? (
                                  <Loader2 className="size-3.5 animate-spin" />
                                ) : (
                                  <Radar className="size-3.5" />
                                )}
                                {ra?.running
                                  ? `Reviewing… (${ra.done}/${ra.total})`
                                  : busy === "review-all"
                                    ? "Starting…"
                                    : `Review all pending (${data.proposals.length})`}
                              </Button>
                            );
                          })()}
                        </div>
                        {catFilter && proposals.length === 0 && (
                          <p className="px-1 py-8 text-center text-[13px] text-muted-foreground">
                            No proposals to review under {catFilter}.
                          </p>
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
                      </>
                    );
                  })()
                )}
              </TabsContent>

              {/* QUEUE: intents waiting to build (Build → a reviewable proposal,
                  or History → Failed). Deliberately separate from Review so a
                  Build click can never sit next to an Approve & deploy. */}
              <TabsContent value="queue" className="mt-5 space-y-3">
                {data.queued.length === 0 ? (
                  <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-10 text-center">
                    <p className="text-[13.5px] text-muted-foreground">Nothing queued.</p>
                    <p className="mx-auto mt-1 max-w-sm text-[12.5px] leading-5 text-muted-foreground/80">
                      Intents JARVIS wants to attempt show here. Build one to turn it into a
                      reviewable proposal, or dismiss it.
                    </p>
                  </div>
                ) : (
                  (() => {
                    const queuedCat = (q: Activity) => categorize([], q.detail || q.title);
                    const counts = Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<Category, number>;
                    data.queued.forEach((q) => {
                      counts[queuedCat(q)] += 1;
                    });
                    const queued = catFilter
                      ? data.queued.filter((q) => queuedCat(q) === catFilter)
                      : data.queued;
                    return (
                      <>
                        <CategoryFilterBar counts={counts} active={catFilter} onPick={setCatFilter} />
                        {catFilter && queued.length === 0 && (
                          <p className="px-1 py-8 text-center text-[13px] text-muted-foreground">
                            Nothing queued under {catFilter}.
                          </p>
                        )}
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
                      </>
                    );
                  })()
                )}
              </TabsContent>

              {/* NEEDS YOU: intents the admission gate escalated to a human —
                  goals that target the protected self-modification loop itself,
                  which can never be auto-built. Informational + copy-to-act. */}
              <TabsContent value="needs" className="mt-5 space-y-3">
                {data.needsHuman.length === 0 ? (
                  <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-10 text-center">
                    <p className="text-[13.5px] text-muted-foreground">No escalations.</p>
                    <p className="mx-auto mt-1 max-w-sm text-[12.5px] leading-5 text-muted-foreground/80">
                      When JARVIS proposes a change to its own evolution loop — which is
                      protected and can&apos;t be auto-built — it&apos;s held here for you to
                      handle by hand instead of burning a build.
                    </p>
                  </div>
                ) : (
                  <>
                    <div className="flex items-start gap-3 rounded-xl border border-primary/20 bg-primary/[0.04] px-4 py-3">
                      <Flag className="mt-0.5 size-4 shrink-0 text-primary" />
                      <p className="text-[12.5px] leading-5 text-muted-foreground">
                        <span className="font-medium text-foreground">
                          {data.needsHuman.length} intent{data.needsHuman.length === 1 ? "" : "s"} need your hand.
                        </span>{" "}
                        These target JARVIS&apos;s own protected self-modification code, so the loop
                        won&apos;t build them autonomously. Implement one yourself, or leave it — it
                        won&apos;t re-queue.
                      </p>
                    </div>
                    <div className="space-y-2">
                      {data.needsHuman.map((n) => (
                        <NeedsHumanRow key={n.id} n={n} />
                      ))}
                    </div>
                  </>
                )}
              </TabsContent>

              {/* BUILDING: live in-flight builds — one row per running jarvis-automod-impl */}
              <TabsContent value="building" className="mt-5 space-y-3">
                {(data.status.buildingDetail ?? []).length === 0 ? (
                  <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-10 text-center">
                    <p className="text-[13.5px] text-muted-foreground">
                      No build is running right now.
                    </p>
                    <p className="mx-auto mt-1 max-w-sm text-[12.5px] leading-5 text-muted-foreground/80">
                      When a cycle or a queued intent builds, it shows here live — then moves to
                      Review if it passes the gate, or History → Failed if it doesn&apos;t.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {(data.status.buildingDetail ?? []).map((b) => (
                      <BuildingRow key={b.id} b={b} />
                    ))}
                  </div>
                )}
              </TabsContent>

              {/* HEALTH: fitness, criteria, graduation, self-assessment */}
              <TabsContent value="health" className="mt-5 space-y-5">
                <FitnessPanel fitness={data.fitness} />
                <GraduationPanel autonomy={data.autonomy} graduation={data.graduation} />
                <CriteriaGrid criteria={data.criteria} />
                <AssessmentPanel assessment={data.selfAssessment} />
                <div>
                  <SectionLabel>Recent activity</SectionLabel>
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
              </TabsContent>

              {/* DEPLOYED: changes that shipped — each revertible in one click */}
              <TabsContent value="deployed" className="mt-5 space-y-2">
                {data.deployed.length === 0 ? (
                  <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-10 text-center">
                    <p className="text-[13.5px] text-muted-foreground">Nothing deployed yet.</p>
                    <p className="mx-auto mt-1 max-w-sm text-[12.5px] leading-5 text-muted-foreground/80">
                      Approving a reviewed proposal deploys it here — and an external watchdog
                      auto-reverts it if JARVIS doesn&apos;t come back healthy.
                    </p>
                  </div>
                ) : (
                  data.deployed.map((d) => (
                    <DeployedRow
                      key={d.id}
                      d={d}
                      confirming={confirming === `revert:${d.id}`}
                      reverting={busy === `revert:${d.id}`}
                      onAskRevert={() => setConfirming(`revert:${d.id}`)}
                      onCancel={() => setConfirming(null)}
                      onRevert={() => revert(d.id)}
                    />
                  ))
                )}
              </TabsContent>

              {/* FAILED: builds that didn't pass the gate — triaged by class +
                  the paths repeatedly targeted across failures (turns a flat
                  list into where-to-look insight). */}
              <TabsContent value="failed" className="mt-5 space-y-3">
                {data.failed.length === 0 ? (
                  <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-10 text-center">
                    <p className="text-[13.5px] text-muted-foreground">No failed builds.</p>
                  </div>
                ) : (
                  <>
                    <FailureTriage digest={data.failureDigest} />
                    <div className="space-y-2">
                      {data.failed.map((f) => <FailedRow key={f.id} f={f} />)}
                    </div>
                  </>
                )}
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
  counts,
  active,
  onPick,
}: {
  counts: Record<string, number>;
  active: Category | null;
  onPick: (c: Category | null) => void;
}) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  // Always show all 7 categories so the taxonomy is visible; empty ones are dimmed.
  return (
    <div className="flex flex-wrap items-center gap-1.5 pb-1">
      <FilterPill label={total ? `All ${total}` : "All"} active={active === null} onClick={() => onPick(null)} />
      {CATEGORIES.map((c) => (
        <FilterPill
          key={c}
          label={`${c} ${counts[c] || 0}`}
          tone={CATEGORY_TONE[c]}
          active={active === c}
          dim={!counts[c]}
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
  dim,
  onClick,
}: {
  label: string;
  tone?: string;
  active: boolean;
  dim?: boolean;
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
        dim && !active && "opacity-45",
      )}
    >
      {tone && <span className={cn("size-1.5 rounded-full bg-current", tone)} />}
      {label}
    </button>
  );
}

// One segment of the pipeline strip. Equal-width, divided cells that read
// left-to-right as a flow. The Review stage gets an "attention" tint when a
// proposal is actually waiting on the human; Building pulses when live.
// One segment of the unified pipeline nav. It IS the tab bar now — clicking a
// stage selects its view (active = highlighted). A count stage shows its number;
// the Health stage shows an icon instead (no count).
// Work stages as a light tab bar (was a row of big-number tiles competing with
// the KPIs). Text label + small count badge + active underline — reads as
// navigation, not a second metrics band. Attention stages (review/needs) tint
// their badge; a live build pulses.
function StageTabs({
  data,
  tab,
  onTab,
}: {
  data: EvolutionData;
  tab: string;
  onTab: (t: string) => void;
}) {
  const s = data.status;
  const stages: {
    id: string; label: string; n?: number; attn?: boolean; live?: boolean; tone?: "emerald" | "amber";
  }[] = [
    { id: "review", label: "Review", n: s.pending, attn: s.pending > 0 },
    { id: "needs", label: "Needs you", n: s.needsHuman, attn: s.needsHuman > 0 },
    { id: "queue", label: "Queue", n: s.queued },
    { id: "building", label: "Building", n: s.building, live: s.building > 0 },
    { id: "deployed", label: "Deployed", n: s.deployed, tone: "emerald" },
    { id: "failed", label: "Failed", n: s.failed, tone: "amber" },
    { id: "health", label: "Health" },
  ];
  return (
    <div className="mt-5 flex flex-wrap items-center gap-x-1 gap-y-1 border-b border-border/60">
      {stages.map((st) => {
        const active = tab === st.id;
        return (
          <button
            key={st.id}
            type="button"
            onClick={() => onTab(st.id)}
            aria-pressed={active}
            className={cn(
              "relative flex items-center gap-1.5 px-3 py-2 text-[13px] transition-colors",
              active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {st.live && <span className="size-1.5 animate-pulse rounded-full bg-primary" />}
            <span className={cn(active && "font-medium")}>{st.label}</span>
            {st.n !== undefined && (
              <span
                className={cn(
                  // Uniform pill for every count — same shape/min-width so the
                  // strip reads even; tone changes text color only.
                  "inline-flex min-w-5 items-center justify-center rounded-full px-1.5 py-px text-[11px] tabular-nums",
                  st.attn
                    ? "bg-primary/15 text-primary"
                    : st.tone === "emerald" && st.n > 0
                      ? "bg-emerald-500/15 text-emerald-500"
                      : st.tone === "amber" && st.n > 0
                        ? "bg-amber-500/15 text-amber-500"
                        : "bg-muted/50 text-muted-foreground",
                )}
              >
                {st.n}
              </span>
            )}
            {active && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />}
          </button>
        );
      })}
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
        <h2 className="line-clamp-2 text-[15px] font-medium leading-snug text-foreground">{p.intent || p.title}</h2>
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

      <FileChips files={p.files} />

      {(p.diffSummary || cov.score !== null) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted-foreground">
          {p.diffSummary && <span className="font-mono">{p.diffSummary}</span>}
          {cov.score !== null && (
            <span title={`${cov.covered}/${cov.measurable} changed lines covered`}>
              coverage {Math.min(100, cov.score * 100).toFixed(0)}%
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
        title="The 3-lens council usually runs automatically when a build passes the gate — run it manually here if it didn't, or to refresh the verdict."
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
          <span
            className="text-[11.5px] font-medium text-foreground"
            title="The 3-lens council (correctness · security · regression) ran automatically when this build passed the gate. Re-run it anytime."
          >
            Review council
          </span>
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
          <button
            type="button"
            onClick={onReview}
            disabled={reviewing}
            title="Re-run the council on this diff"
            className="text-[10.5px] text-muted-foreground hover:text-foreground disabled:opacity-60"
          >
            {reviewing ? "re-running…" : "re-run"}
          </button>
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
        <div className="flex items-center gap-2">
          <p className="truncate text-[13px] text-foreground">{d.title}</p>
          <CategoryChip category={categorize(d.files, d.intent)} />
        </div>
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
          <div className="flex items-center gap-2">
            <p className="truncate text-[13px] text-foreground">{f.title}</p>
            <CategoryChip category={categorize(f.files, f.intent)} />
          </div>
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

// Loop status strip — is the cycle alive, and if idle, WHY. The colored dot +
// plain-language reason kill the "auto-but-quiet looks broken" ambiguity. Offers
// the one-click unblock when the loop is stopped (resume / go auto).
const LOOP_TONE: Record<string, { dot: string; text: string }> = {
  building: { dot: "bg-primary animate-pulse", text: "text-primary" },
  deploying: { dot: "bg-primary animate-pulse", text: "text-primary" },
  ready: { dot: "bg-emerald-500", text: "text-emerald-500" },
  waiting: { dot: "bg-emerald-500", text: "text-muted-foreground" },
  cooldown: { dot: "bg-amber-500", text: "text-muted-foreground" },
  budget: { dot: "bg-amber-500", text: "text-amber-500" },
  manual: { dot: "bg-muted-foreground/50", text: "text-muted-foreground" },
  paused: { dot: "bg-rose-500", text: "text-rose-500" },
  unknown: { dot: "bg-muted-foreground/40", text: "text-muted-foreground" },
};

function loopHeadline(s: LoopStatus): string {
  if (s.state === "paused") return "Loop paused";
  if (s.state === "manual") return "Manual — not building";
  if (s.state === "building") return "Building now";
  if (s.state === "deploying") return "Deploying";
  if (s.state === "waiting") return "Auto — waiting for quiet";
  if (s.state === "cooldown") return `Auto — cooldown ${Math.round(s.cooldownLeftS / 60)}m`;
  if (s.state === "budget") return "Auto — budget spent";
  if (s.state === "ready") return "Auto — ready to build";
  return "Loop status unknown";
}

// System status — ONE card that answers "is the loop alive + healthy?". Top:
// loop state (dot + headline + why) with the unblock action when stopped.
// Bottom: three health stats (fitness / budget / deploy) as hairline-divided
// cells. Replaces the old separate loop strip AND KPI band (removed a whole
// chrome band + the mode-shown-twice redundancy).
// The single run-state control: Off (hard pause) / Manual (propose only) /
// Auto (build). Replaces the old pause button + resume button + manual/auto
// toggle. "Off" wins visually when paused, regardless of the underlying mode.
const LOOP_MODES: { id: "off" | "manual" | "auto"; label: string; hint: string }[] = [
  { id: "off", label: "Off", hint: "Paused — nothing runs" },
  { id: "manual", label: "Manual", hint: "Detect + queue proposals, but don't build" },
  { id: "auto", label: "Auto", hint: "Continuously build proposals (you still approve deploys)" },
];

function LoopModeControl({
  current,
  busy,
  onSet,
}: {
  current: "off" | "manual" | "auto";
  busy: boolean;
  onSet: (m: "off" | "manual" | "auto") => void;
}) {
  return (
    <div className="flex items-center rounded-full border border-border/60 bg-background/50 p-0.5 text-[12px]">
      {LOOP_MODES.map((m) => (
        <button
          key={m.id}
          type="button"
          disabled={busy}
          title={m.hint}
          onClick={() => onSet(m.id)}
          className={cn(
            "rounded-full px-2.5 py-1 transition-colors disabled:opacity-60",
            current === m.id
              ? m.id === "off"
                ? "bg-rose-500/15 font-medium text-rose-500"
                : "bg-primary/15 font-medium text-primary"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

// System status — ONE card that answers "is the loop alive + healthy?". Top:
// loop state (dot + headline + why) + the single Off/Manual/Auto control.
// Bottom: three health stats (fitness / budget / deploy) as hairline-divided
// cells. Replaces the old separate loop strip AND KPI band.
function SystemStatusCard({
  data,
  busy,
  onSetMode,
  onHealth,
}: {
  data: EvolutionData;
  busy: boolean;
  onSetMode: (m: "off" | "manual" | "auto") => void;
  onHealth: () => void;
}) {
  const s = data.loopStatus;
  const f = data.fitness;
  const cost = data.cost ?? { spentToday: 0, dailyUsd: 6, remaining: 6 };
  const spentPct = cost.dailyUsd > 0 ? Math.min(100, (cost.spentToday / cost.dailyUsd) * 100) : 0;
  const rollbacks = data.status.rollbacks;
  const tone = LOOP_TONE[s.state] ?? LOOP_TONE.unknown;
  const current: "off" | "manual" | "auto" = s.paused ? "off" : s.mode === "auto" ? "auto" : "manual";
  const trendTone =
    f.trend === "up" ? "text-emerald-500" : f.trend === "down" ? "text-amber-500" : "text-muted-foreground";
  const trendLabel = f.trend === "up" ? "rising" : f.trend === "down" ? "declining" : f.trend === "flat" ? "steady" : "";
  return (
    <div className="mt-5 overflow-hidden rounded-2xl border border-border/60 bg-card/50">
      {/* Loop state + the single run-state control */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3.5">
        <span className={cn("size-2 shrink-0 rounded-full", tone.dot)} />
        <span className={cn("text-[14px] font-medium", tone.text)}>{loopHeadline(s)}</span>
        <span className="text-[12.5px] text-muted-foreground">— {s.reason}</span>
        <div className="ml-auto flex items-center gap-3">
          {s.lastTickAgeS !== null && (
            <span className="text-[11px] tabular-nums text-muted-foreground/70" title="Time since the in-process loop last ticked">
              ticked {s.lastTickAgeS < 90 ? "just now" : `${Math.round(s.lastTickAgeS / 60)}m ago`}
            </span>
          )}
          <LoopModeControl current={current} busy={busy} onSet={onSetMode} />
        </div>
      </div>
      {/* Health outcomes */}
      <div className="grid grid-cols-3 divide-x divide-border/50 border-t border-border/50">
        <StatCell
          label="Fitness"
          icon={TrendingUp}
          value={f.latest !== null ? f.latest.toFixed(2) : "—"}
          valueTone={f.latest !== null && f.latest >= 0.7 ? "text-emerald-500" : f.latest !== null && f.latest < 0.6 ? "text-amber-500" : "text-foreground"}
          sub={trendLabel ? <span className={trendTone}>{trendLabel}</span> : "no readings"}
          spark={f.points.length > 1 ? f.points : undefined}
          onClick={onHealth}
        />
        <StatCell
          label="Budget today"
          icon={Wallet}
          value={`$${cost.spentToday.toFixed(2)}`}
          sub={`of $${cost.dailyUsd.toFixed(0)}`}
          meterPct={spentPct}
          onClick={onHealth}
        />
        <StatCell
          label="Deploy"
          icon={ShieldCheck}
          value={rollbacks === 0 ? "OK" : String(rollbacks)}
          valueTone={rollbacks === 0 ? "text-emerald-500" : "text-amber-500"}
          sub={rollbacks === 0 ? "auto-rollback on" : `rollback${rollbacks === 1 ? "" : "s"}`}
          onClick={onHealth}
        />
      </div>
    </div>
  );
}

function StatCell({
  label,
  value,
  sub,
  icon: Icon,
  valueTone = "text-foreground",
  meterPct,
  spark,
  onClick,
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  icon: React.ComponentType<{ className?: string }>;
  valueTone?: string;
  meterPct?: number;
  spark?: { ts: string; composite: number; passed: boolean }[];
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex flex-col gap-1.5 px-5 py-3 text-left transition-colors hover:bg-accent/40"
    >
      <span className="flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-wider text-muted-foreground">
        <Icon className="size-3" />
        {label}
      </span>
      <div className="flex items-baseline gap-1.5">
        <span className={cn("font-serif text-[20px] font-semibold leading-none tabular-nums", valueTone)}>{value}</span>
        {sub && <span className="text-[11px] text-muted-foreground">{sub}</span>}
      </div>
      {meterPct !== undefined && (
        <div className="h-1 overflow-hidden rounded-full bg-muted/50">
          <div
            className={cn("h-full rounded-full", meterPct >= 100 ? "bg-rose-500" : meterPct >= 80 ? "bg-amber-500" : "bg-emerald-500")}
            style={{ width: `${Math.max(2, meterPct)}%` }}
          />
        </div>
      )}
      {spark && (
        <div className="-mb-0.5">
          <Sparkline points={spark} width={80} height={18} className="w-full opacity-70" />
        </div>
      )}
    </button>
  );
}

function NeedsHumanRow({ n }: { n: Activity }) {
  const [copied, setCopied] = useState(false);
  const cat = categorize([], n.title);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(n.title);
      setCopied(true);
      toast.success("Intent copied");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy to clipboard.");
    }
  };
  return (
    <div className="flex items-start justify-between gap-3 rounded-xl border border-border/60 bg-card/40 px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Flag className="size-3.5 shrink-0 text-primary" />
          <p className="truncate text-[13px] text-foreground">{n.title}</p>
          <CategoryChip category={cat} />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11.5px] text-muted-foreground">
          <span className="rounded-full bg-muted/60 px-1.5 py-0.5 text-[10.5px]">targets protected loop</span>
          {n.priority && <PriorityDot priority={n.priority} />}
          <span>{timeAgo(n.createdAt)}</span>
        </div>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="h-7 shrink-0 gap-1.5 px-2 text-[12px] text-muted-foreground"
        onClick={copy}
        title="Copy the intent so you can implement it by hand"
      >
        {copied ? <CheckCircle2 className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

const FAILURE_LABELS: Record<string, string> = {
  blocklist: "Blocked path",
  council_block: "Council blocked",
  tests_failed: "Tests failed",
  no_commit: "No commit",
  too_many_files: "Too many files",
  other: "Other",
};
function failClassTone(label: string): string {
  if (label === "blocklist" || label === "council_block") return "bg-rose-500";
  if (label === "tests_failed" || label === "no_commit") return "bg-amber-500";
  if (label === "too_many_files") return "bg-sky-500";
  return "bg-muted-foreground/50";
}

function FailureTriage({ digest }: { digest: FailureDigest }) {
  if (!digest || digest.total === 0) return null;
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Failure triage</span>
        <span className="text-[11.5px] tabular-nums text-muted-foreground">{digest.total} total</span>
      </div>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {digest.byClass.map((c) => (
          <span
            key={c.label}
            className="inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-background/40 px-2 py-0.5 text-[11px] text-muted-foreground"
          >
            <span className={cn("size-1.5 rounded-full", failClassTone(c.label))} />
            {FAILURE_LABELS[c.label] ?? c.label}
            <span className="tabular-nums text-foreground">{c.count}</span>
          </span>
        ))}
      </div>
      {digest.repeatedPaths.length > 0 && (
        <div className="mt-3 border-t border-border/50 pt-2.5">
          <p className="text-[11px] text-muted-foreground">Repeatedly targeted — likely where the real work is:</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {digest.repeatedPaths.map((p) => (
              <span
                key={p.path}
                className="inline-flex items-center gap-1 rounded-md bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
              >
                <FileCode2 className="size-3" />
                {stripPrefix(p.path)}
                <span className="tabular-nums text-foreground">×{p.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function LearnBadge({ flat, std }: { flat: boolean; std: number }) {
  return (
    <span
      className={cn(
        "mt-1 inline-flex items-center rounded-full px-1.5 py-0.5 text-[9.5px] font-medium uppercase tracking-wide",
        flat ? "bg-amber-500/10 text-amber-500" : "bg-sky-500/10 text-sky-500",
      )}
      title={
        flat
          ? `Flat (σ ${std.toFixed(3)}) — plateaued; incremental tweaks aren't moving it`
          : `Oscillating (σ ${std.toFixed(3)}) — responds to change, so it's improvable`
      }
    >
      {flat ? "plateaued" : "learnable"}
    </span>
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
            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-3">
              {axes.map(([axis, score]) => {
                const meta = fitness.perAxisMeta?.[axis];
                const weak = score < 0.6;
                return (
                  <div key={axis} className="min-w-0">
                    <div className="flex items-center justify-between gap-2 text-[11.5px]">
                      <span className="truncate text-muted-foreground">{axis.replace(/_/g, " ")}</span>
                      <span className="tabular-nums text-foreground">{score.toFixed(2)}</span>
                    </div>
                    <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted/50">
                      <div
                        className={cn("h-full rounded-full", weak ? "bg-amber-500" : "bg-emerald-500")}
                        style={{ width: `${Math.max(0, Math.min(1, score)) * 100}%` }}
                      />
                    </div>
                    {weak && meta && <LearnBadge flat={meta.flat} std={meta.std} />}
                  </div>
                );
              })}
            </div>
          )}
          {fitness.weakAxis && (() => {
            const wa = fitness.weakAxis;
            const meta = fitness.perAxisMeta?.[wa.axis];
            return (
              <p className="mt-3.5 text-[12px] leading-5 text-muted-foreground">
                Weakest axis:{" "}
                <span className="text-foreground">{wa.axis.replace(/_/g, " ")}</span> at{" "}
                {wa.score.toFixed(2)}.{" "}
                {meta?.flat
                  ? "It's been flat — incremental tweaks aren't moving it, so the next proposal should try a structurally different approach."
                  : "It oscillates, so it responds to change — the next improvement targets it."}
              </p>
            );
          })()}
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
          <h3 className="text-[14px] font-medium">Autonomy graduation</h3>
        </div>
        <span className="text-[12px] tabular-nums text-muted-foreground">
          {graduation.metCount}/{graduation.total} met
        </span>
      </div>
      <p className="mt-1.5 text-[12.5px] text-muted-foreground">
        Tracking progress from{" "}
        <span className="text-foreground">{autonomy.currentLabel}</span> to{" "}
        <span className="text-foreground">{autonomy.targetLabel}</span>. These are
        observable track-record milestones — once consistently met, JARVIS earns
        the next autonomy level.
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
      <h3 className="text-[14px] font-medium">Change acceptance criteria</h3>
      <p className="mt-1 text-[12.5px] text-muted-foreground">
        Invariants every proposed diff must pass before it can be approved —
        independent of how many autonomy milestones are met.
      </p>
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
            {flaws.map((x, i) => {
              const it = assessmentText(x);
              return (
                <li key={i} className="flex gap-2 text-[12.5px] text-foreground/90">
                  <span className="text-amber-500">·</span>
                  <span>
                    {it.head && <span className="font-medium text-foreground">{it.head}: </span>}
                    {it.body}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {improvements.length > 0 && (
        <div className="mt-3">
          <SectionLabel>Improvements to try</SectionLabel>
          <ul className="space-y-1">
            {improvements.map((x, i) => {
              const it = assessmentText(x);
              return (
                <li key={i} className="flex gap-2 text-[12.5px] text-foreground/90">
                  <span className="text-emerald-500">·</span>
                  <span>
                    {it.head && <span className="font-medium text-foreground">{it.head}: </span>}
                    {it.body}
                  </span>
                </li>
              );
            })}
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
    <div className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-card/40 px-2.5 py-1.5">
      <span className="text-[12px] text-muted-foreground">Model</span>
      <select
        value={current}
        disabled={busy}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Build model — autonomous builds run on this; empty inherits the global model."
        title="The model autonomous builds run on. Empty inherits the global model."
        className="rounded border-0 bg-transparent px-0 py-0 text-[12px] text-foreground outline-none disabled:opacity-50"
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

function fmtElapsed(sec: number): string {
  if (sec <= 0) return "just now";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// One live build. The spinner + ticking elapsed make "is anything happening?"
// answerable at a glance — the gap that made a click on Build feel like a no-op.
function BuildingRow({ b }: { b: InFlightBuild }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-border/60 bg-card/50 px-4 py-3">
      <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium text-foreground">Building…</span>
          {b.kind && (
            <span className="rounded-full border border-border/60 px-1.5 py-0.5 text-[10.5px] text-muted-foreground">
              {b.kind}
            </span>
          )}
          <span className="ml-auto text-[11.5px] tabular-nums text-muted-foreground">
            {b.elapsedSec > 0 ? `${fmtElapsed(b.elapsedSec)} elapsed` : "just started"}
          </span>
        </div>
        <p className="mt-1 line-clamp-2 text-[12.5px] leading-5 text-muted-foreground">
          {b.intent || "(intent unavailable)"}
        </p>
        <p className="mt-1 font-mono text-[10.5px] text-muted-foreground/70">{b.id}</p>
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
      <p className="mt-4 text-[14px] font-medium text-foreground">Nothing to review</p>
      <p className="mt-1 max-w-sm text-[13px] leading-5 text-muted-foreground">
        JARVIS proposes improvements to his own code as he runs. When he does, they&apos;ll appear here
        — each auto-reviewed by a 3-lens council (correctness · security · regression) — for your
        approval. Run a cycle to kick one off now.
      </p>
    </div>
  );
}
