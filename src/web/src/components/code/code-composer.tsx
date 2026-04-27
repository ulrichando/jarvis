"use client";

import { useRef, useEffect } from "react";
import { CircleDot, Plus, CornerDownLeft, ChevronDown, Circle, Loader2 } from "lucide-react";

export function CodeComposer({
  value,
  onChange,
  onSubmit,
  onSelectMachine,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onSelectMachine: () => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <div className="border border-border/60 rounded-2xl overflow-hidden bg-card">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/40">
        <span className="flex items-center gap-1.5 rounded-full border border-border/60 bg-accent/30 px-2.5 py-1 text-[12px] text-foreground/70">
          <CircleDot className="size-3 text-primary" />
          Default
        </span>
        <button
          type="button"
          onClick={onSelectMachine}
          className="flex items-center gap-1.5 rounded-full border border-border/60 bg-accent/30 px-2.5 py-1 text-[12px] text-foreground/70 hover:bg-accent/50 hover:text-foreground transition-colors"
        >
          <Plus className="size-3" />
          Select machine…
        </button>
      </div>

      <div className="flex items-center gap-2 px-3 py-2 border-b border-border/40">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit();
            }
          }}
          placeholder="Describe a task or ask a question"
          rows={1}
          className="flex-1 resize-none bg-transparent text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none"
        />
        <button
          type="button"
          onClick={onSubmit}
          aria-label="Send"
          className="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <CornerDownLeft className="size-3.5" />
        </button>
      </div>

      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-1">
          <button type="button" aria-disabled="true"
            className="rounded px-2 py-1 text-[12px] text-foreground/60 hover:bg-accent/40 hover:text-foreground transition-colors">
            Accept edits
          </button>
          <button type="button" aria-label="Attach" aria-disabled="true"
            className="flex size-6 items-center justify-center rounded text-foreground/50 hover:bg-accent/40 hover:text-foreground transition-colors">
            <Plus className="size-3.5" />
          </button>
          <button type="button" aria-label="Record" aria-disabled="true"
            className="flex size-6 items-center justify-center rounded text-foreground/50 hover:bg-accent/40 hover:text-foreground transition-colors">
            <Circle className="size-3.5" />
          </button>
          <button type="button" aria-label="More options" aria-disabled="true"
            className="flex size-6 items-center justify-center rounded text-foreground/50 hover:bg-accent/40 hover:text-foreground transition-colors">
            <ChevronDown className="size-3.5" />
          </button>
        </div>
        <div className="flex items-center gap-2 text-[11.5px] text-foreground/50">
          <span>Jarvis 4.7</span>
          <span>1M</span>
          <Loader2 className="size-3 animate-spin opacity-40" />
        </div>
      </div>
    </div>
  );
}
