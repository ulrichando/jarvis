"use client";

import { ArrowUp, AudioLines, Paperclip, Square, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ComposerModelPicker } from "./model-picker";
import { ComposerWorkspacePicker } from "./workspace-picker";
import { PlusMenu, SecondaryMenu } from "./plus-menu";
import type { Provider } from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";

export type AttachedImage = {
  // Stable id so we can remove a specific one without index drift.
  id: string;
  dataUrl: string; // data:image/...;base64,xxx
  name: string;
};

type ComposerProps = {
  value: string;
  onChange: (v: string) => void;
  onSubmit: (opts?: { images?: AttachedImage[] }) => void;
  onStop?: () => void;
  status: "ready" | "submitted" | "streaming" | "error";
  provider: Provider;
  // When the chat is embedded inside a workbench page the workspace
  // is already pinned by the page itself — showing the picker would
  // be confusing because changing it would point chat at a different
  // workspace than the editor next to it.
  hideWorkspacePicker?: boolean;
  // Override the textarea placeholder for context-specific surfaces
  // (e.g. /design uses "Describe what you want to create…").
  placeholder?: string;
  // Force a model-agnostic composer: same shell, no provider pre-block, no
  // provider inline toggles, regardless of which model is selected. Used in
  // surfaces like /design where the composer is a means to an end and should
  // not flicker between Anthropic / Groq / DeepSeek visual variants.
  unifiedUX?: boolean;
};

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  status,
  provider,
  hideWorkspacePicker = false,
  placeholder,
  unifiedUX = false,
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
      e.preventDefault();
      // Allow submit when there's text OR an attached image (image-only
      // prompts like "implement this" + a screenshot are valid).
      const hasContent = value.trim().length > 0 || images.length > 0;
      if (!isBusy && hasContent) {
        const carry = images;
        setImages([]);
        onSubmit({ images: carry });
      }
    }
  };

  const submitClick = () => {
    if (isBusy) return;
    if (value.trim().length === 0 && images.length === 0) return;
    const carry = images;
    setImages([]);
    onSubmit({ images: carry });
  };

  const startDictation = () =>
    toast.message("Voice input — coming soon", {
      description: "Whisper streaming will be wired next.",
    });

  // Image attachments (drop-screenshot-get-code workflow). Stored as
  // data URLs so we can preview thumbnails and ship them inline as
  // image parts in the user message — no upload round-trip needed.
  const [images, setImages] = useState<AttachedImage[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const fileToDataUrl = (file: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onerror = () => reject(r.error ?? new Error("read failed"));
      r.onload = () => resolve(String(r.result));
      r.readAsDataURL(file);
    });

  const ingestFiles = useCallback(async (files: FileList | File[]) => {
    const arr = Array.from(files).filter((f) =>
      f.type.startsWith("image/"),
    );
    if (arr.length === 0) return;
    // Cap on file size so a 50MB photo doesn't blow the request body.
    const TOO_BIG = 8 * 1024 * 1024;
    const ok = arr.filter((f) => {
      if (f.size > TOO_BIG) {
        toast.error(
          `Image ${f.name} is ${(f.size / 1024 / 1024).toFixed(1)}MB — over the 8MB limit.`,
        );
        return false;
      }
      return true;
    });
    if (ok.length === 0) return;
    try {
      const parsed = await Promise.all(
        ok.map(async (f) => ({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          dataUrl: await fileToDataUrl(f),
          name: f.name,
        })),
      );
      setImages((prev) => [...prev, ...parsed]);
    } catch (e) {
      toast.error(`Couldn't read image: ${(e as Error).message}`);
    }
  }, []);

  const attach = () => fileInputRef.current?.click();

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? []).filter((f) =>
      f.type.startsWith("image/"),
    );
    if (files.length > 0) {
      e.preventDefault();
      void ingestFiles(files);
    }
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) void ingestFiles(files);
  };

  const removeImage = (id: string) =>
    setImages((prev) => prev.filter((p) => p.id !== id));

  const inlineToggles = providerUX.inlineToggles ?? [];

  return (
    <div
      // Match Thread's cap (max-w-3xl, 768px). Industry-standard fixed
      // width — Claude / ChatGPT / Perplexity all use this cap with no
      // breakpoint scaling. See thread.tsx.
      className="mx-auto w-full max-w-3xl px-4"
      // iOS Safari + Android Chrome with display-mode standalone need
      // env(safe-area-inset-bottom) to clear the home-indicator gesture
      // bar. Fallback to 1rem on browsers that don't support env().
      // Doing this inline (not via Tailwind arbitrary value) so the
      // computed value is correct in all build modes — Tailwind's
      // arbitrary syntax doesn't always pass env() through cleanly.
      style={{
        paddingBottom: "max(1rem, env(safe-area-inset-bottom))",
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) void ingestFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (e.dataTransfer?.types?.includes("Files")) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={cn(
          "relative flex flex-col rounded-2xl border border-transparent bg-card/70 transition-all",
          "shadow-[0_1px_0_0_oklch(1_0_0/3%)_inset]",
          "focus-within:border-border focus-within:bg-card focus-within:shadow-[0_1px_0_0_oklch(1_0_0/5%)_inset]",
          dragOver && "border-primary/60 bg-primary/5",
        )}
      >
        {images.length > 0 && (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {images.map((img) => (
              <div
                key={img.id}
                className="relative group rounded-md border border-border/50 bg-card overflow-hidden"
                title={img.name}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={img.dataUrl}
                  alt={img.name}
                  className="block size-16 object-cover"
                />
                <button
                  type="button"
                  onClick={() => removeImage(img.id)}
                  className="absolute top-0.5 right-0.5 flex size-4 items-center justify-center rounded-full bg-background/80 text-foreground opacity-0 group-hover:opacity-100 transition-opacity"
                  aria-label="Remove image"
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={ref}
          data-jarvis-composer
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKey}
          onPaste={onPaste}
          rows={1}
          placeholder={placeholder ?? ux.placeholder}
          // 16px base prevents iOS Safari from auto-zooming the page
          // when the textarea takes focus — anything under 16px triggers
          // it. Visual cost vs 15px is negligible; UX win is large.
          className="resize-none bg-transparent px-5 pt-5 pb-3 text-[16px] leading-7 outline-none placeholder:text-muted-foreground/70 min-h-18"
        />
        {/* Bottom toolbar. Default flex behavior shrinks every child to
            its content; with no `shrink-0` on the icon buttons, the
            voice/send/paperclip targets collapse to 0 width when the
            container is narrow (e.g. embedded chat panel ~380px) — they
            *appear* to "disappear" but are still occupying their slot.
            Fix: pin the icon buttons with `shrink-0` so they always
            keep their 32×32 footprint, and let the model/workspace
            pickers truncate via `min-w-0` instead. */}
        <div className="flex items-center justify-between gap-2 px-2 pb-2">
          <div className="flex shrink-0 items-center gap-1">
            <PlusMenu groups={ux.plus} />
          </div>
          <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={attach}
              className="size-8 shrink-0 rounded-lg text-muted-foreground hover:text-foreground"
              aria-label="Attach image"
              title="Attach image (or drag/drop, or paste)"
            >
              <Paperclip className="size-4" />
            </Button>
            <div className="min-w-0 shrink">
              <ComposerModelPicker />
            </div>
            {!hideWorkspacePicker && (
              <div className="min-w-0 shrink">
                <ComposerWorkspacePicker />
              </div>
            )}
            {value.trim().length === 0 && images.length === 0 && !isBusy ? (
              <Button
                type="button"
                onClick={startDictation}
                size="icon"
                variant="ghost"
                className="size-8 shrink-0 rounded-lg text-muted-foreground hover:text-foreground"
                aria-label="Voice input"
              >
                <AudioLines className="size-4" />
              </Button>
            ) : isBusy ? (
              // Bright cyan stop button with a soft ping ring so it's
              // unmistakable that the model is still running. Solid fill
              // + ring contrast against the composer's dark surface.
              <button
                type="button"
                onClick={onStop}
                aria-label="Stop"
                title="Stop generating"
                className="relative inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-cyan-400 text-black shadow-[0_0_0_1px_rgba(34,211,238,0.6),0_0_12px_rgba(34,211,238,0.45)] transition-transform hover:scale-105 hover:bg-cyan-300 active:scale-95"
              >
                <span
                  className="pointer-events-none absolute inset-0 rounded-lg ring-2 ring-cyan-400/60 animate-ping"
                  aria-hidden
                />
                <Square className="relative size-3.5 fill-current" />
              </button>
            ) : (
              <Button
                type="button"
                onClick={submitClick}
                size="icon"
                className="size-8 shrink-0 rounded-lg"
                aria-label="Send"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Provider-specific extras — appear below the box, never change its layout */}
      {!unifiedUX && inlineToggles.length > 0 && (
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
