"use client";

import { useEffect, useRef, useState } from "react";
import {
  Settings,
  ChevronDown,
  BarChart3,
  ShieldCheck,
  BookOpen,
  Wrench,
  KeyRound,
  Plug,
  ArrowRight,
  Database,
} from "lucide-react";
import { cn } from "@/lib/utils";

type Props = {
  onAllSettings: () => void;
  onPickDatabase: () => void;
};

// Quick-access dropdown that fans out from the gear icon, mirroring
// bolt's settings popover. Most items are placeholders today (we don't
// have analytics/auth/secrets etc.) — they fall through to the full
// settings panel via onAllSettings.

export function SettingsMenu({ onAllSettings, onPickDatabase }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  const pick = (handler: () => void) => () => {
    handler();
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex size-7 items-center justify-center rounded-md transition-colors",
          open
            ? "bg-accent text-foreground"
            : "text-muted-foreground hover:bg-accent hover:text-foreground",
        )}
        title="Settings"
      >
        <Settings className="size-3.5" />
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 z-50 w-64 rounded-lg border border-border/60 bg-popover shadow-xl overflow-hidden">
          <MenuRow icon={<BarChart3 className="size-4" />} label="Analytics" disabled />
          <MenuRow icon={<ShieldCheck className="size-4" />} label="Authentication" disabled />
          <MenuRow icon={<BookOpen className="size-4" />} label="Knowledge" disabled />
          <MenuRow icon={<Wrench className="size-4" />} label="Server Functions" disabled />
          <MenuRow icon={<KeyRound className="size-4" />} label="Secrets" disabled />
          <MenuRow icon={<Plug className="size-4" />} label="Connectors" disabled />

          <div className="border-t border-border/40 my-1" />

          <MenuRow
            icon={<Settings className="size-4" />}
            label="All project settings"
            trailing={<ArrowRight className="size-3.5 opacity-60" />}
            onClick={pick(onAllSettings)}
          />

          <div className="border-t border-border/40 my-1" />
          <div className="px-3 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Integrations
          </div>

          <MenuRow
            icon={
              <span className="flex size-5 items-center justify-center rounded bg-[#635bff] text-white text-[11px] font-bold">
                S
              </span>
            }
            label="Stripe"
            sub="Add payments to your project"
            disabled
          />
          <MenuRow
            icon={<Database className="size-4 text-primary" />}
            label="Workspace Database"
            sub="Manage database settings"
            onClick={pick(onPickDatabase)}
          />
        </div>
      )}
    </div>
  );
}

function MenuRow({
  icon,
  label,
  sub,
  trailing,
  onClick,
  disabled,
}: {
  icon: React.ReactNode;
  label: string;
  sub?: string;
  trailing?: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex w-full items-center gap-2.5 px-3 py-2 text-[13px] transition-colors text-left",
        disabled
          ? "text-muted-foreground/60 cursor-not-allowed"
          : "hover:bg-accent/60 text-foreground",
      )}
    >
      <span className="shrink-0 text-muted-foreground">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="truncate">{label}</div>
        {sub && (
          <div className="text-[10.5px] text-muted-foreground truncate">{sub}</div>
        )}
      </div>
      {trailing}
    </button>
  );
}
