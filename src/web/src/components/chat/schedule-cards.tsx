"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, CalendarClock, Check, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  parseCreateSchedule,
  parseInterviewQuestion,
  type InterviewQuestion,
} from "@/lib/scheduled/interview";

// Renders the scheduled-task setup blocks a message may contain:
//  - <jarvisQuestion>  → tappable option card; answering sends a new turn
//  - <jarvisCreateSchedule> → confirmation card that POSTs the real task
// Returns null when the message has neither (the common case).
export function ScheduleInterviewCards({
  text,
  active,
  onAnswer,
}: {
  text: string;
  /** True only for the last assistant message while the chat is idle —
   *  older question cards render disabled (already answered). */
  active: boolean;
  onAnswer: (text: string) => void;
}) {
  const question = parseInterviewQuestion(text);
  const create = parseCreateSchedule(text);
  if (!question && !create) return null;
  return (
    <div className="mt-3 space-y-3">
      {question && (
        <QuestionCard q={question} active={active} onAnswer={onAnswer} />
      )}
      {create && <CreateCard create={create} />}
    </div>
  );
}

function QuestionCard({
  q,
  active,
  onAnswer,
}: {
  q: InterviewQuestion;
  active: boolean;
  onAnswer: (text: string) => void;
}) {
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [free, setFree] = useState("");
  const [sent, setSent] = useState(false);
  const disabled = !active || sent;

  const toggle = (i: number) => {
    if (disabled) return;
    if (!q.multiSelect) {
      // Single-select fires immediately (claude.ai behavior).
      setSent(true);
      onAnswer(q.options[i].label);
      return;
    }
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  };

  const submitMulti = () => {
    if (disabled) return;
    const chosen = [...picked].map((i) => q.options[i].label);
    const answer = [...chosen, free.trim()].filter(Boolean).join(", ");
    if (!answer) return;
    setSent(true);
    onAnswer(answer);
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-border/60 bg-card/50",
        disabled && "opacity-60",
      )}
    >
      <div className="px-4 py-3 text-[13.5px] font-medium text-foreground">
        {q.question}
      </div>
      <div className="border-t border-border/40">
        {q.options.map((o, i) => (
          <button
            key={i}
            type="button"
            disabled={disabled}
            onClick={() => toggle(i)}
            className={cn(
              "flex w-full items-center gap-3 px-4 py-2.5 text-left text-[13.5px] transition-colors",
              "border-b border-border/30 last:border-b-0",
              picked.has(i)
                ? "bg-primary/10 text-foreground"
                : "text-foreground/85 hover:bg-accent/40",
              disabled && "cursor-default hover:bg-transparent",
            )}
          >
            <span
              className={cn(
                "flex size-4 shrink-0 items-center justify-center rounded border",
                q.multiSelect ? "rounded" : "rounded-full",
                picked.has(i)
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border",
              )}
            >
              {picked.has(i) && <Check className="size-3" />}
            </span>
            <span className="flex-1">
              {o.label}
              {o.hint && (
                <span className="ml-1.5 text-muted-foreground">{o.hint}</span>
              )}
              {o.recommended && (
                <span className="ml-1.5 text-muted-foreground">
                  (Recommended)
                </span>
              )}
            </span>
            {!q.multiSelect && !disabled && (
              <ArrowRight className="size-3.5 text-muted-foreground/50" />
            )}
          </button>
        ))}
      </div>
      {q.multiSelect && !disabled && (
        <div className="flex items-center gap-2 border-t border-border/40 px-3 py-2">
          <input
            value={free}
            onChange={(e) => setFree(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitMulti()}
            placeholder="Or type an answer…"
            className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted-foreground/60"
          />
          <button
            type="button"
            onClick={submitMulti}
            className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground transition-opacity hover:opacity-90"
            aria-label="Submit answer"
          >
            <ArrowRight className="size-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function CreateCard({
  create,
}: {
  create: ReturnType<typeof parseCreateSchedule>;
}) {
  const router = useRouter();
  const [state, setState] = useState<"idle" | "saving" | "done" | "error">(
    "idle",
  );
  if (!create) return null;

  const go = async () => {
    setState("saving");
    try {
      const r = await fetch("/api/scheduled", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: create.name,
          prompt: create.prompt,
          cron: create.cron,
          label: create.label,
        }),
      });
      if (r.ok) {
        setState("done");
        toast.success("Scheduled task created");
      } else {
        setState("error");
        toast.error("Couldn't create the task");
      }
    } catch {
      setState("error");
      toast.error("Couldn't create the task");
    }
  };

  return (
    <div className="rounded-xl border border-border/60 bg-card/50 p-4">
      <div className="flex items-center gap-2">
        <CalendarClock className="size-4 text-primary" />
        <span className="text-[14px] font-medium">{create.name}</span>
      </div>
      <div className="mt-1 text-[12.5px] text-muted-foreground">
        {create.label}
      </div>
      <p className="mt-2 line-clamp-3 text-[12.5px] text-foreground/70">
        {create.prompt}
      </p>
      <div className="mt-3 flex items-center justify-end gap-2">
        {state === "done" ? (
          <button
            type="button"
            onClick={() => router.push("/scheduled")}
            className="flex items-center gap-1.5 rounded-lg bg-primary/15 px-3 py-1.5 text-[13px] font-medium text-primary"
          >
            <Check className="size-3.5" /> Scheduled — view
          </button>
        ) : (
          <button
            type="button"
            onClick={go}
            disabled={state === "saving"}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {state === "saving" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <CalendarClock className="size-3.5" />
            )}
            {state === "error" ? "Retry" : "Create task"}
          </button>
        )}
      </div>
    </div>
  );
}
