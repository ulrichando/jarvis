"use client";

import { cn } from "@/lib/utils";
import type { Chip } from "@/lib/ai/provider-ux";

/**
 * Horizontal pill row of function shortcuts shown on the homepage empty state.
 *
 * Controlled component — parent owns `activeLabel`. Clicking a chip with tasks
 * calls `onSetActive(label)` and the parent renders a `<TaskPanel>` below.
 * Clicking a chip with no tasks calls `onPick(prompt)` directly.
 */
export function FunctionGrid({
  chips,
  onPick,
  activeLabel,
  onSetActive,
}: {
  chips: Chip[];
  onPick: (prompt: string) => void;
  activeLabel: string | null;
  onSetActive: (label: string | null) => void;
}) {
  if (chips.length === 0) return null;

  return (
    <div className="mt-4 w-full max-w-3xl mx-auto">
      <div className="flex flex-wrap justify-center gap-2">
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
                "flex items-center gap-2 rounded-full border px-4 py-2 text-[13px] transition-colors",
                isActive
                  ? "border-border bg-card text-foreground"
                  : "border-border/50 bg-card/30 text-foreground/75 hover:bg-card/60 hover:border-border/80 hover:text-foreground",
              )}
            >
              {c.icon && (
                <c.icon
                  className={cn(
                    "size-3.5 shrink-0 transition-colors",
                    isActive ? "text-primary" : "text-muted-foreground",
                  )}
                />
              )}
              <span>{c.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
