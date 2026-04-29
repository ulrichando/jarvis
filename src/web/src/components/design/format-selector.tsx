"use client";

import { FORMATS, FORMAT_LABEL, type Format } from "@/lib/design/format";
import { cn } from "@/lib/utils";

export function FormatSelector({
  value,
  onChange,
}: {
  value: Format;
  onChange: (next: Format) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Design format"
      className="flex items-center gap-1 border-b border-border/50 px-2 py-1.5"
    >
      {FORMATS.map((f) => {
        const active = f === value;
        return (
          <button
            key={f}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(f)}
            className={cn(
              "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
              active
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {FORMAT_LABEL[f]}
          </button>
        );
      })}
    </div>
  );
}
