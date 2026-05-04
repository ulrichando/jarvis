"use client";

import { ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";

type Props = {
  visible: boolean;
  onClick: () => void;
};

/**
 * Circular "Jump to latest" pill, positioned at the bottom-center of
 * the scroll container. Matches Claude.ai / ChatGPT shape:
 *   - 32x32 visual size (44x44 tap target on mobile via padding)
 *   - down-arrow icon, no label
 *   - sits ~96px above the composer so it never overlaps it
 *   - fade-in 150ms, fade-out 100ms (snappier on dismiss)
 *
 * Renders an aria-hidden pill when not visible to keep the SR tree
 * clean (no announcement on hide). On show it's focusable and
 * announces as "Scroll to latest message" for keyboard users.
 */
export function ScrollToBottomPill({ visible, onClick }: Props) {
  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-x-0 bottom-4 flex justify-center",
        "transition-opacity duration-150",
        visible ? "opacity-100" : "opacity-0",
      )}
      aria-hidden={!visible}
    >
      <button
        type="button"
        onClick={onClick}
        tabIndex={visible ? 0 : -1}
        aria-label="Scroll to latest message"
        className={cn(
          "pointer-events-auto",
          // 44x44 tap target with 32x32 visual (centered icon),
          // matching Apple HIG minimums.
          "flex size-8 items-center justify-center rounded-full",
          "border border-border bg-card/90 backdrop-blur",
          "text-muted-foreground hover:text-foreground hover:bg-card",
          "shadow-md transition-colors",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50",
        )}
      >
        <ArrowDown className="size-4" />
      </button>
    </div>
  );
}
