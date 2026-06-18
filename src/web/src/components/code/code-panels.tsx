"use client";

import { useEffect, useRef, useState } from "react";
import { X, Folder, ArrowRight, ChevronDown, GitCompare, GitPullRequest, ListChecks } from "lucide-react";

export type PanelName = "diff" | "background" | "plan";
export type PanelsState = Record<PanelName, boolean>;

function PanelShell({
  header,
  onClose,
  children,
}: {
  header: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col border-l border-border/40">
      <div className="flex h-11 shrink-0 items-center justify-between px-4">
        <div className="flex min-w-0 items-center gap-1.5 text-[13px] text-foreground/80">{header}</div>
        <button
          type="button"
          aria-label="Close panel"
          onClick={onClose}
          className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-accent/50 hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
    </div>
  );
}

function Empty({ icon, lines }: { icon: React.ReactNode; lines: string[] }) {
  return (
    <div className="flex flex-1 items-center justify-center px-4 text-center">
      <div className="max-w-[220px] text-[12.5px] text-muted-foreground/70">
        {icon && <div className="mb-2 flex justify-center text-muted-foreground/50">{icon}</div>}
        {lines.map((l, i) => (
          <div key={i} className={i === 0 ? "text-muted-foreground/80" : "mt-1 text-muted-foreground/55"}>
            {l}
          </div>
        ))}
      </div>
    </div>
  );
}

export function CodePanels({
  panels,
  onClose,
  baseBranch = "main",
  workBranch,
  sessionId,
  onComment,
}: {
  panels: PanelsState;
  onClose: (p: PanelName) => void;
  baseBranch?: string;
  workBranch: string;
  /** Session whose container diff the Diff panel reads. */
  sessionId?: string;
  /** Queue an inline review comment on <file>:<line> (bundled into the next
   *  message, like claude.ai/code). */
  onComment?: (file: string, line: number, text: string) => void;
}) {
  // Diff + Background stack in one column; Plan gets its own column (per the
  // claude.ai/code layout).
  const stacked = panels.diff || panels.background;
  return (
    <div className="flex shrink-0">
      {stacked && (
        <div className="flex w-[380px] flex-col">
          {panels.diff && (
            <PanelShell
              header={
                <>
                  <Folder className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="text-foreground/70">{baseBranch}</span>
                  <ArrowRight className="size-3 shrink-0 text-muted-foreground/60" />
                  <span className="truncate">{workBranch}</span>
                </>
              }
              onClose={() => onClose("diff")}
            >
              <DiffPanel sessionId={sessionId} onComment={onComment} />
            </PanelShell>
          )}
          {panels.background && (
            <PanelShell header={<span>Background tasks</span>} onClose={() => onClose("background")}>
              <Empty icon={<GitCompare className="size-5" />} lines={["Background work appears here"]} />
            </PanelShell>
          )}
        </div>
      )}
      {panels.plan && (
        <div className="flex w-[280px] flex-col">
          <PanelShell header={<span>Plan</span>} onClose={() => onClose("plan")}>
            <PlanPanel sessionId={sessionId} />
          </PanelShell>
        </div>
      )}
    </div>
  );
}

type DiffData = { branch: string; base: string; ahead: number; stat: string; diff: string };
type DiffLine = { t: "add" | "del" | "ctx" | "hunk"; text: string; ln?: number };
type DiffFile = { path: string; lines: DiffLine[] };

/** Parse a unified `git diff` into per-file, per-line records. add/ctx lines
 *  carry their new-file line number (`ln`) so inline comments can reference
 *  "<file>:<line>". */
function parseDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  const blocks = diff.split(/^diff --git /m);
  for (const raw of blocks) {
    const block = raw.trim();
    if (!block) continue;
    const plus = /^\+\+\+ b\/(.+)$/m.exec(block);
    const git = /^a\/.+ b\/(.+)$/m.exec(block);
    const path = (plus?.[1] ?? git?.[1] ?? "file").trim();
    const lines: DiffLine[] = [];
    let newLine = 0;
    for (const ln of block.split("\n")) {
      if (
        ln.startsWith("index ") ||
        ln.startsWith("--- ") ||
        ln.startsWith("+++ ") ||
        ln.startsWith("new file") ||
        ln.startsWith("deleted file") ||
        ln.startsWith("similarity ") ||
        ln.startsWith("rename ") ||
        ln.startsWith("old mode") ||
        ln.startsWith("new mode") ||
        /^a\/.+ b\/.+$/.test(ln)
      ) {
        continue;
      }
      if (ln.startsWith("@@")) {
        newLine = Number(/\+(\d+)/.exec(ln)?.[1] ?? newLine);
        lines.push({ t: "hunk", text: ln });
      } else if (ln.startsWith("+")) {
        lines.push({ t: "add", text: ln.slice(1), ln: newLine });
        newLine++;
      } else if (ln.startsWith("-")) {
        lines.push({ t: "del", text: ln.slice(1) });
      } else {
        lines.push({ t: "ctx", text: ln.startsWith(" ") ? ln.slice(1) : ln, ln: newLine });
        newLine++;
      }
    }
    files.push({ path, lines });
  }
  return files;
}

/** Live diff of a container session — polls so it updates as the agent works.
 *  Click an added/context line to queue an inline review comment (bundled into
 *  the next message), and "Create PR" opens a pull request for the work. */
function DiffPanel({
  sessionId,
  onComment,
}: {
  sessionId?: string;
  onComment?: (file: string, line: number, text: string) => void;
}) {
  const [data, setData] = useState<DiffData | null>(null);
  const [loading, setLoading] = useState(true);
  const [pr, setPr] = useState<{ url: string; branch: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const [prError, setPrError] = useState<string | null>(null);
  const [commentOn, setCommentOn] = useState<{ path: string; ln: number } | null>(null);
  const [commentText, setCommentText] = useState("");
  const [prMenuOpen, setPrMenuOpen] = useState(false);
  const fileRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [prStatus, setPrStatus] = useState<{
    pr: { number: number; url: string; state: string } | null;
    checks: { total: number; passed: number; failed: number; pending: number; failing: string[] } | null;
    sha: string | null;
    repo: string | null;
  } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [autoFix, setAutoFix] = useState(false);
  const autoFixRef = useRef(autoFix);
  autoFixRef.current = autoFix;
  const lastFixedShaRef = useRef<string | null>(null);
  const [autoMerge, setAutoMerge] = useState(false);
  useEffect(() => {
    try {
      setAutoFix(localStorage.getItem("jarvis.code.autofix") === "1");
      setAutoMerge(localStorage.getItem("jarvis.code.automerge") === "1");
    } catch {
      /* no localStorage */
    }
  }, []);

  // Toggle a server-side session flag (auto-fix / auto-merge) the background
  // tick reads, mirroring it to localStorage for instant UI.
  const setFlag = (key: "autofix" | "automerge", on: boolean) => {
    (key === "autofix" ? setAutoFix : setAutoMerge)(on);
    try {
      localStorage.setItem(`jarvis.code.${key}`, on ? "1" : "0");
    } catch {
      /* ignore */
    }
    if (sessionId) {
      fetch(`/api/bridge/v1/sessions/${sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: on }),
      }).catch(() => {});
    }
  };

  // Ask the session to review its own diff (the claude.ai/code "Review code"
  // action). The agent leaves notes in the transcript.
  const reviewCode = () => {
    if (!sessionId) return;
    fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: "Review the changes on this branch like a senior engineer: flag bugs, security issues, and correctness/logic problems (skip style and pre-existing issues). Reference each finding by file:line and keep it concise.",
      }),
    }).catch(() => {});
  };

  // Post a model code-review as a comment on the open PR (claude.ai/code Code
  // Review). Opens the comment on success.
  const reviewPr = async () => {
    if (!prStatus?.pr || !prStatus.repo) return;
    setReviewing(true);
    try {
      const r = await fetch(`/api/bridge/v1/github/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: prStatus.repo, number: prStatus.pr.number }),
      });
      const j = (await r.json()) as { url?: string };
      if (r.ok && j.url) window.open(j.url, "_blank", "noopener");
    } catch {
      /* ignore */
    } finally {
      setReviewing(false);
    }
  };

  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/diff`);
        if (r.ok && active) setData((await r.json()) as DiffData);
      } catch {
        /* transient */
      }
      if (active) {
        setLoading(false);
        timer = setTimeout(load, 4000);
      }
    };
    load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [sessionId]);

  // PR + CI status — a slower poll (GitHub API, rate-limited) keyed on the
  // container's current branch.
  const branch = data?.branch;
  useEffect(() => {
    if (!sessionId || !branch) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const r = await fetch(
          `/api/bridge/v1/sessions/${sessionId}/pr-status?branch=${encodeURIComponent(branch)}`,
        );
        if (r.ok && active) {
          const s = (await r.json()) as {
            pr: { number: number; url: string; state: string } | null;
            checks: { total: number; passed: number; failed: number; pending: number; failing: string[] } | null;
            sha: string | null;
            repo: string | null;
          };
          setPrStatus(s);
          // Auto-fix: at most once per failing commit, message the session to fix CI.
          if (
            autoFixRef.current &&
            s.checks &&
            s.checks.failed > 0 &&
            s.sha &&
            s.sha !== lastFixedShaRef.current
          ) {
            lastFixedShaRef.current = s.sha;
            const failing = s.checks.failing ?? [];
            fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                text: `The CI checks (${failing.join(", ") || "on this PR"}) are failing. Investigate the failures, fix them, and push the fix to the same branch.`,
              }),
            }).catch(() => {});
          }
        }
      } catch {
        /* transient */
      }
      if (active) timer = setTimeout(load, 15000);
    };
    load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [sessionId, branch]);

  const fixCi = async () => {
    if (!sessionId) return;
    const failing = prStatus?.checks?.failing ?? [];
    const text = `The CI checks (${failing.join(", ") || "on this PR"}) are failing. Investigate the failures, fix them, and push the fix to the same branch.`;
    try {
      await fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
    } catch {
      /* ignore */
    }
  };

  const createPr = async (mode: "full" | "draft" | "compose" = "full") => {
    if (!sessionId) return;
    setPrMenuOpen(false);
    setCreating(true);
    setPrError(null);
    try {
      const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/pr`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const j = (await r.json()) as {
        url?: string;
        branch?: string;
        error?: { message?: string } | string;
      };
      if (r.ok && j.url) {
        setPr({ url: j.url, branch: j.branch ?? "" });
        window.open(j.url, "_blank", "noopener");
      } else {
        setPrError((typeof j.error === "string" ? j.error : j.error?.message) ?? "Could not create PR");
      }
    } catch (e) {
      setPrError(String(e));
    } finally {
      setCreating(false);
    }
  };

  const submitComment = (path: string, ln: number) => {
    const t = commentText.trim();
    if (t && onComment) onComment(path, ln, t);
    setCommentOn(null);
    setCommentText("");
  };

  if (loading && !data) {
    return <Empty icon={<GitCompare className="size-5" />} lines={["Loading changes…"]} />;
  }
  const diff = data?.diff ?? "";
  if (!diff.trim()) {
    return (
      <Empty
        icon={<GitCompare className="size-5" />}
        lines={["No changes yet", "Edits the agent makes appear here as a diff."]}
      />
    );
  }
  const files = parseDiff(diff);
  let adds = 0;
  let dels = 0;
  for (const f of files) for (const l of f.lines) (l.t === "add" && adds++) || (l.t === "del" && dels++);
  // Surface an existing PR (whether the agent or this panel opened it).
  const prUrl = pr?.url ?? prStatus?.pr?.url ?? null;
  const checks = prStatus?.checks;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border/40 px-4 py-1.5 text-[11.5px] text-muted-foreground">
        <span>{files.length} file{files.length === 1 ? "" : "s"}</span>
        <span className="text-emerald-500">+{adds}</span>
        <span className="text-red-500">−{dels}</span>
        <button
          type="button"
          onClick={reviewCode}
          title="Ask Jarvis to review these changes in this session"
          className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-accent/50 hover:text-foreground"
        >
          Review
        </button>
        {prStatus?.pr && prStatus.repo && (
          <button
            type="button"
            onClick={reviewPr}
            disabled={reviewing}
            title="Post a code review as a comment on the PR"
            className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-accent/50 hover:text-foreground disabled:opacity-60"
          >
            {reviewing ? "Reviewing…" : "Review PR ↗"}
          </button>
        )}
        <div className="ml-auto flex items-center gap-2">
          {data?.branch && <span className="max-w-[110px] truncate">{data.branch}</span>}
          {prUrl ? (
            <a
              href={prUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-md bg-secondary px-2 py-1 text-[11px] font-medium text-secondary-foreground hover:bg-secondary/80"
            >
              <GitPullRequest className="size-3" /> View PR
            </a>
          ) : (
            <div className="relative">
              <div className="inline-flex overflow-hidden rounded-md">
                <button
                  type="button"
                  onClick={() => createPr("full")}
                  disabled={creating}
                  className="inline-flex items-center gap-1 bg-orange-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-orange-500 disabled:opacity-60"
                >
                  <GitPullRequest className="size-3" /> {creating ? "Creating…" : "Create PR"}
                </button>
                <button
                  type="button"
                  aria-label="PR options"
                  onClick={() => setPrMenuOpen((o) => !o)}
                  disabled={creating}
                  className="flex items-center border-l border-white/20 bg-orange-600 px-1 text-white hover:bg-orange-500 disabled:opacity-60"
                >
                  <ChevronDown className="size-3" />
                </button>
              </div>
              {prMenuOpen && (
                <div className="absolute right-0 top-full z-50 mt-1 w-44 rounded-lg border border-border bg-card p-1 shadow-xl">
                  <button type="button" onClick={() => createPr("full")} className="block w-full rounded px-2 py-1.5 text-left text-[12px] text-foreground/90 hover:bg-accent/50">Create pull request</button>
                  <button type="button" onClick={() => createPr("draft")} className="block w-full rounded px-2 py-1.5 text-left text-[12px] text-foreground/90 hover:bg-accent/50">Create draft PR</button>
                  <button type="button" onClick={() => createPr("compose")} className="block w-full rounded px-2 py-1.5 text-left text-[12px] text-foreground/90 hover:bg-accent/50">Open compose page ↗</button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {checks && checks.total > 0 && (
        <div className="flex shrink-0 items-center gap-2 border-b border-border/40 px-4 py-1 text-[11px]">
          <span className="text-muted-foreground">CI</span>
          {checks.passed > 0 && <span className="text-emerald-500">✓{checks.passed}</span>}
          {checks.failed > 0 && <span className="text-red-500">✗{checks.failed}</span>}
          {checks.pending > 0 && <span className="text-amber-500">◷{checks.pending}</span>}
          <label
            className="ml-auto flex cursor-pointer items-center gap-1 text-[10.5px] text-muted-foreground"
            title="Automatically ask Jarvis to fix CI failures (once per failing commit)"
          >
            <input
              type="checkbox"
              checked={autoFix}
              onChange={(e) => setFlag("autofix", e.target.checked)}
              className="size-3 accent-orange-600"
            />
            Auto-fix
          </label>
          <label
            className="flex cursor-pointer items-center gap-1 text-[10.5px] text-muted-foreground"
            title="Merge the PR automatically once all checks pass"
          >
            <input
              type="checkbox"
              checked={autoMerge}
              onChange={(e) => setFlag("automerge", e.target.checked)}
              className="size-3 accent-orange-600"
            />
            Auto-merge
          </label>
          {checks.failed > 0 && (
            <button
              type="button"
              onClick={fixCi}
              className="rounded bg-orange-600 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-orange-500"
            >
              Fix CI
            </button>
          )}
        </div>
      )}
      {prError && <div className="shrink-0 px-4 py-1 text-[11px] text-red-500">{prError}</div>}
      {onComment && (
        <div className="shrink-0 px-4 py-1 text-[10.5px] text-muted-foreground/55">
          Click a line to comment — it bundles into your next message.
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        {files.length > 1 && (
          <div className="border-b border-border/40 px-2 py-1.5">
            {files.map((f) => {
              let a = 0;
              let d = 0;
              for (const l of f.lines) (l.t === "add" && a++) || (l.t === "del" && d++);
              return (
                <button
                  key={f.path}
                  type="button"
                  onClick={() =>
                    fileRefs.current[f.path]?.scrollIntoView({ behavior: "smooth", block: "start" })
                  }
                  className="flex w-full items-center gap-2 rounded px-1.5 py-0.5 text-left text-[11px] hover:bg-accent/40"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-foreground/75">{f.path}</span>
                  <span className="shrink-0 text-emerald-500">+{a}</span>
                  <span className="shrink-0 text-red-500">−{d}</span>
                </button>
              );
            })}
          </div>
        )}
        {files.map((f) => (
          <div
            key={f.path}
            ref={(el) => {
              fileRefs.current[f.path] = el;
            }}
            className="border-b border-border/30"
          >
            <div className="sticky top-0 z-10 bg-card/95 px-3 py-1.5 font-mono text-[11px] text-foreground/80 backdrop-blur">
              {f.path}
            </div>
            <div className="pb-1 font-mono text-[11px] leading-[1.5]">
              {f.lines.map((l, i) => {
                const commentable =
                  !!onComment && (l.t === "add" || l.t === "ctx") && l.ln !== undefined;
                const active =
                  commentOn?.path === f.path && commentOn?.ln === l.ln && l.ln !== undefined;
                return (
                  <div key={i}>
                    <div
                      onClick={
                        commentable
                          ? () => {
                              setCommentOn({ path: f.path, ln: l.ln! });
                              setCommentText("");
                            }
                          : undefined
                      }
                      title={commentable ? "Click to comment on this line" : undefined}
                      className={
                        (l.t === "add"
                          ? "bg-emerald-500/12 text-emerald-300"
                          : l.t === "del"
                            ? "bg-red-500/12 text-red-300"
                            : l.t === "hunk"
                              ? "text-muted-foreground/60"
                              : "text-foreground/55") +
                        " overflow-x-auto whitespace-pre px-3" +
                        (commentable ? " cursor-pointer hover:bg-orange-500/10" : "")
                      }
                    >
                      <span className="select-none opacity-50">
                        {l.t === "add" ? "+" : l.t === "del" ? "−" : " "}{" "}
                      </span>
                      {l.text || " "}
                    </div>
                    {active && (
                      <div className="flex items-start gap-1 bg-accent/30 px-3 py-1.5">
                        <textarea
                          autoFocus
                          rows={2}
                          value={commentText}
                          onChange={(e) => setCommentText(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault();
                              submitComment(f.path, l.ln!);
                            }
                            if (e.key === "Escape") setCommentOn(null);
                          }}
                          placeholder={`Comment on ${f.path}:${l.ln} — Enter to queue, Esc to cancel`}
                          className="min-w-0 flex-1 resize-none rounded border border-border bg-background px-2 py-1 font-sans text-[11px] text-foreground outline-none focus:border-orange-500/60"
                        />
                        <button
                          type="button"
                          onClick={() => submitComment(f.path, l.ln!)}
                          className="shrink-0 rounded bg-orange-600 px-2 py-1 font-sans text-[11px] text-white hover:bg-orange-500"
                        >
                          Add
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

type PlanData = { plan: string; mode: string };

/** Renders the agent's current plan (from plan mode / a plan_* event) — the
 *  SDLC design phase, polled live like the diff. */
function PlanPanel({ sessionId }: { sessionId?: string }) {
  const [plan, setPlan] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/plan`);
        if (r.ok && active) setPlan(((await r.json()) as PlanData).plan ?? "");
      } catch {
        /* transient */
      }
      if (active) {
        setLoading(false);
        timer = setTimeout(load, 4000);
      }
    };
    load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [sessionId]);

  if (loading && !plan) {
    return <Empty icon={<ListChecks className="size-5" />} lines={["Loading plan…"]} />;
  }
  if (!plan.trim()) {
    return (
      <Empty
        icon={<ListChecks className="size-5" />}
        lines={["No plan yet", "Switch to Plan mode and Jarvis writes the plan here before editing."]}
      />
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
      <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-relaxed text-foreground/85">
        {plan}
      </pre>
    </div>
  );
}
