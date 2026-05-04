"use client";

import { useState } from "react";
import { CheckCircle2, AlertTriangle, ChevronDown, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import type { VerifyOutcome } from "@/lib/verify/types";

/**
 * Inline status pill rendered below an assistant message after the
 * runtime ran the verify pipeline (tsc / curl / screenshot). Replaces
 * the old synthetic "[auto-retry]" user message that the chat used to
 * inject when verify failed — that pattern matched Cursor / Devin /
 * Cline but produced runaway loops + transient-failure misclassification.
 *
 * Two visual states:
 *   - `ok`     → faint green check + "Verified" label, collapsed
 *   - `failed` → amber warning + summary ("tsc failed", "preview 500"),
 *                expandable to show the actual error output, with a
 *                "Try fix" button that asks the parent to re-prompt
 *                the model. The model already has the verify output
 *                in its conversation history (as a `<jarvisVerify>`
 *                block), so this is just a manual nudge.
 *
 * Keep it small. The verify failure should NOT dominate the message —
 * the user already sees the assistant's prose + artifacts above; this
 * is just a status badge to say "by the way, the build broke."
 */
export function VerifyPill({
  outcome,
  onRetry,
}: {
  outcome: VerifyOutcome;
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  // Build a short summary of what failed. Order matters: tsc errors
  // are usually the actionable root cause, preview failures are
  // downstream symptoms.
  const failures: string[] = [];
  if (outcome.typecheck.ran && !outcome.typecheck.ok) failures.push("tsc");
  if (outcome.preview.ran && !outcome.preview.ok) {
    failures.push(
      outcome.preview.status
        ? `preview ${outcome.preview.status}`
        : "preview down",
    );
  }

  if (outcome.ok) {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-[11.5px] text-emerald-500/85">
        <CheckCircle2 className="size-3.5" />
        <span>Verified</span>
        {outcome.fixers.length > 0 && (
          <span className="text-muted-foreground/70">
            · auto-fixed {outcome.fixers.length}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full",
          "border border-amber-500/30 bg-amber-500/10 px-2.5 py-1",
          "text-[11.5px] font-medium text-amber-400/90",
          "hover:bg-amber-500/15 transition-colors",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/40",
        )}
        aria-expanded={expanded}
      >
        <AlertTriangle className="size-3.5" />
        <span>Verify failed{failures.length > 0 ? `: ${failures.join(", ")}` : ""}</span>
        <ChevronDown
          className={cn(
            "size-3 text-amber-400/70 transition-transform",
            expanded && "rotate-180",
          )}
        />
      </button>

      {expanded && (
        <div className="mt-2 rounded-lg border border-border/60 bg-card/40 p-3 text-[12px]">
          {outcome.typecheck.ran && !outcome.typecheck.ok && (
            <div className="mb-2">
              <div className="mb-1 font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground/70">
                tsc
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-muted/40 p-2 font-mono text-[11px] leading-snug text-foreground/85">
                {outcome.typecheck.output.split("\n").slice(0, 30).join("\n")}
              </pre>
            </div>
          )}
          {outcome.preview.ran && !outcome.preview.ok && (
            <div className="mb-2 text-foreground/85">
              <span className="font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground/70">
                preview
              </span>{" "}
              returned status {outcome.preview.status ?? "(no response)"}.{" "}
              Check <code className="font-mono">.jarvis/dev.log</code> for the
              server-side error.
            </div>
          )}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full",
                "border border-border bg-card/80 px-2.5 py-1",
                "text-[11.5px] text-foreground hover:bg-card",
                "transition-colors",
                "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
              )}
              aria-label="Try fix"
            >
              <RotateCcw className="size-3" />
              <span>Try fix</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
