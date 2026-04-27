"use client";

import { ArrowUp, AudioLines, Paperclip, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ComposerModelPicker } from "./model-picker";
import { ComposerWorkspacePicker } from "./workspace-picker";
import { PlusMenu, SecondaryMenu } from "./plus-menu";
import type { Provider } from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";

type ComposerProps = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  status: "ready" | "submitted" | "streaming" | "error";
  provider: Provider;
  // When the chat is embedded inside a workbench page the workspace
  // is already pinned by the page itself — showing the picker would
  // be confusing because changing it would point chat at a different
  // workspace than the editor next to it.
  hideWorkspacePicker?: boolean;
};

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  status,
  provider,
  hideWorkspacePicker = false,
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const isBusy = status === "streaming" || status === "submitted";
  // Layout and common features are locked to anthropic style — stable across all models.
  const ux = getProviderUX("anthropic");
  // Extras (toggles, pre-composer) come from the real provider and render
  // below the box, not inside it.
  const providerUX = getProviderUX(provider);

  const initialToggles: Record<string, boolean> = {};
  for (const t of providerUX.inlineToggles ?? []) {
    initialToggles[t.id] = !!t.defaultOn;
  }
  const [toggles, setToggles] = useState(initialToggles);

  const autoSize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 240) + "px";
  }, []);

  useEffect(autoSize, [value, autoSize]);

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      // DEBUG instrumentation — remove with chat.tsx's dbg() once
      // the chat-flow bug is found.
      try {
        fetch("/api/dbg", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            stage: "composer:enter-pressed",
            isBusy,
            valueLen: value.trim().length,
            t: Date.now(),
          }),
        }).catch(() => {});
      } catch {}
      e.preventDefault();
      if (!isBusy && value.trim().length > 0) onSubmit();
    }
  };

  const startDictation = () =>
    toast.message("Voice input — coming soon", {
      description: "Whisper streaming will be wired next.",
    });

  const attach = () =>
    toast.message("Attachments — coming soon", {
      description: "File upload pipeline is not wired yet.",
    });

  const inlineToggles = providerUX.inlineToggles ?? [];

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      {providerUX.renderPreComposer && providerUX.renderPreComposer()}

      <div
        className={cn(
          "relative flex flex-col rounded-2xl border border-transparent bg-card/70 transition-all",
          "shadow-[0_1px_0_0_oklch(1_0_0/3%)_inset]",
          "focus-within:border-border focus-within:bg-card focus-within:shadow-[0_1px_0_0_oklch(1_0_0/5%)_inset]",
        )}
      >
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
          placeholder={ux.placeholder}
          className="resize-none bg-transparent px-5 pt-4 pb-2 text-[15px] leading-6 outline-none placeholder:text-muted-foreground/70"
        />
        <div className="flex items-center justify-between gap-2 px-2 pb-2">
          <div className="flex items-center gap-1">
            <PlusMenu groups={ux.plus} />
          </div>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={attach}
              className="size-8 rounded-lg text-muted-foreground hover:text-foreground"
              aria-label="Attach"
            >
              <Paperclip className="size-4" />
            </Button>
            <ComposerModelPicker />
            {!hideWorkspacePicker && <ComposerWorkspacePicker />}
            {value.trim().length === 0 && !isBusy ? (
              <Button
                type="button"
                onClick={startDictation}
                size="icon"
                variant="ghost"
                className="size-8 rounded-lg text-muted-foreground hover:text-foreground"
                aria-label="Voice input"
              >
                <AudioLines className="size-4" />
              </Button>
            ) : isBusy ? (
              <Button
                type="button"
                onClick={onStop}
                size="icon"
                className="size-8 rounded-lg"
                aria-label="Stop"
              >
                <Square className="size-3.5 fill-current" />
              </Button>
            ) : (
              <Button
                type="button"
                onClick={onSubmit}
                size="icon"
                className="size-8 rounded-lg"
                aria-label="Send"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Provider-specific extras — appear below the box, never change its layout */}
      {inlineToggles.length > 0 && (
        <div className="mt-2 flex items-center gap-2 px-1">
          {inlineToggles.map((t) => {
            const on = toggles[t.id] ?? false;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setToggles((s) => ({ ...s, [t.id]: !s[t.id] }))}
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-medium transition-colors",
                  on
                    ? "border-primary/60 bg-primary/15 text-primary"
                    : "border-border/70 text-muted-foreground hover:border-border hover:text-foreground",
                )}
              >
                <t.icon className="size-3.5" />
                {t.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
