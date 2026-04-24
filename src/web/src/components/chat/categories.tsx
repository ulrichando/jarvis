"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import type { Chip } from "@/lib/ai/provider-ux";

export function Categories({
  chips,
  onPick,
}: {
  chips: Chip[];
  onPick: (prompt: string) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);

  if (chips.length === 0) return null;

  return (
    <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
      {chips.map((c, i) => {
        const isHover = hovered === c.label;
        const isFirst = i === 0;
        return (
          <button
            key={c.label}
            type="button"
            onMouseEnter={() => setHovered(c.label)}
            onMouseLeave={() => setHovered(null)}
            onClick={() => onPick(c.prompt)}
            className={cn(
              "flex items-center gap-1.5 rounded-full border border-border/70 bg-card/40 px-3 py-1.5 text-[12.5px] font-medium transition-colors",
              isHover
                ? "border-border bg-card text-foreground"
                : "text-foreground/85",
            )}
          >
            {c.icon && (
              <c.icon
                className={cn(
                  "size-3.5 transition-colors",
                  isFirst
                    ? "text-primary"
                    : isHover
                      ? "text-foreground"
                      : "text-muted-foreground",
                )}
              />
            )}
            {c.label}
          </button>
        );
      })}
    </div>
  );
}
