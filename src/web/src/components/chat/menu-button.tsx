"use client";

import { useState } from "react";
import { Check, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { MenuGroup } from "@/lib/ai/provider-ux";
import { cn } from "@/lib/utils";

/**
 * Shared dropdown surface for composer menus (`+` and Tools). Renders a
 * vertical list of groups from the provider UX config. Each item supports
 * three kinds:
 *  - `action`   : click fires a placeholder `coming soon` toast
 *  - `submenu`  : shows a right chevron; click fires the same toast (real
 *                 sub-menus land later)
 *  - `toggle`   : persists a boolean in local state; ticks a checkmark and
 *                 turns the row primary when active
 */
export function MenuButton({
  label,
  groups,
  trigger,
  align = "start",
  contentClassName,
  onAction,
}: {
  label: string;
  groups: MenuGroup[];
  trigger: React.ReactNode;
  align?: "start" | "end";
  contentClassName?: string;
  /** Handle an action/submenu item by id. Return truthy if handled — only
   *  unhandled items fall back to the "not wired yet" toast. */
  onAction?: (id: string) => boolean | void;
}) {
  const initialToggles: Record<string, boolean> = {};
  for (const g of groups) {
    for (const i of g.items) {
      if (i.kind === "toggle") initialToggles[i.id] = !!i.toggled;
    }
  }
  const [toggles, setToggles] = useState(initialToggles);

  const soon = (itemLabel: string) =>
    toast.message(`${itemLabel} — coming soon`, {
      description: "The hook for this action isn't wired yet.",
    });

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 gap-1.5 rounded-lg px-2 text-muted-foreground hover:text-foreground"
            aria-label={label}
          />
        }
      >
        {trigger}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={align}
        side="bottom"
        sideOffset={6}
        className={cn("w-64 p-1", contentClassName)}
      >
        {groups.map((g, gi) => (
          <div key={gi}>
            {gi > 0 && <DropdownMenuSeparator className="my-1" />}
            {(g.label || g.badge) && (
              <div className="flex items-center justify-between gap-2 px-2 pt-1.5 pb-1 text-[11px] text-muted-foreground">
                <span>{g.label}</span>
                {g.badge && (
                  <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium text-primary">
                    {g.badge}
                  </span>
                )}
              </div>
            )}
            {g.items.map((item) => {
              const Icon = item.icon;
              const isToggled =
                item.kind === "toggle" && (toggles[item.id] ?? false);

              return (
                <DropdownMenuItem
                  key={item.id}
                  onClick={(e) => {
                    if (item.kind === "toggle") {
                      e.preventDefault();
                      setToggles((t) => ({ ...t, [item.id]: !t[item.id] }));
                    } else if (!onAction?.(item.id)) {
                      soon(item.label);
                    }
                  }}
                  className={cn(
                    "gap-2.5 py-2 text-[13px]",
                    isToggled && "text-primary",
                  )}
                >
                  <Icon
                    className={cn(
                      "size-4",
                      isToggled ? "text-primary" : "text-muted-foreground",
                    )}
                  />
                  <span className="flex-1">{item.label}</span>
                  {item.badge && !isToggled && (
                    <span className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                      {item.badge}
                    </span>
                  )}
                  {item.kind === "submenu" && (
                    <ChevronRight className="size-3.5 text-muted-foreground/70" />
                  )}
                  {item.kind === "toggle" && isToggled && (
                    <Check className="size-3.5 text-primary" />
                  )}
                </DropdownMenuItem>
              );
            })}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
