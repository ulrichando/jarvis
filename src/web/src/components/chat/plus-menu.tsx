"use client";

import { Plus, type LucideIcon } from "lucide-react";
import type { MenuGroup } from "@/lib/ai/provider-ux";
import { MenuButton } from "./menu-button";

export function PlusMenu({ groups }: { groups: MenuGroup[] }) {
  return (
    <MenuButton
      label="Add content or tool"
      groups={groups}
      trigger={<Plus className="size-4" />}
      align="start"
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
