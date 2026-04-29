"use client";

import { X } from "lucide-react";
import type {
  ColorSwatchesTweak,
  RangeTweak,
  SegmentedTweak,
  TextTweak,
  ToggleTweak,
  Tweak,
} from "@/lib/design/tweaks";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function TweaksPanel({
  tweaks,
  values,
  onChange,
  onClose,
}: {
  tweaks: Tweak[];
  values: Record<string, Tweak["value"]>;
  onChange: (id: string, value: Tweak["value"]) => void;
  onClose: () => void;
}) {
  return (
    <aside
      className="absolute right-3 top-3 z-30 flex w-72 max-h-[calc(100%-1.5rem)] flex-col overflow-hidden rounded-lg border border-border/60 bg-popover shadow-xl backdrop-blur"
      role="region"
      aria-label="Design tweaks"
    >
      <header className="flex items-center justify-between border-b border-border/60 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="size-1.5 rounded-full bg-emerald-400" />
          <span className="text-[12px] font-semibold uppercase tracking-[0.18em] text-foreground/80">
            Tweaks
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Close tweaks"
          onClick={onClose}
        >
          <X className="size-3.5" />
        </Button>
      </header>

      {tweaks.length === 0 ? (
        <div className="px-4 py-6 text-[12px] leading-5 text-muted-foreground">
          This design didn&apos;t declare any tweaks. Ask Jarvis to add some —
          e.g. &ldquo;add an accent color tweak and a density tweak.&rdquo;
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
          {tweaks.map((t) => (
            <TweakRow
              key={t.id}
              tweak={t}
              value={values[t.id] ?? t.value}
              onChange={(v) => onChange(t.id, v)}
            />
          ))}
        </div>
      )}
    </aside>
  );
}

function TweakRow({
  tweak,
  value,
  onChange,
}: {
  tweak: Tweak;
  value: Tweak["value"];
  onChange: (v: Tweak["value"]) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {tweak.label}
      </label>
      {tweak.type === "color-swatches" && (
        <ColorSwatches
          tweak={tweak}
          value={value as string}
          onChange={(v) => onChange(v)}
        />
      )}
      {tweak.type === "range" && (
        <RangeRow
          tweak={tweak}
          value={value as number}
          onChange={(v) => onChange(v)}
        />
      )}
      {tweak.type === "segmented" && (
        <SegmentedRow
          tweak={tweak}
          value={value as string}
          onChange={(v) => onChange(v)}
        />
      )}
      {tweak.type === "toggle" && (
        <ToggleRow
          tweak={tweak}
          value={value as boolean}
          onChange={(v) => onChange(v)}
        />
      )}
      {tweak.type === "text" && (
        <TextRow
          tweak={tweak}
          value={value as string}
          onChange={(v) => onChange(v)}
        />
      )}
    </div>
  );
}

function ColorSwatches({
  tweak,
  value,
  onChange,
}: {
  tweak: ColorSwatchesTweak;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {tweak.options.map((c) => {
        const active = c.toLowerCase() === value.toLowerCase();
        return (
          <button
            key={c}
            type="button"
            onClick={() => onChange(c)}
            aria-label={c}
            aria-pressed={active}
            className={cn(
              "size-7 rounded-md border transition-transform",
              active
                ? "border-foreground scale-110 shadow"
                : "border-border/60 hover:scale-105",
            )}
            style={{ background: c }}
          />
        );
      })}
    </div>
  );
}

function RangeRow({
  tweak,
  value,
  onChange,
}: {
  tweak: RangeTweak;
  value: number;
  onChange: (v: number) => void;
}) {
  const fmt = tweak.suffix
    ? `${value}${tweak.suffix}`
    : value.toFixed(Math.max(0, -Math.log10(tweak.step)));
  return (
    <div className="flex flex-col gap-1">
      <input
        type="range"
        min={tweak.min}
        max={tweak.max}
        step={tweak.step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-primary"
      />
      <div className="flex justify-between text-[10px] tabular-nums text-muted-foreground">
        <span>{tweak.min}</span>
        <span className="font-mono text-foreground/80">{fmt}</span>
        <span>{tweak.max}</span>
      </div>
    </div>
  );
}

function SegmentedRow({
  tweak,
  value,
  onChange,
}: {
  tweak: SegmentedTweak;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex rounded-md border border-border/60 bg-muted/30 p-0.5">
      {tweak.options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            aria-pressed={active}
            className={cn(
              "flex-1 rounded px-2 py-1 text-[11px] font-medium uppercase tracking-wide transition-colors",
              active
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function ToggleRow({
  tweak,
  value,
  onChange,
}: {
  tweak: ToggleTweak;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      role="switch"
      aria-checked={value}
      aria-label={tweak.label}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
        value ? "bg-primary" : "bg-muted",
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-background shadow transition-transform",
          value ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

function TextRow({
  tweak,
  value,
  onChange,
}: {
  tweak: TextTweak;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={tweak.placeholder}
      maxLength={tweak.maxLength ?? 80}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-md border border-border/60 bg-background px-2 py-1 text-[12px] focus:border-foreground/40 focus:outline-none"
    />
  );
}
