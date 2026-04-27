"use client";

import { cn } from "@/lib/utils";
import type { Chip } from "@/lib/ai/provider-ux";

type Props = {
  chips: Chip[];
  onPick: (prompt: string) => void;
  activeLabel: string | null;
  onSetActive: (label: string | null) => void;
};

export function FunctionGrid({ chips, onPick, activeLabel, onSetActive }: Props) {
  if (chips.length === 0) return null;

  return (
    <div className="mt-6 w-full max-w-2xl mx-auto">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {chips.map((c) => {
          const isActive = activeLabel === c.label;
          const hasTasks = (c.tasks?.length ?? 0) > 0;
          return (
            <button
              key={c.label}
              type="button"
              onClick={() => {
                if (isActive) {
                  onSetActive(null);
                } else if (hasTasks) {
                  onSetActive(c.label);
                } else {
                  onPick(c.prompt);
                }
              }}
              className={cn(
                "flex flex-col gap-2.5 rounded-2xl border p-4 text-left transition-colors",
                isActive
                  ? "border-border bg-card text-foreground"
                  : "border-border/50 bg-card/40 text-foreground/85 hover:bg-card/70 hover:border-border/80",
              )}
            >
              {c.icon && (
                <c.icon
                  className={cn(
                    "size-4 transition-colors",
                    isActive ? "text-primary" : "text-muted-foreground",
                  )}
                />
              )}
              <div>
                <div className="text-[13px] font-semibold leading-snug">{c.label}</div>
                {c.description && (
                  <div className="text-[11.5px] text-muted-foreground mt-0.5 leading-snug">
                    {c.description}
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
