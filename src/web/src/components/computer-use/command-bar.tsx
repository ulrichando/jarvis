"use client";
import { useEffect, useRef } from "react";
import { CornerDownLeft, Loader2 } from "lucide-react";
import { ModelPicker } from "./model-picker";
import { shouldSubmitOnEnter, useAutoResize } from "@/lib/chat/enter-submit";

export function CommandBar({
  value, onChange, onSubmit, running, disabled, model, setModel, providers, placeholder,
}: {
  value: string; onChange: (v: string) => void; onSubmit: () => void;
  running: boolean; disabled: boolean; model: string; setModel: (m: string) => void;
  providers?: Record<string, boolean>; placeholder: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  // Grow with the task up to 128px (= max-h-32), then scroll internally —
  // shared with the chat/code composers so the three can't drift.
  useAutoResize(ref, value, 128);

  // Drop the caret into the box once the desktop is first ready, so you can
  // type a task immediately on load. One-shot — never steals focus back from
  // the live desktop on later give-control / run-finished transitions.
  const didFocus = useRef(false);
  useEffect(() => {
    if (!disabled && !didFocus.current && ref.current) {
      ref.current.focus();
      didFocus.current = true;
    }
  }, [disabled]);

  return (
    <footer className="shrink-0 border-t border-border/40 bg-card/30 px-4 pb-3.5 pt-3">
      <div className="flex items-center gap-2.5 rounded-2xl border border-border/60 bg-card px-2 py-2 pl-3.5 ring-4 ring-primary/5 focus-within:border-primary/40">
        <ModelPicker model={model} setModel={setModel} disabled={running} providers={providers} />
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          // IME-safe: ignores Enter mid-composition (CJK candidate / dead-key
          // accent) so a half-written task isn't sent.
          onKeyDown={(e) => { if (shouldSubmitOnEnter(e)) { e.preventDefault(); onSubmit(); } }}
          rows={1}
          placeholder={placeholder}
          aria-label="Task for Jarvis to run on the desktop"
          disabled={disabled}
          className="max-h-32 flex-1 resize-none self-center bg-transparent text-[14px] text-foreground outline-none placeholder:text-muted-foreground/70 disabled:opacity-50"
        />
        <button onClick={onSubmit} disabled={disabled || !value.trim()} title="Send" aria-label="Send task"
          className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40">
          {running ? <Loader2 className="size-4 animate-spin motion-reduce:animate-none" /> : <CornerDownLeft className="size-4" />}
        </button>
      </div>
      <div className="mt-2 flex gap-3.5 px-1 text-[11px] text-muted-foreground/70">
        <span><kbd className="rounded border border-border/40 bg-muted px-1 py-px font-mono text-[10px]">Enter</kbd> send</span>
        <span><kbd className="rounded border border-border/40 bg-muted px-1 py-px font-mono text-[10px]">⇧ Enter</kbd> newline</span>
      </div>
    </footer>
  );
}
