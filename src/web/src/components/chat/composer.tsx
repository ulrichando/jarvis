"use client";

import { ArrowUp, AudioLines, Paperclip, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ComposerModelPicker } from "./model-picker";
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
};

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  status,
  provider,
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const isBusy = status === "streaming" || status === "submitted";
  const ux = getProviderUX(provider);

  const initialToggles: Record<string, boolean> = {};
  for (const t of ux.inlineToggles ?? []) {
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

  const hasInlineToggles =
    ux.inlineToggles && ux.inlineToggles.length > 0;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      {ux.renderPreComposer && ux.renderPreComposer()}

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
            {hasInlineToggles ? (
              <>
                {ux.inlineToggles!.map((t) => {
                  const on = toggles[t.id] ?? false;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() =>
                        setToggles((s) => ({ ...s, [t.id]: !s[t.id] }))
                      }
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
              </>
            ) : (
              <>
                <PlusMenu groups={ux.plus} />
                {ux.secondary && (
                  <SecondaryMenu
                    label={ux.secondary.label}
                    icon={ux.secondary.icon}
                    groups={ux.secondary.groups}
                  />
                )}
              </>
            )}
          </div>
          <div className="flex items-center gap-1">
            {hasInlineToggles && (
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
            )}
            {!ux.hideComposerModelPicker && <ComposerModelPicker />}
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
    </div>
  );
}
