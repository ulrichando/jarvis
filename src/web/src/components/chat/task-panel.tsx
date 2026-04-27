"use client";

import { X } from "lucide-react";
import { cn } from "@/lib/utils";
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
    <div
      className={cn(
        "mt-3 w-full max-w-2xl mx-auto",
        "rounded-2xl border border-border bg-card",
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center">
          {Icon && (
            <Icon className="size-4 text-muted-foreground" />
          )}
          <span
            className={cn(
              "text-[13px] font-semibold",
              Icon ? "ml-2" : undefined,
            )}
          >
            {chip.label}
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="size-4" />
        </button>
      </div>

      {/* Divider */}
      <div className="border-t border-border" />

      {/* Task list */}
      <div>
        {chip.tasks.map((task) => (
          <button
            key={task}
            type="button"
            onClick={() => onPick(task)}
            className="w-full text-left px-4 py-2.5 text-[13px] text-foreground/85 hover:bg-accent/40 hover:text-foreground transition-colors"
          >
            {task}
          </button>
        ))}
      </div>
    </div>
  );
}
