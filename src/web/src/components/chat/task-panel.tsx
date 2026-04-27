"use client";

import { X } from "lucide-react";
import type { Chip } from "@/lib/ai/provider-ux";

export function TaskPanel({
  chip,
  onPick,
  onClose,
}: {
  chip: Chip;
  onPick: (prompt: string) => void;
  onClose: () => void;
}) {
  if (!chip.tasks?.length) return null;

  const Icon = chip.icon;

  return (
    <div className="mt-3 w-full max-w-2xl mx-auto rounded-2xl border border-border bg-card">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          {Icon && <Icon className="size-4 text-muted-foreground" />}
          <span className="text-[13px] font-semibold">{chip.label}</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="border-t border-border" />

      <div>
        {chip.tasks.map((task) => (
          <button
            key={task}
            type="button"
            onClick={() => onPick(task)}
            className="w-full text-left px-4 py-2.5 text-[13px] text-foreground/85 hover:bg-accent/40 hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
          >
            {task}
          </button>
        ))}
      </div>
    </div>
  );
}
