"use client";
import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { Brain, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export function KimiReasoning({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(streaming);
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

  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);

  useEffect(() => {
    if (!open || !streaming) return;
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text, open, streaming]);

  const label = streaming
    ? "Thinking…"
    : duration !== null
      ? `Thought for ${duration}s`
      : "Thoughts";

  if (!text && !streaming) return null;

  return (
    <div className="mb-3 rounded-lg border border-border/40 bg-muted/20 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[12px] text-muted-foreground hover:bg-muted/40 transition-colors"
      >
        <Brain
          className={cn(
            "size-3.5 shrink-0",
            streaming ? "text-primary animate-pulse" : "text-muted-foreground/70",
          )}
        />
        <span className="flex-1 font-medium">{label}</span>
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
