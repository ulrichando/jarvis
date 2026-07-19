"use client";
import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

// Reasoning / "thinking" disclosure shown above an assistant reply for any
// model that streams a hidden reasoning trace (DeepSeek reasoner, K2.6
// thinking, o-series, etc.). Deliberately minimal: no brain/sparkle icon —
// while the model thinks it shows a plain "Thinking" heading with a small
// animated three-dot loader (uniform across every model), and it stays
// COLLAPSED by default so the raw reasoning never auto-dumps into the thread.
// The chevron reveals the trace on demand; once done it reads "Thought for Ns".
export function KimiReasoning({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (streaming && startedAtRef.current === null) {
      startedAtRef.current = Date.now();
    }
    if (!streaming && startedAtRef.current !== null && duration === null) {
      setDuration(Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000)));
    }
  }, [streaming, duration]);

  // Only auto-scroll the trace if the user chose to open it mid-stream.
  useEffect(() => {
    if (!open || !streaming) return;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text, open, streaming]);

  // Thinking in progress → ONLY the 3 loading dots (no box, no label, no
  // chevron, per Ulrich). Once thinking ends, the trace is offered as a small
  // collapsed "Thought for Ns" disclosure.
  if (streaming) {
    return (
      <div className="mb-3 flex items-center py-1">
        <ThinkingDots />
      </div>
    );
  }
  if (!text) return null;

  const doneLabel = duration !== null ? `Thought for ${duration}s` : "Thoughts";

  return (
    <div className="mb-3 rounded-lg border border-border/40 bg-muted/20 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-muted-foreground hover:bg-muted/40 transition-colors"
      >
        <span className="flex-1 font-medium">{doneLabel}</span>
        <ChevronDown
          className={cn(
            "size-3.5 shrink-0 transition-transform duration-200",
            open ? "rotate-180" : "rotate-0",
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="kimi-reasoning-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden border-t border-border/30"
          >
            <div
              ref={scrollerRef}
              className="max-h-64 overflow-y-auto px-3 py-2.5 text-[12px] leading-5 text-muted-foreground/85 whitespace-pre-wrap font-mono"
            >
              {text}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// Three dots fading in sequence — a calm "working" indicator. Driven by
// motion's compositor loop (no per-frame React state), so it's cheap even
// while the thread re-renders during streaming.
function ThinkingDots() {
  return (
    <span className="flex items-center gap-[3px]" aria-hidden>
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="size-1 rounded-full bg-current"
          animate={{ opacity: [0.25, 1, 0.25] }}
          transition={{
            duration: 0.9,
            repeat: Infinity,
            ease: "easeInOut",
            delay: i * 0.18,
          }}
        />
      ))}
    </span>
  );
}
