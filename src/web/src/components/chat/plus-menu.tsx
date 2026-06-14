"use client";

import { Plus, type LucideIcon } from "lucide-react";
import type { MenuGroup } from "@/lib/ai/provider-ux";
import { MenuButton } from "./menu-button";

export function PlusMenu({
  groups,
  onAction,
}: {
  groups: MenuGroup[];
  onAction?: (id: string) => boolean | void;
}) {
  return (
    <MenuButton
      label="Add content or tool"
      groups={groups}
      trigger={<Plus className="size-4" />}
      align="start"
      onAction={onAction}
    />
  );
}

export function SecondaryMenu({
  label,
  icon: Icon,
  groups,
}: {
  label: string;
  icon: LucideIcon;
  groups: MenuGroup[];
}) {
  return (
    <MenuButton
      label={label}
      groups={groups}
      align="start"
      trigger={
        <>
          <Icon className="size-4" />
          <span className="text-[13px] text-foreground/85">{label}</span>
        </>
      }
    />
  );
}
